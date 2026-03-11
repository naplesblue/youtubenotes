import yaml
import feedparser
import subprocess
import os
import json
import logging
import datetime
import time
import tempfile
import requests
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from dotenv import load_dotenv
from yt_dlp.utils import sanitize_filename

load_dotenv()

# =============================================================================
# 原子写入工具
# =============================================================================

def write_file_atomically(filepath, content, mode='w', encoding='utf-8'):
    """先写临时文件，再 os.replace 原子替换，防止写入中断导致文件损坏。"""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(dir=filepath.parent, prefix='.tmp_')
    try:
        if mode == 'w':
            with os.fdopen(temp_fd, 'w', encoding=encoding) as f:
                f.write(content)
        else:  # 'wb'
            with os.fdopen(temp_fd, 'wb') as f:
                f.write(content)
        os.replace(temp_path, filepath)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def save_tracking_data(filepath, data):
    """原子方式保存 tracking JSON，直接复用 write_file_atomically。"""
    try:
        content = json.dumps(data, indent=4, ensure_ascii=False)
        write_file_atomically(filepath, content)
        logging.debug(f"跟踪数据已保存: {filepath}")
    except Exception as e:
        logging.error(f"保存跟踪数据时发生错误: {e}")

# =============================================================================
# 配置常量
# =============================================================================

PROJECT_DIR        = Path(__file__).resolve().parent.parent.parent.parent

CHANNELS_FILE      = str(PROJECT_DIR / 'channels.yaml')
TRACKING_FILE      = str(PROJECT_DIR / 'data' / 'download_history.json')
DOWNLOAD_DIR       = str(PROJECT_DIR / 'data' / 'downloads')
YT_DLP_PATH        = 'yt-dlp'
AUDIO_FORMAT       = 'mp3'

# Cookies：优先读环境变量，默认使用项目根目录下的 data/youtube_cookies.txt
COOKIES_PATH       = os.getenv('YTDLP_COOKIES_PATH', str(PROJECT_DIR / 'data' / 'youtube_cookies.txt'))

# 下载控制（download_video 内部统一引用，不再重复硬编码）
MAX_DOWNLOAD_RETRIES = 3
DOWNLOAD_TIMEOUT     = 1800   # 秒，30 分钟
RETRY_BASE_DELAY     = 10     # 秒，退避基数

# RSS 默认每个频道最多拉取的条目数（可在 channels.yaml 的频道条目里用 max_entries 覆盖）
DEFAULT_MAX_ENTRIES  = 5
HISTORY_TTL_DAYS     = 3
LATEST_PER_CHANNEL   = 1
CLEANUP_DOWNLOAD_FILES = os.getenv("YTDLP_CLEANUP_DOWNLOAD_FILES", "1").strip().lower() in {
    "1", "true", "yes", "on"
}

# YouTube Data API Key（用于 RSS 失败时的回退查询）
YOUTUBE_DATA_API_KEY = os.getenv("YOUTUBE_DATA_API_KEY", "").strip()

# =============================================================================
# 日志
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8',   # 防止频道名含中文时日志写文件报编码错误
)

# =============================================================================
# Cookie 相关
# =============================================================================

# 确定是 cookie/登录问题 → 值得尝试刷新 cookie
_COOKIE_ERROR_PATTERNS = [
    "HTTP Error 403",
    "Login required",
    "sign in to confirm your age",
    "Please sign in",
    "Members only",
    "This video is available to members only",
    "Your account has been terminated",
]

# 永久性错误 → 刷新 cookie 没有帮助，直接跳过，不重试
_PERMANENT_ERROR_PATTERNS = [
    "Video unavailable",
    "This video has been removed",
    "Premiere will begin",
    "This live event will begin",
]


def is_cookie_error(stderr: str) -> bool:
    """判断是否是 cookie/登录类错误（值得刷新重试）。"""
    stderr_lower = stderr.lower()
    return any(p.lower() in stderr_lower for p in _COOKIE_ERROR_PATTERNS)


