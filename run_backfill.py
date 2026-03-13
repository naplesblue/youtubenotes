#!/usr/bin/env python3
"""
run_backfill.py

独立的历史数据追溯脚本。
工作流：
1. 扫描 channels.yaml
2. 调取 YouTube API 查询 dateafter 至今的所有视频（带缓存与重试）
3. 对比 data/backfill_history.json，挑出 N 个未完成状态的视频
4. 字幕优先准备输入：命中字幕则保存文本并跳过音频下载；失败可回退音频
5. 调用 audio_analyzer.py（注入 TRACKING_FILE=data/backfill_history.json）
6. 调用 obsidian_sync.py 并回写状态机（input_ready/analyzed/done）
7. 清理当批次已完成分析的音频文件
"""

import os
import sys
import json
import yaml
import logging
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import re
import requests
import time
from yt_dlp.utils import sanitize_filename

from src.ytbnotes.analyzer.subtitle import load_subtitle_transcript

load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
CHANNELS_FILE = PROJECT_DIR / "channels.yaml"
BACKFILL_FILE = DATA_DIR / "backfill_history.json"
BACKFILL_CACHE_FILE = DATA_DIR / "backfill_cache.json"
DOWNLOAD_DIR = DATA_DIR / "downloads"
SUBTITLE_DIR = DATA_DIR / "subtitles"

PYTHON = sys.executable
YT_DLP = os.getenv("YTDLP_PATH", "yt-dlp")
COOKIES_PATH = os.getenv("YTDLP_COOKIES_PATH", str(DATA_DIR / "youtube_cookies.txt"))
SUBTITLE_FIRST_ENABLED = os.getenv("SUBTITLE_FIRST_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
SUBTITLE_TO_ASR_FALLBACK = os.getenv("SUBTITLE_TO_ASR_FALLBACK", "1").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, min_value: int | None = None) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(str(raw).strip())
    except Exception:
        value = default
    if min_value is not None and value < min_value:
        value = min_value
    return value


def _env_float(name: str, default: float, min_value: float | None = None) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(str(raw).strip())
    except Exception:
        value = default
    if min_value is not None and value < min_value:
        value = min_value
    return value


BACKFILL_API_TIMEOUT_SECONDS = _env_int("BACKFILL_API_TIMEOUT_SECONDS", 20, min_value=5)
BACKFILL_API_MAX_RETRIES = _env_int("BACKFILL_API_MAX_RETRIES", 3, min_value=0)
BACKFILL_API_BACKOFF_SECONDS = _env_float("BACKFILL_API_BACKOFF_SECONDS", 1.5, min_value=0.0)
BACKFILL_CACHE_TTL_HOURS_DEFAULT = _env_int("BACKFILL_CACHE_TTL_HOURS", 24, min_value=0)

RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}

STATUS_INPUT_READY = "input_ready"
STATUS_ANALYZED = "analyzed"
STATUS_DONE = "done"
STATUS_FAILED_ANALYZE = "failed_analyze"
STATUS_FAILED_SYNC = "failed_sync"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    force=True
)

