#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
频道候选发现 — 从多个渠道自动搜集候选财经 YouTube 频道。

发现渠道：
  1. YouTube Data API 搜索（关键词 + 频道类型过滤）
  2. 种子频道关系链（现有频道的 featured channels / 视频描述 @mention）

输出：brain/candidates_discovered.yaml（增量追加，自动去重）

用法：
  python tools/discover_channels.py                    # 全部渠道
  python tools/discover_channels.py --source search    # 仅搜索
  python tools/discover_channels.py --source seed      # 仅种子关系链
  python tools/discover_channels.py --queries "stock picks,price target analysis"  # 自定义搜索词

依赖：
  pip install requests python-dotenv ruamel.yaml
  环境变量：YOUTUBE_DATA_API_KEY
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from ruamel.yaml import YAML

# ---------------------------------------------------------------------------
# 路径 & 环境
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("discover")

YT_API_BASE = "https://www.googleapis.com/youtube/v3"
CHANNELS_YAML = PROJECT_DIR / "channels.yaml"
DISCOVERED_YAML = PROJECT_DIR / "brain" / "candidates_discovered.yaml"
REJECTED_YAML = PROJECT_DIR / "brain" / "candidates_rejected.yaml"
WATCHLIST_YAML = PROJECT_DIR / "brain" / "candidates_watchlist.yaml"

# 默认搜索词组：面向个股分析型博主
DEFAULT_QUERIES = [
    "stock analysis price target",
    "stock picks portfolio update",
    "stock market technical analysis support resistance",
    "dividend stock analysis buy sell",
    "growth stock valuation deep dive",
]

# ---------------------------------------------------------------------------
# YouTube Data API
# ---------------------------------------------------------------------------