def is_permanent_error(stderr: str) -> bool:
    """判断是否是永久性错误（刷新 cookie 也无法解决，应直接跳过）。"""
    stderr_lower = stderr.lower()
    return any(p.lower() in stderr_lower for p in _PERMANENT_ERROR_PATTERNS)


def refresh_cookies(cookies_path: str, test_url: str) -> bool:
    """使用 yt-dlp 从 Chrome 浏览器刷新 cookies，成功返回 True。"""
    logging.info("尝试从 Chrome 浏览器刷新 YouTube cookies...")
    command = [
        YT_DLP_PATH,
        '--cookies-from-browser', 'chrome',
        '--cookies', cookies_path,
        '--skip-download',
        test_url,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            encoding='utf-8', timeout=60,
        )
        if result.returncode == 0:
            logging.info("Cookie 刷新成功。")
            return True
        logging.error(f"Cookie 刷新失败: {result.stderr}")
        return False
    except subprocess.TimeoutExpired:
        logging.error("Cookie 刷新超时。")
        return False
    except Exception as e:
        logging.error(f"Cookie 刷新异常: {e}")
        return False

# =============================================================================
# 频道与历史记录
# =============================================================================

def load_channels(filepath: str) -> list | None:
    """从 YAML 文件加载频道列表，返回合法条目列表，失败返回 None。"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            channels = yaml.safe_load(f)
        if not isinstance(channels, list):
            logging.error(f"'{filepath}' 格式错误，顶层应为列表。")
            return None
        valid = []
        for item in channels:
            if isinstance(item, dict) and 'name' in item and 'url' in item:
                valid.append(item)
            else:
                logging.warning(f"跳过无效的频道条目: {item}")
        return valid
    except FileNotFoundError:
        logging.error(f"找不到频道文件 '{filepath}'。")
        return None
    except yaml.YAMLError as e:
        logging.error(f"解析 YAML 文件 '{filepath}' 失败: {e}")
        return None
    except Exception as e:
        logging.error(f"加载频道时发生未知错误: {e}")
        return None


def load_tracking_data(filepath: str) -> dict:
    """
    加载下载历史记录。
    文件不存在返回空字典；文件损坏时自动备份后返回空字典。
    """
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        backup_path = (
            f"{filepath}.corrupt."
            f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        )
        try:
            os.rename(filepath, backup_path)
            logging.error(f"下载历史文件损坏，已备份至 '{backup_path}'。错误: {e}")
        except OSError:
            logging.error(f"下载历史文件损坏且无法备份。错误: {e}")
        return {}
    except Exception as e:
        logging.error(f"加载跟踪数据时发生错误: {e}")
        return {}


def _normalize_dt(dt_obj: datetime.datetime | None) -> datetime.datetime | None:
    if dt_obj is None:
        return None
    if dt_obj.tzinfo is not None:
        try:
            return dt_obj.astimezone().replace(tzinfo=None)
        except Exception:
            return dt_obj.replace(tzinfo=None)
    return dt_obj


def parse_tracking_time(raw_value) -> datetime.datetime | None:
    if not raw_value:
        return None
    try:
        dt_obj = datetime.datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        return _normalize_dt(dt_obj)
    except Exception:
        return None


def get_entry_published_datetime(entry) -> datetime.datetime | None:
    """尽量从 RSS 条目里解析发布时间，用于 3 天回溯窗口过滤。"""
    if not isinstance(entry, dict):
        return None

    for parsed_key in ("published_parsed", "updated_parsed"):
        parsed_val = entry.get(parsed_key)
        if parsed_val:
            try:
                return datetime.datetime(*parsed_val[:6])
            except Exception:
                pass

    for text_key in ("published", "updated"):
        text_val = entry.get(text_key)
        if text_val:
            try:
                return _normalize_dt(parsedate_to_datetime(str(text_val)))
            except Exception:
                pass

    upload_date = entry.get("upload_date")
    if upload_date:
        try:
            return datetime.datetime.strptime(str(upload_date), "%Y%m%d")
        except Exception:
            pass
    return None


def prune_tracking_data(tracking_data: dict, cutoff: datetime.datetime):
    """
    清理历史记录：
    1) 仅保留 download_time 在 cutoff 之后的记录
    2) 每个 feed 仅保留“最新一条”记录
    """
    pruned = {}
    removed = 0
    kept = 0

    for feed_url, videos in tracking_data.items():
        if not isinstance(videos, dict):
            continue

        candidates = []
        for video_id, details in videos.items():
            if not isinstance(details, dict):
                removed += 1
                continue
            dt_obj = parse_tracking_time(details.get("download_time"))
            if dt_obj is None or dt_obj < cutoff:
                removed += 1
                continue
            candidates.append((dt_obj, video_id, details))

        if not candidates:
            continue

        latest = max(candidates, key=lambda x: x[0])
        _, video_id, details = latest
        pruned[feed_url] = {video_id: details}
        kept += 1
        removed += max(0, len(candidates) - 1)

    return pruned, kept, removed


def _safe_rel_from_cwd(path_str: str) -> str | None:
    """将任意路径归一化为相对 cwd 的路径字符串，用于状态文件对齐。"""
    if not path_str:
        return None
    try:
        abs_path = Path(path_str).resolve()
        return str(abs_path.relative_to(Path.cwd().resolve()))
    except Exception:
        return None


def build_tracked_file_set(tracking_data: dict) -> set[str]:
    tracked = set()
    for _feed, videos in tracking_data.items():
        if not isinstance(videos, dict):
            continue
        for _vid, details in videos.items():
            if not isinstance(details, dict):
                continue
            rel = _safe_rel_from_cwd(str(details.get("file_path", "")))
            if rel:
                tracked.add(rel)
    return tracked


def _is_under_directory(path_obj: Path, root_obj: Path) -> bool:
    try:
        path_obj.resolve().relative_to(root_obj.resolve())
        return True
    except Exception:
        return False


def remove_download_file_if_safe(path_str: str, root_dir: str) -> bool:
    """仅删除 root_dir 下的文件，防止误删外部路径。"""
    rel = _safe_rel_from_cwd(path_str)
    if not rel:
        return False
    target = Path(rel).resolve()
    root = Path(root_dir).resolve()
    if not _is_under_directory(target, root):
        logging.warning(f"跳过删除（不在下载目录内）: {target}")
        return False
    if not target.exists() or not target.is_file():
        return False
    try:
        target.unlink()
        logging.info(f"已清理旧下载文件: {target}")
        return True
    except Exception as e:
        logging.warning(f"删除文件失败 {target}: {e}")
        return False


def cleanup_orphan_download_files(download_dir: str, tracked_rel_paths: set[str], cutoff: datetime.datetime):
    """
    清理孤儿下载文件：
    - 文件位于 download_dir
    - 不在 tracking 记录中
    - 且 mtime 早于 cutoff（3 天前）
    """
    root = Path(download_dir).resolve()
    if not root.exists():
        return {"removed": 0, "kept_recent": 0, "errors": 0}

    removable_exts = {".mp3", ".m4a", ".webm", ".wav", ".flac", ".aac", ".ogg", ".opus"}
    removed = 0
    kept_recent = 0
    errors = 0

    for file_obj in root.rglob("*"):
        if not file_obj.is_file():
            continue
        if file_obj.suffix.lower() not in removable_exts:
            continue
        rel = _safe_rel_from_cwd(str(file_obj))
        if not rel:
            continue
        if rel in tracked_rel_paths:
            continue
        try:
            mtime = datetime.datetime.fromtimestamp(file_obj.stat().st_mtime)
        except Exception:
            errors += 1
            continue
        if mtime >= cutoff:
            kept_recent += 1
            continue
        try:
            file_obj.unlink()
            removed += 1
        except Exception:
            errors += 1

    return {"removed": removed, "kept_recent": kept_recent, "errors": errors}

# =============================================================================
# RSS 解析
# =============================================================================

def get_videos_from_rss(feed_url: str, max_entries: int = DEFAULT_MAX_ENTRIES) -> list:
    """
    从 RSS Feed 获取最新的多个视频条目。
    max_entries 防止单次处理过多；可在 channels.yaml 的频道条目里单独配置。
    失败时返回空列表（调用方可触发 YouTube Data API 回退）。
    """
    try:
        logging.info(f"正在解析 Feed: {feed_url}")
        feed = feedparser.parse(feed_url)
        if feed.bozo:
            logging.warning(
                f"Feed '{feed_url}' 可能格式不佳。"
                f"Bozo 异常: {feed.bozo_exception}"
            )
        if not feed.entries:
            logging.warning(f"Feed '{feed_url}' 中没有找到任何条目。")
            return []
        entries = feed.entries[:max_entries]
        logging.info(f"从 '{feed_url}' 获取到 {len(entries)} 个条目")
        return entries
    except Exception as e:
        logging.error(f"获取或解析 Feed '{feed_url}' 失败: {e}")
        return []

# =============================================================================
# YouTube Data API 回退（RSS 失败时使用）
# =============================================================================

def _extract_channel_id_from_feed_url(feed_url: str) -> str | None:
    """从 YouTube RSS feed URL 中提取 channel_id 参数。"""
    try:
        qs = parse_qs(urlparse(feed_url).query)
        ids = qs.get("channel_id")
        if ids and ids[0].startswith("UC"):
            return ids[0]
    except Exception:
        pass
    return None


def fetch_latest_video_from_api(
    channel_id: str,
    api_key: str,
    timeout: int = 15,
) -> dict | None:
    """
    使用 YouTube Data API v3 查询频道最新上传视频。
    两步：channels.list → playlistItems.list。
    成功返回 {'video_id', 'title', 'published_at', 'url'}，失败返回 None。
    """
    base = "https://www.googleapis.com/youtube/v3"
    try:
        # Step 1: 获取 uploads 播放列表 ID
        r = requests.get(
            f"{base}/channels",
            params={"part": "contentDetails", "id": channel_id, "key": api_key},
            timeout=timeout,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            logging.warning(f"YouTube Data API: 未找到频道 {channel_id}")
            return None
        uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # Step 2: 取播放列表第一条（最新上传）
        r2 = requests.get(
            f"{base}/playlistItems",
            params={
                "part": "snippet,contentDetails",
                "playlistId": uploads_id,
                "maxResults": 1,
                "key": api_key,
            },
            timeout=timeout,
        )
        r2.raise_for_status()
        video_items = r2.json().get("items", [])
        if not video_items:
            logging.warning(f"YouTube Data API: 频道 {channel_id} 上传列表为空")
            return None

        snippet = video_items[0]["snippet"]
        video_id = snippet["resourceId"]["videoId"]
        return {
            "video_id":     video_id,
            "title":        snippet.get("title", "无标题"),
            "published_at": snippet.get("publishedAt"),
            "url":          f"https://www.youtube.com/watch?v={video_id}",
        }
    except requests.RequestException as e:
        logging.error(f"YouTube Data API 请求失败 (channel={channel_id}): {e}")
        return None
    except Exception as e:
        logging.error(f"YouTube Data API 回退异常 (channel={channel_id}): {e}")
        return None

# =============================================================================
# 视频 ID 提取
# =============================================================================

def extract_video_id(url: str) -> str:
    """
    从常见 YouTube 链接中提取视频 ID（watch / youtu.be / shorts / embed）。
    提取失败时返回原始 URL 作为备用唯一标识。
    """
    try:
        parsed_url = urlparse(url)
        netloc = parsed_url.netloc.lower().replace("www.", "")

        if netloc == "youtu.be":
            path_part = parsed_url.path.strip("/")
            if path_part:
                return path_part.split("/")[0]

        if netloc in ("youtube.com", "m.youtube.com"):
            query_params = parse_qs(parsed_url.query)
            if "v" in query_params and query_params["v"]:
                return query_params["v"][0]

            path_parts = [p for p in parsed_url.path.split("/") if p]
            if len(path_parts) >= 2 and path_parts[0] in ("shorts", "embed", "v"):
                return path_parts[1]

        logging.warning(f"无法从 URL '{url}' 提取标准视频 ID，将使用完整 URL。")
        return url
    except Exception as e:
        logging.error(f"提取视频 ID 时出错 ({url}): {e}")
        return url

# =============================================================================
# 下载核心
# =============================================================================

def download_video(
    video_url: str,
    video_id: str,
    download_dir: str,
    channel_name: str,
) -> str | None:
    """
    使用 yt-dlp 下载音频，带超时和重试。
    成功返回下载文件的相对路径，失败返回 None。
    """
    logging.info(f"准备下载音频: {video_url} (ID: {video_id})")

    safe_channel_name = sanitize_filename(channel_name)
    channel_download_dir = os.path.join(download_dir, safe_channel_name)
    os.makedirs(channel_download_dir, exist_ok=True)

    output_template = '%(upload_date)s - %(title)s [%(id)s].%(ext)s'

    command = [
        YT_DLP_PATH,
        '--ignore-config',
        '--encoding', 'utf-8',
        '--paths', channel_download_dir,
        '-o', output_template,
        '-x', '--audio-format', AUDIO_FORMAT,
        '--print', 'after_move:filepath',
        '--concurrent-fragments', '4',
        '--no-warnings',
        '--progress',
    ]

    # Cookies（由环境变量 YTDLP_USE_COOKIES=1 启用）
    if os.getenv('YTDLP_USE_COOKIES', '0') == '1':
        if os.path.exists(COOKIES_PATH):
            command += ['--cookies', COOKIES_PATH]
        else:
            logging.warning(f"Cookies 文件不存在: {COOKIES_PATH}，将不使用 cookies 下载。")

    command.append(video_url)
    logging.info(f"执行命令: {' '.join(command)}")

    download_stdout = ""

    for attempt in range(MAX_DOWNLOAD_RETRIES):
        try:
            logging.info(f"开始下载 (尝试 {attempt + 1}/{MAX_DOWNLOAD_RETRIES})...")
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=DOWNLOAD_TIMEOUT,
            )
            download_stdout = result.stdout or ""
            logging.debug(f"yt-dlp 输出:\n{result.stdout}")
            break  # 下载成功，跳出重试循环

        except subprocess.TimeoutExpired:
            logging.error(
                f"yt-dlp 超时 (尝试 {attempt + 1}/{MAX_DOWNLOAD_RETRIES}): "
                f"超过 {DOWNLOAD_TIMEOUT} 秒"
            )
            if attempt < MAX_DOWNLOAD_RETRIES - 1:
                wait = (attempt + 1) * RETRY_BASE_DELAY
                logging.info(f"等待 {wait} 秒后重试...")
                time.sleep(wait)
            else:
                logging.error("达到最大重试次数，放弃下载。")
                return None

        except subprocess.CalledProcessError as e:
            logging.error(
                f"yt-dlp 错误 (尝试 {attempt + 1}/{MAX_DOWNLOAD_RETRIES}): "
                f"返回码 {e.returncode}"
            )
            logging.error(f"yt-dlp 错误输出:\n{e.stderr}")

            # 永久性错误：无论重试多少次都不会成功，直接放弃
            if is_permanent_error(e.stderr):
                logging.warning(
                    f"检测到永久性错误（视频不可用/首映未开始等），跳过该视频: {video_url}"
                )
                return None

            # Cookie 错误：尝试刷新一次（仅第一次失败时）
            if is_cookie_error(e.stderr) and attempt == 0:
                logging.warning("检测到 Cookie 可能已过期，尝试从浏览器刷新...")
                if refresh_cookies(COOKIES_PATH, video_url):
                    logging.info("Cookie 刷新成功，立即重试...")
                    continue  # 不等待，直接重试
                else:
                    logging.error("=" * 60)
                    logging.error("Cookie 刷新失败，请手动执行以下命令：")
                    logging.error(
                        f"  yt-dlp --cookies-from-browser chrome "
                        f"--cookies {COOKIES_PATH} --skip-download \"{video_url}\""
                    )
                    logging.error(
                        f"  # Firefox: yt-dlp --cookies-from-browser firefox "
                        f"--cookies {COOKIES_PATH} --skip-download \"{video_url}\""
                    )
                    logging.error("提示：确保浏览器已登录 YouTube")
                    logging.error("=" * 60)

            if attempt < MAX_DOWNLOAD_RETRIES - 1:
                wait = (attempt + 1) * RETRY_BASE_DELAY
                logging.info(f"等待 {wait} 秒后重试...")
                time.sleep(wait)
            else:
                logging.error("达到最大重试次数，放弃下载。")
                return None

        except FileNotFoundError:
            logging.error(
                f"未找到 yt-dlp 命令。请确认 '{YT_DLP_PATH}' 已安装并在 PATH 中。"
            )
            return None

        except Exception as e:
            logging.error(f"下载视频时发生未知错误 ({video_url}): {e}")
            return None

    # 下载循环结束后，验证文件是否真实存在
    try:
        downloaded_filepath = None

        # 优先使用 yt-dlp --print 输出的最终文件路径
        for line in reversed(download_stdout.splitlines()):
            candidate = line.strip()
            if candidate and os.path.exists(candidate):
                downloaded_filepath = candidate
                break

        # 回退：按 video_id 在目录中查找
        for filename in os.listdir(channel_download_dir):
            if downloaded_filepath:
                break
            if video_id in filename:
                downloaded_filepath = os.path.join(channel_download_dir, filename)
                break

        if downloaded_filepath and os.path.exists(downloaded_filepath):
            relative_filepath = os.path.relpath(downloaded_filepath, start=os.getcwd())
            logging.info(f"音频下载并验证成功: {video_id} → {relative_filepath}")
            return relative_filepath
        else:
            logging.error(
                f"yt-dlp 报告成功，但在 '{channel_download_dir}' "
                f"中未找到含 ID '{video_id}' 的文件。视频: {video_url}"
            )
            return None
    except Exception as e:
        logging.error(f"检查下载文件时出错: {e}")
        return None

# =============================================================================
# 主流程
# =============================================================================

def main():
    logging.info("--- 开始 YouTube RSS 视频下载任务 ---")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=HISTORY_TTL_DAYS)
    logging.info(
        f"运行策略：每频道仅处理最新 {LATEST_PER_CHANNEL} 条，"
        f"仅保留/处理最近 {HISTORY_TTL_DAYS} 天数据（cutoff={cutoff.isoformat(timespec='seconds')})"
    )

    # 加载频道列表
    channels = load_channels(CHANNELS_FILE)
    if not channels:
        logging.error("无法加载频道信息，脚本退出。")
        return

    # 加载历史记录
    # 结构: { feed_url: { video_id: { channel_name, file_path, download_time } } }
    tracking_data = load_tracking_data(TRACKING_FILE)
    tracking_data, kept_feed_count, removed_count = prune_tracking_data(tracking_data, cutoff)
    save_tracking_data(TRACKING_FILE, tracking_data)
    logging.info(
        f"历史记录已裁剪：保留 {kept_feed_count} 个频道最新记录，清理 {removed_count} 条过期/冗余记录。"
    )
    if CLEANUP_DOWNLOAD_FILES:
        tracked_paths = build_tracked_file_set(tracking_data)
        cleanup_stats = cleanup_orphan_download_files(DOWNLOAD_DIR, tracked_paths, cutoff)
        logging.info(
            f"下载目录清理完成：删除孤儿文件 {cleanup_stats['removed']}，"
            f"保留近3天孤儿文件 {cleanup_stats['kept_recent']}，"
            f"错误 {cleanup_stats['errors']}。"
        )

    # 遍历频道
    for channel in channels:
        channel_name = channel.get('name', '未知频道')
        feed_url     = channel.get('url')
        host         = channel.get('host')

        if not feed_url:
            logging.warning(f"跳过频道 '{channel_name}'，缺少 URL。")
            continue

        logging.info(f"\n--- 正在处理频道: {channel_name} ({feed_url}) ---")

        entries = get_videos_from_rss(feed_url, max_entries=LATEST_PER_CHANNEL)
        if not entries:
            # --- YouTube Data API 回退 ---
            # channel_id 优先从频道配置里取，其次从 RSS URL 中解析
            channel_id = channel.get("channel_id") or _extract_channel_id_from_feed_url(feed_url)
            if channel_id and YOUTUBE_DATA_API_KEY:
                logging.warning(
                    f"RSS 获取失败，尝试 YouTube Data API 回退 (channel_id={channel_id})…"
                )
                api_result = fetch_latest_video_from_api(channel_id, YOUTUBE_DATA_API_KEY)
                if api_result:
                    # 构造与 feedparser 条目兼容的仿真 entry
                    pub_str = api_result.get("published_at") or ""
                    try:
                        pub_dt = datetime.datetime.fromisoformat(
                            pub_str.replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                    except Exception:
                        pub_dt = None
                    entries = [{
                        "link":  api_result["url"],
                        "title": api_result["title"],
                        "published": pub_str,
                        "published_parsed": pub_dt.timetuple() if pub_dt else None,
                        "_source": "youtube_data_api",
                    }]
                    logging.info(
                        f"YouTube Data API 回退成功: 最新视频 '{api_result['title']}' "
                        f"(ID={api_result['video_id']})"
                    )
                else:
                    logging.error(
                        f"YouTube Data API 回退也失败，跳过频道 '{channel_name}'。"
                    )
                    continue
            elif not YOUTUBE_DATA_API_KEY:
                logging.warning(
                    f"未配置 YOUTUBE_DATA_API_KEY，无法回退，跳过频道 '{channel_name}'。"
                )
                continue
            else:
                logging.warning(
                    f"无法从 Feed URL 提取 channel_id，无法回退，跳过频道 '{channel_name}'。"
                )
                continue

        # 固定只看该频道 RSS 最新一条
        entry = entries[0]
        video_link  = entry.get('link')
        video_title = entry.get('title', '无标题')

        if not video_link:
            logging.warning(f"频道 '{channel_name}' 的最新条目缺少视频链接，跳过。")
            continue

        published_dt = get_entry_published_datetime(entry)
        if published_dt and published_dt < cutoff:
            logging.info(
                f"频道 '{channel_name}' 最新视频发布时间 {published_dt.isoformat(timespec='seconds')} "
                f"早于 cutoff，跳过。"
            )
            continue
        if not published_dt:
            logging.warning(f"频道 '{channel_name}' 最新视频无法解析发布时间，将继续处理。")

        video_id = extract_video_id(video_link)
        logging.info(f"检查最新视频: '{video_title}' (ID: {video_id})")

        # 跳过已下载的视频
        if video_id in tracking_data.get(feed_url, {}):
            logging.info(f"视频 '{video_id}' 已在记录中，跳过。")
            continue

        logging.info(f"发现新视频 (ID: {video_id})，准备下载...")
        relative_filepath = download_video(
            video_link, video_id, DOWNLOAD_DIR, channel_name
        )

        if relative_filepath:
            old_relative_filepath = None
            existing_feed_videos = tracking_data.get(feed_url, {})
            if isinstance(existing_feed_videos, dict) and existing_feed_videos:
                first_details = next(iter(existing_feed_videos.values()))
                if isinstance(first_details, dict):
                    old_relative_filepath = first_details.get("file_path")

            # 每个 feed 只保留“当前最新视频”一条记录（覆盖旧记录）
            tracking_data[feed_url] = {
                video_id: {
                    "channel_name":  channel_name,
                    "host":          host,
                    "file_path":     relative_filepath,
                    "download_time": datetime.datetime.now().isoformat(),
                    "published_time": published_dt.isoformat() if published_dt else None,
                }
            }
            save_tracking_data(TRACKING_FILE, tracking_data)
            logging.info(f"已记录最新视频: Feed={feed_url}, VideoID={video_id}")

            # 新视频落地后，立即删除该频道被替换的旧文件，避免持续堆积。
            if (
                CLEANUP_DOWNLOAD_FILES
                and old_relative_filepath
                and old_relative_filepath != relative_filepath
            ):
                remove_download_file_if_safe(old_relative_filepath, DOWNLOAD_DIR)
        else:
            logging.error(f"下载视频失败: {video_link}")

    logging.info("--- YouTube RSS 视频下载任务完成 ---")


if __name__ == "__main__":
    main()
