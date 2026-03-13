#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
频道旁路快筛工具 — 低成本评估候选频道是否值得长期追踪。

流程：
  候选频道 → YouTube Data API 取近 5 期视频
  → 字幕探测（复用 analyzer.subtitle）
  → LLM 轻量打分（Cerebras gpt-oss-120b，极速推理）
  → 频道级聚合评分 → 自动分流（合格/观察/淘汰）

单频道筛选成本极低（Cerebras 免费额度），耗时约 30-60 秒。

用法：
  # 单频道（频道 URL / 视频链接 / channel_id）
  python tools/channel_screen.py "https://www.youtube.com/@SomeChannel"
  python tools/channel_screen.py "https://www.youtube.com/watch?v=xxxxx"
  python tools/channel_screen.py UCxxxxxxxxxxxxxxxxxxxxxxxx

  # 批量模式（每行一个候选）
  python tools/channel_screen.py --batch candidates.txt

  # 只打分不写入
  python tools/channel_screen.py --dry-run "https://www.youtube.com/@SomeChannel"

依赖：
  pip install requests python-dotenv ruamel.yaml openai
  环境变量：YOUTUBE_DATA_API_KEY, CEREBRAS_API_KEY
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from ruamel.yaml import YAML

# ---------------------------------------------------------------------------
# 路径设置 & 环境加载
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

load_dotenv(PROJECT_DIR / ".env")

from ytbnotes.analyzer.subtitle import load_subtitle_transcript, probe_subtitle  # noqa: E402
from ytbnotes.analyzer.config import YTDLP_PATH  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("channel_screen")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# --- Cerebras LLM 配置 ---
CEREBRAS_API_KEY  = os.getenv("CEREBRAS_API_KEY", "").strip()
CEREBRAS_BASE_URL = os.getenv("SCREEN_LLM_BASE_URL", os.getenv("OPINION_BASE_URL", "https://api.cerebras.ai/v1")).strip()
CEREBRAS_MODEL    = os.getenv("SCREEN_LLM_MODEL", os.getenv("OPINION_MODEL_NAME", "gpt-oss-120b")).strip()

YT_API_BASE = "https://www.googleapis.com/youtube/v3"
VIDEOS_PER_CHANNEL = 5
SUBTITLE_HIT_THRESHOLD = 2        # 至少 N 个视频有字幕才继续
PASS_SCORE_THRESHOLD = 50         # 频道总分 >= 此值 → 合格
WATCHLIST_SCORE_THRESHOLD = 30    # >= 此值但 < PASS → 观察

CHANNELS_YAML = PROJECT_DIR / "channels.yaml"
REJECTED_YAML = PROJECT_DIR / "brain" / "candidates_rejected.yaml"
WATCHLIST_YAML = PROJECT_DIR / "brain" / "candidates_watchlist.yaml"

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class VideoScore:
    video_id: str
    title: str
    has_subtitle: bool
    subtitle_source: str | None = None
    opinion_density: int = 0        # 0-5，具体个股观点数
    price_specificity: bool = False # 是否有具体价位
    position_signal: bool = False   # 是否透露持仓
    update_pattern: bool = False    # 是否暗示定期更新
    noise_ratio: str = "high"       # high / medium / low
    raw_score: float = 0.0
    error: str | None = None


@dataclass
class ChannelReport:
    channel_id: str
    channel_name: str
    host: str
    video_count: int = 0
    subtitle_hit_count: int = 0
    videos: list[VideoScore] = field(default_factory=list)
    aggregate_score: float = 0.0
    verdict: str = "reject"         # pass / watchlist / reject
    reject_reason: str | None = None


# ---------------------------------------------------------------------------
# YouTube Data API 工具
# ---------------------------------------------------------------------------