def load_backfill_history():
    if not BACKFILL_FILE.exists():
        return {}
    try:
        with open(BACKFILL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_backfill_history(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(BACKFILL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_video_cache():
    if not BACKFILL_CACHE_FILE.exists():
        return {}
    try:
        with open(BACKFILL_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_video_cache(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 移除内部脏标记
    data_to_save = {k: v for k, v in data.items() if k != "_updated"}
    with open(BACKFILL_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data_to_save, f, indent=4, ensure_ascii=False)

def parse_iso_duration(duration_str):
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0
    hours = int(match.group(1)) if match.group(1) else 0
    mins = int(match.group(2)) if match.group(2) else 0
    secs = int(match.group(3)) if match.group(3) else 0
    return hours * 3600 + mins * 60 + secs


def parse_utc_datetime(raw_value) -> datetime | None:
    if not raw_value:
        return None
    try:
        dt = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _backoff_sleep_seconds(attempt: int) -> float:
    return BACKFILL_API_BACKOFF_SECONDS * (2 ** max(attempt, 0))


def request_json_with_retry(url: str, params: dict, api_name: str) -> dict | None:
    max_attempts = BACKFILL_API_MAX_RETRIES + 1
    for attempt in range(max_attempts):
        try:
            response = requests.get(url, params=params, timeout=BACKFILL_API_TIMEOUT_SECONDS)
        except requests.RequestException as e:
            if attempt < max_attempts - 1:
                wait_seconds = _backoff_sleep_seconds(attempt)
                logging.warning(
                    f"⚠️ {api_name} 请求异常，{wait_seconds:.1f}s 后重试 "
                    f"({attempt + 1}/{max_attempts}): {type(e).__name__}: {e}"
                )
                time.sleep(wait_seconds)
                continue
            logging.error(f"❌ {api_name} 请求异常且达到最大重试次数: {type(e).__name__}: {e}")
            return None

        if response.status_code == 200:
            try:
                data = response.json()
                if isinstance(data, dict):
                    return data
                logging.error(f"❌ {api_name} 返回非 JSON 对象。")
                return None
            except ValueError as e:
                logging.error(f"❌ {api_name} JSON 解析失败: {e}")
                return None

        retriable = response.status_code in RETRYABLE_HTTP_STATUS
        response_tail = (response.text or "").strip()[-300:]
        if retriable and attempt < max_attempts - 1:
            wait_seconds = _backoff_sleep_seconds(attempt)
            logging.warning(
                f"⚠️ {api_name} 返回 HTTP {response.status_code}，{wait_seconds:.1f}s 后重试 "
                f"({attempt + 1}/{max_attempts})"
            )
            time.sleep(wait_seconds)
            continue

        logging.error(f"❌ {api_name} 请求失败: HTTP {response.status_code} | {response_tail}")
        return None
    return None


def is_cache_entry_usable(
    cache_entry: dict,
    dateafter: str,
    cache_ttl_seconds: int,
    refresh_cache: bool,
) -> tuple[bool, str]:
    if refresh_cache:
        return False, "refresh_requested"
    if not isinstance(cache_entry, dict):
        return False, "invalid_cache"
    if cache_ttl_seconds <= 0:
        return False, "cache_disabled_by_ttl"

    cached_dateafter = str(cache_entry.get("dateafter", "99999999"))
    if cached_dateafter > dateafter:
        return False, "cache_range_insufficient"

    fetched_at = parse_utc_datetime(cache_entry.get("fetched_at"))
    if fetched_at is None:
        return False, "cache_missing_fetched_at"

    age_seconds = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    if age_seconds > cache_ttl_seconds:
        return False, "cache_expired"

    videos = cache_entry.get("videos")
    if not isinstance(videos, list):
        return False, "cache_videos_invalid"

    return True, "cache_hit"


def get_record_status(record: dict | None) -> str:
    if not isinstance(record, dict):
        return ""
    return str(record.get("status", "")).strip().lower()


def normalize_upload_date(upload_date: str) -> str:
    raw = str(upload_date or "").strip()
    if len(raw) >= 8 and raw[:8].isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


def resolve_input_path(record: dict | None) -> Path | None:
    if not isinstance(record, dict):
        return None
    input_type = str(record.get("input_type", "audio")).strip().lower()
    primary_path = record.get("subtitle_path") if input_type == "subtitle" else record.get("file_path")
    fallback_path = record.get("file_path") or record.get("subtitle_path")
    candidate = str(primary_path or fallback_path or "").strip()
    if not candidate:
        return None
    try:
        path_obj = Path(candidate).resolve()
        return path_obj if path_obj.exists() else None
    except Exception:
        return None


def save_subtitle_text(video_id: str, channel_name: str, video_title: str, upload_date: str, transcript: str) -> Path:
    safe_channel = sanitize_filename(channel_name or "Unknown")
    safe_title = sanitize_filename(video_title or "无标题").strip()[:120] or "无标题"
    date_prefix = str(upload_date or "").replace("-", "")
    if not date_prefix or len(date_prefix) != 8:
        date_prefix = datetime.now().strftime("%Y%m%d")
    subtitle_dir = SUBTITLE_DIR / safe_channel
    subtitle_dir.mkdir(parents=True, exist_ok=True)
    subtitle_path = subtitle_dir / f"{date_prefix} - {safe_title} [{video_id}].txt"
    subtitle_path.write_text(transcript, encoding="utf-8")
    return subtitle_path.resolve()


def build_tracker_record(
    *,
    record: dict | None,
    video_id: str,
    feed_url: str,
    channel_name: str,
    host: str,
    video_url: str,
    video_title: str,
    upload_date_yyyymmdd: str,
    published_time: str | None,
    duration_seconds: int,
    input_type: str,
    input_path: Path,
    subtitle_probe_result: dict | None,
) -> dict:
    existing = record if isinstance(record, dict) else {}
    existing_metadata = existing.get("metadata")
    if not isinstance(existing_metadata, dict):
        existing_metadata = {}
    formatted_date = normalize_upload_date(upload_date_yyyymmdd)
    input_path_str = str(input_path)

    payload = {
        "video_id": video_id,
        "channel_name": channel_name,
        "host": host,
        "original_url": video_url,
        "title": video_title,
        "upload_date": formatted_date,
        "published_time": published_time or existing.get("published_time") or (f"{formatted_date}T00:00:00" if formatted_date else None),
        "input_type": input_type,
        "file_path": input_path_str,
        "subtitle_path": input_path_str if input_type == "subtitle" else None,
        "subtitle_probe_result": subtitle_probe_result,
        "download_time": datetime.now(timezone.utc).isoformat(),
        "status": STATUS_INPUT_READY,
        "metadata": {
            "id": video_id,
            "description": existing_metadata.get("description", ""),
            "duration": duration_seconds,
            "feed_url": feed_url,
        },
    }
    return payload

def get_channel_videos(
    url,
    dateafter="20260101",
    video_cache=None,
    refresh_cache=False,
    cache_ttl_seconds=24 * 3600,
):
    if video_cache is not None and url in video_cache:
        cached_data = video_cache[url]
        usable, reason = is_cache_entry_usable(
            cached_data,
            dateafter=dateafter,
            cache_ttl_seconds=cache_ttl_seconds,
            refresh_cache=refresh_cache,
        )
        if usable:
            logging.info(f"⚡ 命中本地元数据缓存: {url} (cache_dateafter={cached_data.get('dateafter')})")
            vids = cached_data.get("videos", [])
            return [v for v in vids if str(v.get("upload_date", "")) >= dateafter]
        logging.info(f"🕒 缓存未命中，原因: {reason} | {url}")

    api_key = os.getenv("YOUTUBE_DATA_API_KEY")
    if not api_key:
        logging.error("❌ 缺少 YOUTUBE_DATA_API_KEY")
        return []
        
    channel_id = None
    if "channel_id=" in url:
        channel_id = url.split("channel_id=")[1].split("&")[0]
    else:
        logging.error(f"❌ 无法提取 channel_id: {url}")
        return []

    published_after = f"{dateafter[:4]}-{dateafter[4:6]}-{dateafter[6:8]}T00:00:00Z"
    logging.info(f"🔍 正在调用 YouTube API 检索频道: {channel_id} (自 {published_after} 起)")
    
    videos = []
    page_token = None
    base_url = "https://www.googleapis.com/youtube/v3/search"
    
    while True:
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "maxResults": 50,
            "order": "date",
            "publishedAfter": published_after,
            "type": "video",
            "key": api_key
        }
        if page_token:
            params["pageToken"] = page_token
            
        data = request_json_with_retry(base_url, params, "YouTube API search")
        if not data:
            break

        items = data.get("items", [])
        if not items:
            break
            
        video_ids = [item["id"]["videoId"] for item in items]
        if not video_ids:
            break
            
        # 批量获取时长
        v_url = "https://www.googleapis.com/youtube/v3/videos"
        v_params = {
            "part": "contentDetails",
            "id": ",".join(video_ids),
            "key": api_key
        }
        v_data = request_json_with_retry(v_url, v_params, "YouTube API videos")
        durations = {}
        if v_data:
            for v in v_data.get("items", []):
                vid = v["id"]
                dur_str = v["contentDetails"]["duration"]
                durations[vid] = parse_iso_duration(dur_str)
        else:
            logging.warning("⚠️ YouTube API videos 未返回有效数据，本页将使用默认时长 0。")
            
        for item in items:
            vid = item["id"]["videoId"]
            title = item["snippet"]["title"]
            pub_date = item["snippet"]["publishedAt"]
            pub_date_flat = pub_date[:10].replace("-", "")
            dur = durations.get(vid)
            
            title_lower = title.lower()
            if "#shorts" in title_lower or "shorts" in title_lower.split():
                continue
            if dur is not None and dur < 180:
                continue
                
            videos.append({
                "id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": title,
                "upload_date": pub_date_flat,
                "published_time": pub_date,
                "duration": int(dur or 0),
            })
            
        page_token = data.get("nextPageToken")
        if not page_token:
            break
            
    if video_cache is not None:
        video_cache[url] = {
            "dateafter": dateafter,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "videos": videos,
        }
        video_cache["_updated"] = True
            
    return videos

def download_audio(video_url, vid):
    logging.info(f"📥 正在下载音频: {video_url}")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    output_tmpl = str(DOWNLOAD_DIR / f"{vid}.%(ext)s")
    
    cmd = [
        YT_DLP,
        "--ignore-config",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--no-playlist",
        "-o", output_tmpl
    ]
    if os.getenv("YTDLP_USE_COOKIES", "0") == "1" and os.path.exists(COOKIES_PATH):
        cmd.extend(["--cookies", str(COOKIES_PATH)])
    cmd.append(video_url)
    
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        expected_path = DOWNLOAD_DIR / f"{vid}.mp3"
        return expected_path
        
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ 下载音频失败: {video_url}\n{e.stderr}")
        return None

def main():
    parser = argparse.ArgumentParser(description="回溯历史视频下载与分析")
    parser.add_argument("--batch-size", type=int, default=5, help="单次处理的最大视频数量")
    parser.add_argument("--dateafter", type=str, default="20260101", help="YYYYMMDD，回溯起始日期")
    parser.add_argument("--only-download", action="store_true", help="只下载，不运行分析和同步")
    parser.add_argument("--refresh-cache", action="store_true", help="忽略本地 video cache，强制刷新频道元数据")
    parser.add_argument(
        "--cache-ttl-hours",
        type=int,
        default=BACKFILL_CACHE_TTL_HOURS_DEFAULT,
        help=f"本地 video cache TTL（小时，0=禁用缓存，默认 {BACKFILL_CACHE_TTL_HOURS_DEFAULT}）",
    )
    args = parser.parse_args()
    if args.batch_size <= 0:
        logging.error("❌ --batch-size 必须大于 0")
        sys.exit(1)
    if not re.match(r"^\d{8}$", args.dateafter or ""):
        logging.error("❌ --dateafter 格式错误，必须为 YYYYMMDD")
        sys.exit(1)
    if args.cache_ttl_hours < 0:
        logging.error("❌ --cache-ttl-hours 不能小于 0")
        sys.exit(1)
    cache_ttl_seconds = args.cache_ttl_hours * 3600
    logging.info(
        f"回填参数: dateafter={args.dateafter}, batch_size={args.batch_size}, "
        f"refresh_cache={args.refresh_cache}, cache_ttl_hours={args.cache_ttl_hours}, "
        f"api_timeout={BACKFILL_API_TIMEOUT_SECONDS}s, api_retries={BACKFILL_API_MAX_RETRIES}"
    )

    if not CHANNELS_FILE.exists():
        logging.error(f"❌ 找不到 channels.yaml: {CHANNELS_FILE}")
        sys.exit(1)

    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        channels = yaml.safe_load(f)

    tracker = load_backfill_history()
    video_cache = load_video_cache()

    candidates = []
    for ch in channels:
        name = ch.get("name", "Unknown")
        host = ch.get("host", name)
        url = ch.get("url")
        if not url:
            continue

        feed_dict = tracker.get(url)
        if not isinstance(feed_dict, dict):
            feed_dict = {}
            tracker[url] = feed_dict

        v_list = get_channel_videos(
            url,
            dateafter=args.dateafter,
            video_cache=video_cache,
            refresh_cache=args.refresh_cache,
            cache_ttl_seconds=cache_ttl_seconds,
        )
        for v in v_list:
            vid = v.get("id")
            if not vid:
                continue
            record = feed_dict.get(vid)
            if get_record_status(record) == STATUS_DONE:
                continue
            candidates.append((url, name, host, v, record))

    if video_cache.pop("_updated", False):
        save_video_cache(video_cache)

    if not candidates:
        logging.info("🎉 恭喜！所选频道的历史视频已全部回溯完成（status=done）。")
        sys.exit(0)

    logging.info(f"📊 发现 {len(candidates)} 个未完成状态的视频（待准备输入或待同步）。")

    def get_date(v_tuple):
        v = v_tuple[3]
        return v.get("upload_date", "99999999")

    candidates.sort(key=get_date)
    batch = candidates[:args.batch_size]
    logging.info(
        f"💼 选取当批次最早发布的 {len(batch)} 个视频执行 (最早日期: {get_date(batch[0])}) "
        f"| subtitle_first={SUBTITLE_FIRST_ENABLED}, subtitle_to_asr_fallback={SUBTITLE_TO_ASR_FALLBACK}"
    )

    SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)
    downloaded_audio = []
    batch_context = []

    for feed_url, ch_name, ch_host, v_info, old_record in batch:
        vid = str(v_info.get("id", "")).strip()
        if not vid:
            continue
        v_url = v_info.get("url") or f"https://www.youtube.com/watch?v={vid}"
        old_status = get_record_status(old_record)
        existing_input_path = resolve_input_path(old_record)

        input_path = None
        input_type = "audio"
        subtitle_probe_result = old_record.get("subtitle_probe_result") if isinstance(old_record, dict) else None

        if existing_input_path and old_status in {"", STATUS_INPUT_READY, STATUS_ANALYZED, STATUS_FAILED_ANALYZE, STATUS_FAILED_SYNC}:
            input_path = existing_input_path
            input_type = str((old_record or {}).get("input_type", "audio")).strip().lower() or "audio"
            logging.info(f"♻️ 复用现有输入: vid={vid}, status={old_status}, path={input_path}")
        else:
            if SUBTITLE_FIRST_ENABLED:
                logging.info(f"📝 字幕优先探测: {v_url}")
                try:
                    subtitle_result = load_subtitle_transcript(v_url)
                except Exception as e:
                    subtitle_result = {
                        "ok": False,
                        "probe": {"ok": False, "reason": f"exception:{type(e).__name__}"},
                        "quality": None,
                        "error": f"subtitle_exception:{type(e).__name__}:{e}",
                        "lang_family": None,
                        "source": None,
                    }
                subtitle_probe_result = {
                    "ok": bool(subtitle_result.get("ok")),
                    "probe": subtitle_result.get("probe"),
                    "quality": subtitle_result.get("quality"),
                    "error": subtitle_result.get("error"),
                    "lang_family": subtitle_result.get("lang_family"),
                    "source": subtitle_result.get("source"),
                }
                if subtitle_result.get("ok") and subtitle_result.get("transcript"):
                    try:
                        subtitle_abs = save_subtitle_text(
                            video_id=vid,
                            channel_name=ch_name,
                            video_title=v_info.get("title", ""),
                            upload_date=v_info.get("upload_date", ""),
                            transcript=subtitle_result.get("transcript", ""),
                        )
                        input_path = subtitle_abs
                        input_type = "subtitle"
                        logging.info(f"✅ 命中字幕并保存，跳过音频下载: {subtitle_abs}")
                    except Exception as e:
                        logging.warning(f"⚠️ 字幕保存失败，准备回退音频: vid={vid}, err={e}")
                else:
                    err_msg = subtitle_result.get("error") or "subtitle_not_available"
                    if not SUBTITLE_TO_ASR_FALLBACK:
                        logging.warning(f"⏭️ 字幕不可用且禁用回退，跳过视频: vid={vid}, reason={err_msg}")
                        continue
                    logging.info(f"字幕不可用或质量不达标，回退音频下载: vid={vid}, reason={err_msg}")

            if input_path is None:
                expected_path = download_audio(v_url, vid)
                if not expected_path or not expected_path.exists():
                    logging.error(f"⚠️ 音频下载失败，跳过: vid={vid}, path={expected_path}")
                    continue
                input_path = expected_path.resolve()
                input_type = "audio"
                downloaded_audio.append((feed_url, vid, input_path))

        tracker[feed_url][vid] = build_tracker_record(
            record=old_record,
            video_id=vid,
            feed_url=feed_url,
            channel_name=ch_name,
            host=ch_host,
            video_url=v_url,
            video_title=v_info.get("title", ""),
            upload_date_yyyymmdd=v_info.get("upload_date", ""),
            published_time=v_info.get("published_time"),
            duration_seconds=int(v_info.get("duration", 0) or 0),
            input_type=input_type,
            input_path=input_path,
            subtitle_probe_result=subtitle_probe_result,
        )
        batch_context.append({
            "feed_url": feed_url,
            "video_id": vid,
            "input_path": input_path,
        })

    save_backfill_history(tracker)

    if args.only_download:
        logging.info("⏹️ `--only-download` 被设置，终止后续分析与同步。")
        sys.exit(0)

    if not batch_context:
        logging.warning("⚠️ 当批次没有可用输入，取消分析与同步。")
        sys.exit(1)

    logging.info("\n🚀 ================= 开始分析管道 =================")
    env = os.environ.copy()
    env["TRACKING_FILE"] = str(BACKFILL_FILE)
    analyze_rc = subprocess.run(
        [PYTHON, str(PROJECT_DIR / "audio_analyzer.py")],
        env=env, cwd=str(PROJECT_DIR), check=False
    ).returncode
    if analyze_rc != 0:
        logging.error(f"❌ 分析管道返回非零退出码: {analyze_rc}")

    analysis_log_path = DATA_DIR / "analysis_log.json"
    path_to_vid = {}
    for ctx in batch_context:
        path_to_vid[str(Path(ctx["input_path"]).resolve())] = (ctx["feed_url"], ctx["video_id"])

    latest_log_by_vid = {}
    if analysis_log_path.exists():
        try:
            with open(analysis_log_path, "r", encoding="utf-8") as f:
                log_data = json.load(f)
            for entry in log_data:
                raw_path = entry.get("video_file_path")
                if not raw_path:
                    continue
                try:
                    resolved_path = str(Path(raw_path).resolve())
                except Exception:
                    continue
                feed_vid = path_to_vid.get(resolved_path)
                if not feed_vid:
                    continue
                _, vid = feed_vid
                latest_log_by_vid[vid] = entry
        except Exception as e:
            logging.warning(f"⚠️ 读取 analysis_log.json 失败，无法精确回写分析状态: {e}")

    analyzed_targets = []
    for ctx in batch_context:
        feed_url = ctx["feed_url"]
        vid = ctx["video_id"]
        rec = tracker.get(feed_url, {}).get(vid)
        if not isinstance(rec, dict):
            continue
        latest = latest_log_by_vid.get(vid)
        current_status = get_record_status(rec)
        if latest and latest.get("status") == "success":
            rec["status"] = STATUS_ANALYZED
            rec["analyze_time"] = datetime.now(timezone.utc).isoformat()
            rec["analysis_error"] = None
            analyzed_targets.append((feed_url, vid))
        elif current_status == STATUS_ANALYZED:
            analyzed_targets.append((feed_url, vid))
        elif current_status != STATUS_DONE:
            rec["status"] = STATUS_FAILED_ANALYZE
            rec["analysis_error"] = (latest or {}).get("error_message") or f"analyzer_rc={analyze_rc}"

    save_backfill_history(tracker)

    sync_candidates = []
    for feed_url, vid in analyzed_targets:
        rec = tracker.get(feed_url, {}).get(vid)
        if isinstance(rec, dict) and get_record_status(rec) == STATUS_ANALYZED:
            sync_candidates.append((feed_url, vid))

    if sync_candidates:
        logging.info("\n📋 ================= 开始同步管道 =================")
        sync_rc = subprocess.run(
            [PYTHON, str(PROJECT_DIR / "obsidian_sync.py")],
            env=env, cwd=str(PROJECT_DIR), check=False
        ).returncode
        if sync_rc == 0:
            for feed_url, vid in sync_candidates:
                rec = tracker.get(feed_url, {}).get(vid)
                if not isinstance(rec, dict):
                    continue
                rec["status"] = STATUS_DONE
                rec["sync_time"] = datetime.now(timezone.utc).isoformat()
                rec["sync_error"] = None
            logging.info(f"✅ 同步成功，已标记 done: {len(sync_candidates)} 条。")
        else:
            for feed_url, vid in sync_candidates:
                rec = tracker.get(feed_url, {}).get(vid)
                if not isinstance(rec, dict):
                    continue
                rec["status"] = STATUS_FAILED_SYNC
                rec["sync_error"] = f"sync_rc={sync_rc}"
            logging.error(f"❌ 同步管道返回非零退出码: {sync_rc}")
    else:
        logging.info("ℹ️ 本批次无可同步条目，跳过同步步骤。")

    save_backfill_history(tracker)

    logging.info("\n🧹 ================= 清理当批次环境 =================")
    for feed_url, vid, audio_path in downloaded_audio:
        rec = tracker.get(feed_url, {}).get(vid)
        status = get_record_status(rec)
        if status in {STATUS_ANALYZED, STATUS_DONE, STATUS_FAILED_SYNC} and audio_path.exists():
            try:
                audio_path.unlink()
                logging.info(f"🗑️ 删除已完成分析的当批音频: {audio_path}")
            except Exception as e:
                logging.warning(f"⚠️ 删除失败: {audio_path} - {e}")
        elif audio_path.exists():
            logging.warning(f"⚠️ 保留音频等待重试: vid={vid}, status={status}, path={audio_path}")

    failed_in_batch = []
    for ctx in batch_context:
        feed_url = ctx["feed_url"]
        vid = ctx["video_id"]
        status = get_record_status(tracker.get(feed_url, {}).get(vid))
        if status not in {STATUS_DONE, STATUS_ANALYZED}:
            failed_in_batch.append((vid, status))

    if failed_in_batch:
        logging.warning(f"⚠️ 当批次存在未完成条目: {failed_in_batch}")
        sys.exit(1)

    logging.info("\n✅ 当批次历史回溯已完成。")

if __name__ == "__main__":
    main()
