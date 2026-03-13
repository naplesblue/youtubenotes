#!/usr/bin/env python3
"""
run_backfill.py

独立的历史数据追溯脚本。
工作流：
1. 扫描 channels.yaml
2. 调取 yt-dlp 查询 2026-01-01 至今的所有视频
3. 对比 data/backfill_history.json，挑出 N 个未处理的视频下载音频
4. 调用 audio_analyzer.py（注入 TRACKING_FILE=data/backfill_history.json）
5. 调用 obsidian_sync.py
6. 清理当批次下载的音频文件，释放磁盘空间
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

load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
CHANNELS_FILE = PROJECT_DIR / "channels.yaml"
BACKFILL_FILE = DATA_DIR / "backfill_history.json"
BACKFILL_CACHE_FILE = DATA_DIR / "backfill_cache.json"
DOWNLOAD_DIR = DATA_DIR / "downloads"

PYTHON = sys.executable
YT_DLP = os.getenv("YTDLP_PATH", "yt-dlp")
COOKIES_PATH = os.getenv("YTDLP_COOKIES_PATH", str(DATA_DIR / "youtube_cookies.txt"))

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

def get_channel_videos(url, dateafter="20260101", video_cache=None):
    if video_cache is not None and url in video_cache:
        cached_data = video_cache[url]
        if cached_data.get("dateafter", "99999999") <= dateafter:
            logging.info(f"⚡ 命中本地元数据缓存: {url} (本地包含自 {cached_data['dateafter']} 的记录)")
            vids = cached_data.get("videos", [])
            # 返回 >= dateafter 的所有记录
            return [v for v in vids if v["upload_date"] >= dateafter]

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
            
        r = requests.get(base_url, params=params)
        if r.status_code != 200:
            logging.error(f"❌ YouTube API search 请求失败: {r.text}")
            break
            
        data = r.json()
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
        vr = requests.get(v_url, params=v_params)
        durations = {}
        if vr.status_code == 200:
            v_data = vr.json()
            for v in v_data.get("items", []):
                vid = v["id"]
                dur_str = v["contentDetails"]["duration"]
                durations[vid] = parse_iso_duration(dur_str)
        else:
            logging.error(f"❌ YouTube API videos 请求失败: {vr.text}")
            
        for item in items:
            vid = item["id"]["videoId"]
            title = item["snippet"]["title"]
            pub_date = item["snippet"]["publishedAt"]
            pub_date_flat = pub_date[:10].replace("-", "")
            dur = durations.get(vid, 0)
            
            title_lower = title.lower()
            if "#shorts" in title_lower or "shorts" in title_lower.split():
                continue
            if dur < 180:
                continue
                
            videos.append({
                "id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": title,
                "upload_date": pub_date_flat,
                "duration": dur
            })
            
        page_token = data.get("nextPageToken")
        if not page_token:
            break
            
    if video_cache is not None:
        video_cache[url] = {
            "dateafter": dateafter,
            "videos": videos
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
    args = parser.parse_args()

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
        if not url: continue
        
        feed_dict = tracker.setdefault(url, {})
        v_list = get_channel_videos(url, dateafter=args.dateafter, video_cache=video_cache)
        
        for v in v_list:
            vid = v.get("id")
            if vid in feed_dict:
                continue
            candidates.append((url, name, host, v))
            
    if not candidates:
        logging.info("🎉 恭喜！所选频道的历史视频已全部回溯完成。")
        sys.exit(0)
        
    logging.info(f"📊 发现 {len(candidates)} 个尚未处理的历史视频。")
    
    def get_date(v_tuple):
        v = v_tuple[3]
        return v.get("upload_date", "99999999")
        
    if video_cache.pop("_updated", False):
        save_video_cache(video_cache)
        
    candidates.sort(key=get_date)
    
    batch = candidates[:args.batch_size]
    logging.info(f"💼 截取当批次最早发布记录的 {len(batch)} 个视频开始全流程管线 (最早日期: {get_date(batch[0])})...")

    downloaded_files = []
    
    for feed_url, ch_name, ch_host, v_info in batch:
        vid = v_info.get("id")
        v_url = v_info.get("url") or f"https://www.youtube.com/watch?v={vid}"
        
        expected_path = download_audio(v_url, vid)
        if not expected_path or not expected_path.exists():
            logging.error(f"⚠️ 指定路径音频不存在，跳过: {expected_path}")
            continue
            
        downloaded_files.append(expected_path)
        
        # Format upload_date from YYYYMMDD to YYYY-MM-DD for consistency
        pub_raw = v_info.get("upload_date", "")
        formatted_date = f"{pub_raw[:4]}-{pub_raw[4:6]}-{pub_raw[6:8]}" if len(pub_raw) >= 8 else ""

        # Align EXACTLY with what `src/ytbnotes/analyzer/metadata.py` get_video_metadata expects
        tracker[feed_url][vid] = {
            "video_id": vid,
            "download_time": datetime.now(timezone.utc).isoformat(),
            "file_path": str(expected_path),
            "title": v_info.get("title", ""),
            "channel_name": ch_name,
            "host": ch_host,
            "original_url": v_url,
            "upload_date": formatted_date,
            "metadata": {
                "id": vid,
                "description": v_info.get("description", ""),
                "duration": v_info.get("duration", 0)
            }
        }
        
    save_backfill_history(tracker)
    
    if args.only_download:
        logging.info("⏹️ `--only-download` 被设置，终止后续分析管道。")
        sys.exit(0)
        
    if not downloaded_files:
        logging.warning("⚠️ 没有成功下载任何音频，取消分析管道。")
        sys.exit(1)
        
    logging.info("\n🚀 ================= 开始分析管道 =================")
    env = os.environ.copy()
    env["TRACKING_FILE"] = str(BACKFILL_FILE)
    
    try:
        subprocess.run(
            [PYTHON, str(PROJECT_DIR / "audio_analyzer.py")],
            env=env, cwd=str(PROJECT_DIR), check=True
        )
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ 分析管道异常终止: exit_code {e.returncode}")
        sys.exit(1)
        
    logging.info("\n📋 ================= 开始同步管道 =================")
    try:
        subprocess.run(
            [PYTHON, str(PROJECT_DIR / "obsidian_sync.py")],
            env=env, cwd=str(PROJECT_DIR), check=True
        )
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ 同步管道异常终止: exit_code {e.returncode}")
        sys.exit(1)
        
    logging.info("\n🧹 ================= 清理当批次环境 =================")
    analysis_log_path = DATA_DIR / "analysis_log.json"
    successful_vids = set()
    log_data = []
    
    if analysis_log_path.exists():
        try:
            with open(analysis_log_path, "r", encoding="utf-8") as f:
                log_data = json.load(f)
            successful_vids = set()
            for entry in log_data:
                if entry.get("status") == "success" and entry.get("video_file_path"):
                    try:
                        successful_vids.add(Path(entry["video_file_path"]).stem)
                    except:
                        pass
        except Exception as e:
            logging.warning(f"⚠️ 读取分析日志时出错: {e}")

    # 1. 仅删除成功分析的音频，失败的保留供下次重试
    for fp in downloaded_files:
        vid = fp.stem
        if vid in successful_vids and fp.exists():
            try:
                fp.unlink()
                logging.info(f"🗑️ 已成功分析并删除音频: {fp}")
            except Exception as e:
                logging.warning(f"⚠️ 删除失败: {fp} - {e}")
        elif fp.exists():
            logging.warning(f"⚠️ 视频 {vid} 未显示成功状态，保留音频以备重试: {fp}")

    # 2. 从日常日志中隐去本次的大批量测试足迹防止污染
    if log_data:
        try:
            processed_vids = {v_tuple[3].get("id") for v_tuple in batch}
            initial_count = len(log_data)
            
            # 过滤掉属于本次回溯批次的视频记录
            filtered_logs = []
            for entry in log_data:
                v_path = entry.get("video_file_path")
                if not v_path:
                    filtered_logs.append(entry)
                    continue
                try:
                    entry_vid = Path(v_path).stem
                    if entry_vid not in processed_vids:
                        filtered_logs.append(entry)
                except:
                    filtered_logs.append(entry)
            
            if len(filtered_logs) < initial_count:
                with open(analysis_log_path, "w", encoding="utf-8") as f:
                    json.dump(filtered_logs, f, indent=2, ensure_ascii=False)
                logging.info(f"✨ 已从 analysis_log.json 中彻底移除 {initial_count - len(filtered_logs)} 条回溯记录足迹。")
        except Exception as e:
            logging.warning(f"⚠️ 清理分析日志时出错: {e}")

    logging.info("\n✅ 当批次历史回退已完美结束！")

if __name__ == "__main__":
    main()