def _yt_api_key() -> str:
    key = os.getenv("YOUTUBE_DATA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("YOUTUBE_DATA_API_KEY 未设置")
    return key


def resolve_channel_id(raw_input: str) -> tuple[str, str]:
    """
    从各种输入格式解析出 (channel_name, channel_id)。
    支持：channel_id、频道 URL、视频 URL、视频 ID。
    """
    raw = raw_input.strip()

    # 直接是 UC 开头的 channel_id
    if re.match(r"^UC[\w-]{22}$", raw):
        name = _fetch_channel_name(raw)
        return name, raw

    # 频道 URL 中包含 channel_id
    m = re.search(r"channel/(UC[\w-]{22})", raw)
    if m:
        cid = m.group(1)
        name = _fetch_channel_name(cid)
        return name, cid

    # @handle 或 /c/ 或 /user/ 格式 → 用 yt-dlp 解析
    if "youtube.com/" in raw or "youtu.be/" in raw:
        return _resolve_via_ytdlp(raw)

    # 兜底：当作视频 ID 试
    if len(raw) >= 11 and " " not in raw:
        url = f"https://www.youtube.com/watch?v={raw}"
        return _resolve_via_ytdlp(url)

    raise ValueError(f"无法解析输入: {raw}")


def _fetch_channel_name(channel_id: str) -> str:
    """通过 YouTube Data API 获取频道名称。"""
    resp = requests.get(
        f"{YT_API_BASE}/channels",
        params={"part": "snippet", "id": channel_id, "key": _yt_api_key()},
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if items:
        return items[0]["snippet"]["title"]
    return channel_id


def _resolve_via_ytdlp(url: str) -> tuple[str, str]:
    """用 yt-dlp 从任意 YouTube URL 解析频道信息。"""
    cmd = [
        YTDLP_PATH, "--print", "%(channel)s\t%(channel_id)s",
        "--no-warnings", "--skip-download", "--playlist-items", "1",
        url,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError(f"yt-dlp 解析失败: {(p.stderr or '').strip()[-200:]}")
    parts = p.stdout.strip().split("\t", 1)
    if len(parts) != 2 or not parts[1].startswith("UC"):
        raise RuntimeError(f"yt-dlp 输出异常: {p.stdout.strip()}")
    return parts[0].strip(), parts[1].strip()


def fetch_recent_videos(channel_id: str, max_results: int = VIDEOS_PER_CHANNEL) -> list[dict]:
    """通过 YouTube Data API 获取频道最近 N 个视频。"""
    api_key = _yt_api_key()

    # 先获取 uploads playlist id
    resp = requests.get(
        f"{YT_API_BASE}/channels",
        params={
            "part": "contentDetails",
            "id": channel_id,
            "key": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return []
    uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # 拉取最近视频
    resp = requests.get(
        f"{YT_API_BASE}/playlistItems",
        params={
            "part": "snippet",
            "playlistId": uploads_id,
            "maxResults": max_results,
            "key": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    videos = []
    for item in resp.json().get("items", []):
        snippet = item.get("snippet", {})
        vid = snippet.get("resourceId", {}).get("videoId")
        if vid:
            videos.append({
                "video_id": vid,
                "title": snippet.get("title", ""),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "published_at": snippet.get("publishedAt", ""),
            })
    return videos


# ---------------------------------------------------------------------------
# LLM 快筛打分
# ---------------------------------------------------------------------------

_SCREEN_PROMPT_PREFIX = """你是一个财经内容评估助手。给定一段 YouTube 财经视频的字幕文本，快速判断以下维度。
只输出 JSON，不要解释。

判断维度：
1. opinion_density: 视频中提到了多少个具体的个股观点（含买入/卖出/目标价/支撑位/阻力位等）？给出 0-5 的整数。
2. price_specificity: 是否给出了具体的价格点位（如目标价 $XX、支撑位 $XX）？true/false
3. position_signal: 发言人是否透露了自己的真实持仓或操作计划（如"我买了XX"、"我的仓位"）？true/false
4. update_pattern: 内容是否暗示这是定期更新的节目（如"今天的行情回顾"、"本周分析"）？true/false
5. noise_ratio: 情绪化表达（喊单、标题党、极端用词）与理性分析的比例。high（情绪为主）/ medium（混合）/ low（理性为主）

输出格式（严格 JSON）：
{"opinion_density": 3, "price_specificity": true, "position_signal": false, "update_pattern": true, "noise_ratio": "low"}

以下是字幕文本（可能较长，只需基于内容判断）：

---
"""


def llm_score_video(transcript: str) -> dict:
    """调用 Cerebras gpt-oss-120b 对单个视频字幕做快筛打分。"""
    if not CEREBRAS_API_KEY:
        raise RuntimeError("CEREBRAS_API_KEY 未设置")

    from openai import OpenAI
    client = OpenAI(api_key=CEREBRAS_API_KEY, base_url=CEREBRAS_BASE_URL)

    # 截断过长文本（保留前 6000 字符，约 3k tokens）
    text = transcript[:6000] if len(transcript) > 6000 else transcript
    prompt = _SCREEN_PROMPT_PREFIX + text + "\n---"

    resp = client.chat.completions.create(
        model=CEREBRAS_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=8192,
    )
    raw = (resp.choices[0].message.content or "").strip()

    # 容忍 markdown code block 包裹
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    return json.loads(raw)


def compute_video_score(scores: dict) -> float:
    """将 LLM 打分转化为 0-100 的单视频分数。"""
    s = 0.0
    # opinion_density: 0-5 → 0-40 分
    s += min(scores.get("opinion_density", 0), 5) * 8
    # price_specificity: 20 分
    if scores.get("price_specificity"):
        s += 20
    # position_signal: 15 分（加分项）
    if scores.get("position_signal"):
        s += 15
    # update_pattern: 10 分
    if scores.get("update_pattern"):
        s += 10
    # noise_ratio: low=15, medium=5, high=0
    nr = scores.get("noise_ratio", "high")
    if nr == "low":
        s += 15
    elif nr == "medium":
        s += 5
    return s


# ---------------------------------------------------------------------------
# 频道筛选主逻辑
# ---------------------------------------------------------------------------

def screen_channel(raw_input: str) -> ChannelReport:
    """对单个候选频道执行完整快筛流程。"""
    report = ChannelReport(channel_id="", channel_name="", host="")

    # Step 1: 解析频道
    try:
        name, cid = resolve_channel_id(raw_input)
        report.channel_id = cid
        report.channel_name = name
        report.host = name  # 默认 host = 频道名，后续可人工修改
        log.info(f"频道: {name} ({cid})")
    except Exception as e:
        report.reject_reason = f"resolve_failed: {e}"
        log.error(f"解析失败: {raw_input} → {e}")
        return report

    # Step 2: 拉取最近视频
    try:
        videos = fetch_recent_videos(cid, VIDEOS_PER_CHANNEL)
        report.video_count = len(videos)
        log.info(f"  获取到 {len(videos)} 个视频")
    except Exception as e:
        report.reject_reason = f"fetch_videos_failed: {e}"
        log.error(f"  获取视频失败: {e}")
        return report

    if not videos:
        report.reject_reason = "no_videos"
        return report

    # Step 3: 逐视频探测字幕 + 打分
    for v in videos:
        vs = VideoScore(video_id=v["video_id"], title=v["title"], has_subtitle=False)
        url = v["url"]
        log.info(f"  [{v['video_id']}] {v['title'][:50]}...")

        # 字幕探测
        try:
            sub_result = load_subtitle_transcript(url)
            if sub_result.get("ok") and sub_result.get("transcript"):
                vs.has_subtitle = True
                vs.subtitle_source = sub_result.get("source", "unknown")
                report.subtitle_hit_count += 1
                log.info(f"    字幕: ✓ ({vs.subtitle_source})")

                # LLM 快筛
                try:
                    scores = llm_score_video(sub_result["transcript"])
                    vs.opinion_density = scores.get("opinion_density", 0)
                    vs.price_specificity = scores.get("price_specificity", False)
                    vs.position_signal = scores.get("position_signal", False)
                    vs.update_pattern = scores.get("update_pattern", False)
                    vs.noise_ratio = scores.get("noise_ratio", "high")
                    vs.raw_score = compute_video_score(scores)
                    log.info(f"    打分: {vs.raw_score:.0f} (观点={vs.opinion_density}, 价位={vs.price_specificity}, 噪声={vs.noise_ratio})")
                except Exception as e:
                    vs.error = f"llm_score_failed: {e}"
                    log.warning(f"    LLM 打分失败: {e}")
            else:
                reason = sub_result.get("error", "unknown")
                log.info(f"    字幕: ✗ ({reason})")
        except Exception as e:
            vs.error = f"subtitle_probe_failed: {e}"
            log.warning(f"    字幕探测异常: {e}")

        report.videos.append(vs)
        time.sleep(0.3)  # 礼貌间隔

    # Step 4: 聚合评分
    if report.subtitle_hit_count < SUBTITLE_HIT_THRESHOLD:
        report.reject_reason = f"subtitle_coverage_too_low ({report.subtitle_hit_count}/{report.video_count})"
        report.verdict = "reject"
        return report

    scored_videos = [v for v in report.videos if v.has_subtitle and v.error is None]
    if scored_videos:
        report.aggregate_score = sum(v.raw_score for v in scored_videos) / len(scored_videos)
    else:
        report.aggregate_score = 0

    if report.aggregate_score >= PASS_SCORE_THRESHOLD:
        report.verdict = "pass"
    elif report.aggregate_score >= WATCHLIST_SCORE_THRESHOLD:
        report.verdict = "watchlist"
    else:
        report.verdict = "reject"
        report.reject_reason = f"low_score ({report.aggregate_score:.1f})"

    return report


# ---------------------------------------------------------------------------
# YAML 读写（复用 channel_add.py 逻辑）
# ---------------------------------------------------------------------------

def load_yaml_list(yaml_path: Path) -> tuple[YAML, list]:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=2, offset=0)
    if not yaml_path.exists():
        return yaml, []
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)
    if data is None:
        data = []
    elif not isinstance(data, list):
        raise ValueError(f"{yaml_path} 不是 YAML 列表")
    return yaml, data


def save_yaml_list(yaml: YAML, data: list, yaml_path: Path) -> None:
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)


def _existing_channel_ids(data: list) -> set[str]:
    """从 YAML 列表中提取所有已知的 channel_id。"""
    ids = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", ""))
        m = re.search(r"channel_id=(UC[\w-]{22})", url)
        if m:
            ids.add(m.group(1))
    return ids


def append_to_channels_yaml(report: ChannelReport) -> bool:
    """将合格频道追加到 channels.yaml，返回是否实际写入。"""
    yaml, data = load_yaml_list(CHANNELS_YAML)
    existing_ids = _existing_channel_ids(data)
    if report.channel_id in existing_ids:
        log.info(f"  频道已在 channels.yaml 中: {report.channel_name}")
        return False
    entry = {
        "name": report.channel_name,
        "url": f"https://www.youtube.com/feeds/videos.xml?channel_id={report.channel_id}",
        "host": report.host,
    }
    data.append(entry)
    save_yaml_list(yaml, data, CHANNELS_YAML)
    log.info(f"  ✓ 已追加到 channels.yaml: {report.channel_name}")
    return True


def append_to_candidate_yaml(report: ChannelReport, yaml_path: Path) -> None:
    """将候选记录追加到 rejected/watchlist YAML。"""
    yaml, data = load_yaml_list(yaml_path)
    existing_ids = _existing_channel_ids(data)
    if report.channel_id in existing_ids:
        return
    entry = {
        "name": report.channel_name,
        "channel_id": report.channel_id,
        "score": round(report.aggregate_score, 1),
        "subtitle_coverage": f"{report.subtitle_hit_count}/{report.video_count}",
        "reason": report.reject_reason or report.verdict,
    }
    data.append(entry)
    save_yaml_list(yaml, data, yaml_path)


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_report(report: ChannelReport) -> None:
    """打印频道快筛报告到终端。"""
    verdict_emoji = {"pass": "✓ 合格", "watchlist": "~ 观察", "reject": "✗ 淘汰"}
    print(f"\n{'='*60}")
    print(f"频道: {report.channel_name} ({report.channel_id})")
    print(f"结论: {verdict_emoji.get(report.verdict, report.verdict)}")
    print(f"综合评分: {report.aggregate_score:.1f}/100")
    print(f"字幕覆盖: {report.subtitle_hit_count}/{report.video_count}")
    if report.reject_reason:
        print(f"淘汰原因: {report.reject_reason}")
    print(f"{'-'*60}")
    for v in report.videos:
        sub = f"✓ {v.subtitle_source}" if v.has_subtitle else "✗"
        score_str = f"{v.raw_score:.0f}" if v.has_subtitle and not v.error else "-"
        detail = ""
        if v.has_subtitle and not v.error:
            parts = []
            parts.append(f"观点={v.opinion_density}")
            if v.price_specificity:
                parts.append("有价位")
            if v.position_signal:
                parts.append("有持仓")
            parts.append(f"噪声={v.noise_ratio}")
            detail = f" ({', '.join(parts)})"
        print(f"  [{v.video_id}] 字幕:{sub} 分:{score_str}{detail}")
        print(f"    {v.title[:70]}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="频道旁路快筛 — 低成本评估候选频道价值",
    )
    parser.add_argument(
        "candidate",
        nargs="?",
        help="候选频道（频道URL / 视频链接 / channel_id）",
    )
    parser.add_argument(
        "--batch",
        metavar="FILE",
        help="批量模式：从文件读取候选列表（每行一个）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打分不写入 YAML 文件",
    )
    parser.add_argument(
        "--pass-threshold",
        type=float,
        default=PASS_SCORE_THRESHOLD,
        help=f"合格分数阈值（默认 {PASS_SCORE_THRESHOLD}）",
    )
    parser.add_argument(
        "--videos",
        type=int,
        default=VIDEOS_PER_CHANNEL,
        help=f"每频道抽查视频数（默认 {VIDEOS_PER_CHANNEL}）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.candidate and not args.batch:
        print("错误：请提供候选频道或 --batch 文件。", file=sys.stderr)
        print("用法: python tools/channel_screen.py <频道URL/视频链接/channel_id>", file=sys.stderr)
        return 1

    global VIDEOS_PER_CHANNEL, PASS_SCORE_THRESHOLD
    VIDEOS_PER_CHANNEL = args.videos
    PASS_SCORE_THRESHOLD = args.pass_threshold

    # 构建候选列表
    candidates: list[str] = []
    if args.batch:
        batch_path = Path(args.batch)
        if not batch_path.exists():
            print(f"错误：文件不存在: {args.batch}", file=sys.stderr)
            return 1
        candidates = [
            line.strip()
            for line in batch_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    elif args.candidate:
        candidates = [args.candidate]

    if not candidates:
        print("没有候选频道需要筛选。", file=sys.stderr)
        return 0

    # 检查已有频道，避免重复筛选
    _, existing_data = load_yaml_list(CHANNELS_YAML)
    existing_ids = _existing_channel_ids(existing_data)

    results = {"pass": 0, "watchlist": 0, "reject": 0, "skip": 0}

    for i, candidate in enumerate(candidates, 1):
        log.info(f"\n[{i}/{len(candidates)}] 筛选: {candidate}")

        report = screen_channel(candidate)
        print_report(report)

        # 已在追踪中
        if report.channel_id and report.channel_id in existing_ids:
            log.info(f"  跳过: 已在 channels.yaml 中")
            results["skip"] += 1
            continue

        results[report.verdict] += 1

        if args.dry_run:
            continue

        # 写入对应 YAML
        if report.verdict == "pass":
            append_to_channels_yaml(report)
        elif report.verdict == "watchlist":
            append_to_candidate_yaml(report, WATCHLIST_YAML)
        else:
            if report.channel_id:  # 只记录成功解析了频道的
                append_to_candidate_yaml(report, REJECTED_YAML)

    # 汇总
    print(f"\n筛选完成: {len(candidates)} 个候选")
    print(f"  合格: {results['pass']}  观察: {results['watchlist']}  淘汰: {results['reject']}  跳过: {results['skip']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