def _api_key() -> str:
    key = os.getenv("YOUTUBE_DATA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("YOUTUBE_DATA_API_KEY 未设置")
    return key


def search_channels(query: str, max_results: int = 10) -> list[dict]:
    """通过 YouTube Data API 搜索频道。"""
    resp = requests.get(
        f"{YT_API_BASE}/search",
        params={
            "part": "snippet",
            "q": query,
            "type": "channel",
            "maxResults": max_results,
            "order": "relevance",
            "key": _api_key(),
        },
        timeout=15,
    )
    resp.raise_for_status()
    results = []
    for item in resp.json().get("items", []):
        cid = item.get("id", {}).get("channelId", "")
        snippet = item.get("snippet", {})
        if cid:
            results.append({
                "channel_id": cid,
                "name": snippet.get("channelTitle", cid),
                "description": snippet.get("description", "")[:200],
                "source": f"search:{query[:30]}",
            })
    return results


def get_channel_sections(channel_id: str) -> list[dict]:
    """获取频道的 featured channels（通过 channelSections API）。"""
    try:
        resp = requests.get(
            f"{YT_API_BASE}/channelSections",
            params={
                "part": "snippet,contentDetails",
                "channelId": channel_id,
                "key": _api_key(),
            },
            timeout=15,
        )
        resp.raise_for_status()
        featured = []
        for section in resp.json().get("items", []):
            section_type = section.get("snippet", {}).get("type", "")
            if section_type == "multipleChannels":
                channels = section.get("contentDetails", {}).get("channels", [])
                for fc_id in channels:
                    featured.append({"channel_id": fc_id, "source": f"featured_by:{channel_id[:12]}"})
        return featured
    except Exception as e:
        log.warning(f"  获取 featured channels 失败 ({channel_id}): {e}")
        return []


def get_channel_name(channel_id: str) -> str:
    """通过 API 获取频道名称。"""
    try:
        resp = requests.get(
            f"{YT_API_BASE}/channels",
            params={"part": "snippet", "id": channel_id, "key": _api_key()},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items:
            return items[0]["snippet"]["title"]
    except Exception:
        pass
    return channel_id


def batch_get_channel_names(channel_ids: list[str]) -> dict[str, str]:
    """批量获取频道名称（每次最多 50 个）。"""
    result = {}
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i+50]
        try:
            resp = requests.get(
                f"{YT_API_BASE}/channels",
                params={"part": "snippet", "id": ",".join(batch), "key": _api_key()},
                timeout=15,
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                result[item["id"]] = item["snippet"]["title"]
        except Exception as e:
            log.warning(f"  批量获取频道名失败: {e}")
        time.sleep(0.2)
    return result


# ---------------------------------------------------------------------------
# 种子频道关系链
# ---------------------------------------------------------------------------

def discover_from_seeds() -> list[dict]:
    """从 channels.yaml 中的种子频道发现关联频道。"""
    yaml = YAML()
    if not CHANNELS_YAML.exists():
        log.warning("channels.yaml 不存在")
        return []

    with CHANNELS_YAML.open("r", encoding="utf-8") as f:
        channels = yaml.load(f) or []

    seed_ids = []
    for ch in channels:
        if not isinstance(ch, dict):
            continue
        url = str(ch.get("url", ""))
        m = re.search(r"channel_id=(UC[\w-]{22})", url)
        if m:
            seed_ids.append((ch.get("name", ""), m.group(1)))

    log.info(f"从 {len(seed_ids)} 个种子频道发现关联频道...")
    candidates = []

    for name, cid in seed_ids:
        log.info(f"  检查种子: {name} ({cid})")
        featured = get_channel_sections(cid)
        if featured:
            log.info(f"    发现 {len(featured)} 个 featured channels")
            candidates.extend(featured)
        time.sleep(0.3)

    # 批量补全频道名
    ids_need_names = [c["channel_id"] for c in candidates if "name" not in c]
    if ids_need_names:
        names = batch_get_channel_names(list(set(ids_need_names)))
        for c in candidates:
            if "name" not in c:
                c["name"] = names.get(c["channel_id"], c["channel_id"])

    return candidates


# ---------------------------------------------------------------------------
# 搜索发现
# ---------------------------------------------------------------------------

def discover_from_search(queries: list[str]) -> list[dict]:
    """通过 YouTube 搜索发现候选频道。"""
    log.info(f"执行 {len(queries)} 组搜索...")
    candidates = []
    for q in queries:
        log.info(f"  搜索: {q}")
        try:
            results = search_channels(q, max_results=10)
            log.info(f"    找到 {len(results)} 个频道")
            candidates.extend(results)
        except Exception as e:
            log.warning(f"    搜索失败: {e}")
        time.sleep(0.5)
    return candidates


# ---------------------------------------------------------------------------
# 去重 & 写入
# ---------------------------------------------------------------------------

def _load_known_channel_ids() -> set[str]:
    """加载所有已知频道 ID（channels.yaml + 已发现 + 已拒绝 + 观察列表）。"""
    known = set()
    for yaml_path in [CHANNELS_YAML, DISCOVERED_YAML, REJECTED_YAML, WATCHLIST_YAML]:
        if not yaml_path.exists():
            continue
        yaml = YAML()
        try:
            with yaml_path.open("r", encoding="utf-8") as f:
                data = yaml.load(f) or []
            for item in data:
                if not isinstance(item, dict):
                    continue
                # 从 url 字段提取
                url = str(item.get("url", ""))
                m = re.search(r"channel_id=(UC[\w-]{22})", url)
                if m:
                    known.add(m.group(1))
                # 从 channel_id 字段提取
                cid = str(item.get("channel_id", ""))
                if cid.startswith("UC"):
                    known.add(cid)
        except Exception:
            pass
    return known


def dedupe_and_save(candidates: list[dict]) -> int:
    """去重后追加到 candidates_discovered.yaml，返回新增数量。"""
    known_ids = _load_known_channel_ids()

    # 候选内部去重
    seen = set()
    unique = []
    for c in candidates:
        cid = c.get("channel_id", "")
        if not cid or cid in known_ids or cid in seen:
            continue
        seen.add(cid)
        unique.append(c)

    if not unique:
        log.info("没有新的候选频道")
        return 0

    # 追加到 YAML
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=2, offset=0)

    DISCOVERED_YAML.parent.mkdir(parents=True, exist_ok=True)
    if DISCOVERED_YAML.exists():
        with DISCOVERED_YAML.open("r", encoding="utf-8") as f:
            existing = yaml.load(f) or []
    else:
        existing = []

    for c in unique:
        entry = {
            "name": c.get("name", c["channel_id"]),
            "channel_id": c["channel_id"],
            "source": c.get("source", "unknown"),
        }
        if c.get("description"):
            entry["description"] = c["description"]
        existing.append(entry)

    with DISCOVERED_YAML.open("w", encoding="utf-8") as f:
        yaml.dump(existing, f)

    log.info(f"新增 {len(unique)} 个候选频道到 {DISCOVERED_YAML}")
    return len(unique)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="频道候选发现")
    parser.add_argument(
        "--source",
        choices=["all", "search", "seed"],
        default="all",
        help="发现渠道（默认 all）",
    )
    parser.add_argument(
        "--queries",
        help="自定义搜索词（逗号分隔），覆盖默认搜索词",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    candidates = []

    if args.source in ("all", "search"):
        queries = DEFAULT_QUERIES
        if args.queries:
            queries = [q.strip() for q in args.queries.split(",") if q.strip()]
        candidates.extend(discover_from_search(queries))

    if args.source in ("all", "seed"):
        candidates.extend(discover_from_seeds())

    if not candidates:
        log.info("未发现任何候选频道")
        return 0

    log.info(f"共发现 {len(candidates)} 个候选（去重前）")
    new_count = dedupe_and_save(candidates)

    print(f"\n发现完成: 新增 {new_count} 个候选 → {DISCOVERED_YAML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
