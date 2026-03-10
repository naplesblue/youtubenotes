import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- 项目根目录解析 ---
PROJECT_DIR          = Path(__file__).resolve().parent.parent.parent.parent

# --- 存储路径 ---
HISTORY_FILE         = str(PROJECT_DIR / 'data' / 'download_history.json')
AUDIO_DIR            = str(PROJECT_DIR / 'data' / 'audio')
ANALYSIS_RESULTS_DIR = str(PROJECT_DIR / 'data' / 'results')
ANALYSIS_LOG_FILE    = str(PROJECT_DIR / 'data' / 'analysis_log.json')
FFMPEG_PATH          = 'ffmpeg'
YTDLP_PATH           = os.getenv("YTDLP_PATH", "yt-dlp")

# --- 模型标识 ---
AUDIO_MODEL_NAME     = "FunAudioLLM/Fun-ASR-Nano-2512"
TEXT_MODEL_NAME      = os.getenv("LLM_MODEL_NAME", "qwen-plus")

# --- FunASR CLI 路径（跨包引用 encapsulate） ---
FUNASR_SCRIPT_PATH   = os.getenv(
    "FUNASR_SCRIPT_PATH",
    str(PROJECT_DIR / "src" / "ytbnotes" / "transcribe" / "funasr.py")
)

# --- 分析范围控制 ---
ANALYZER_ONLY_VIDEO  = os.getenv("ANALYZER_ONLY_VIDEO", "").strip()
try:
    ANALYZER_MAX_VIDEOS = int(os.getenv("ANALYZER_MAX_VIDEOS", "0") or "0")
except ValueError:
    ANALYZER_MAX_VIDEOS = 0
if ANALYZER_MAX_VIDEOS < 0:
    ANALYZER_MAX_VIDEOS = 0

# --- FunASR Worker 并发与超控配置 ---
def _get_worker_env(key: str, default: int, min_val: int = 0) -> int:
    try:
        val = int(os.getenv(key, str(default)) or str(default))
        return max(min_val, val)
    except ValueError:
        return max(min_val, default)

FUNASR_USE_WORKER               = os.getenv("FUNASR_USE_WORKER", "1").strip().lower() in {"1", "true", "yes", "on"}
FUNASR_WORKER_MAX_JOBS          = _get_worker_env("FUNASR_WORKER_MAX_JOBS", 6)
FUNASR_WORKER_IDLE_TIMEOUT      = _get_worker_env("FUNASR_WORKER_IDLE_TIMEOUT", 180)
FUNASR_WORKER_MAX_SECONDS       = _get_worker_env("FUNASR_WORKER_MAX_SECONDS", 1800)
FUNASR_WORKER_REQUEST_TIMEOUT   = _get_worker_env("FUNASR_WORKER_REQUEST_TIMEOUT", 1800, min_val=30)
FUNASR_WORKER_STARTUP_TIMEOUT   = _get_worker_env("FUNASR_WORKER_STARTUP_TIMEOUT", 900, min_val=30)
FUNASR_WORKER_MAX_RETRIES       = _get_worker_env("FUNASR_WORKER_MAX_RETRIES", 1)

_fallback = os.getenv("FUNASR_FALLBACK_TO_CLI_ON_WORKER_ERROR", "0").strip().lower()
FUNASR_FALLBACK_TO_CLI_ON_WORKER_ERROR = _fallback in {"1", "true", "yes", "on"}

# --- 文本来源路由 ---
_transcript_mode_raw = os.getenv("TRANSCRIPT_MODE", "auto").strip().lower()
TRANSCRIPT_MODE = _transcript_mode_raw if _transcript_mode_raw in {"auto", "subtitle", "asr"} else "auto"

# --- 字幕优先策略 ---
# 语言优先级：先英文人工字幕，再中文人工字幕
_subtitle_langs_raw = os.getenv("SUBTITLE_PREFERRED_LANGS", "en,en-us,en-gb,zh-hans,zh,zh-tw,zh-hant").strip()
SUBTITLE_PREFERRED_LANGS = [lang.strip().lower() for lang in _subtitle_langs_raw.split(",") if lang.strip()]

# 是否允许 YouTube 自动字幕（auto_captions）作为最后兜底，默认关闭（质量不稳定）
_allow_auto_raw = os.getenv("SUBTITLE_ALLOW_AUTO_CAPTIONS", "0").strip().lower()
SUBTITLE_ALLOW_AUTO_CAPTIONS = _allow_auto_raw in {"1", "true", "yes", "on"}

try:
    SUBTITLE_PROBE_TIMEOUT = int(os.getenv("SUBTITLE_PROBE_TIMEOUT", "120"))
except ValueError:
    SUBTITLE_PROBE_TIMEOUT = 120

try:
    SUBTITLE_DOWNLOAD_TIMEOUT = int(os.getenv("SUBTITLE_DOWNLOAD_TIMEOUT", "240"))
except ValueError:
    SUBTITLE_DOWNLOAD_TIMEOUT = 240

try:
    SUBTITLE_MIN_CHARS = int(os.getenv("SUBTITLE_MIN_CHARS", "1000"))
except ValueError:
    SUBTITLE_MIN_CHARS = 1000

try:
    SUBTITLE_MIN_CUES = int(os.getenv("SUBTITLE_MIN_CUES", "20"))
except ValueError:
    SUBTITLE_MIN_CUES = 20

try:
    SUBTITLE_MIN_COVERAGE = float(os.getenv("SUBTITLE_MIN_COVERAGE", "0.5"))
except ValueError:
    SUBTITLE_MIN_COVERAGE = 0.5

try:
    SUBTITLE_MIN_ENGLISH_RATIO = float(os.getenv("SUBTITLE_MIN_ENGLISH_RATIO", "0.8"))
except ValueError:
    SUBTITLE_MIN_ENGLISH_RATIO = 0.8

try:
    SUBTITLE_MIN_ZH_RATIO = float(os.getenv("SUBTITLE_MIN_ZH_RATIO", "0.2"))
except ValueError:
    SUBTITLE_MIN_ZH_RATIO = 0.2

# --- Qwen API ---
QWEN_BASE_URL = os.getenv(
    "LLM_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
