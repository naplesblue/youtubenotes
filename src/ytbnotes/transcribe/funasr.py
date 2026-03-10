#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
funasr_transcribe.py - 基于 Fun-ASR-Nano-2512 的本地语音识别 CLI

用法:
    python funasr_transcribe.py audio.mp3
    python funasr_transcribe.py audio.mp3 --output transcript.txt
    python funasr_transcribe.py audio.mp3 --format json
    python funasr_transcribe.py audio.mp3 --hotwords "NVDA AMD AAPL GOOG META"
    python funasr_transcribe.py audio.mp3 --hotwords-file hotwords.txt

首次运行会从 ModelScope 自动下载模型（约 600MB）。
模型缓存路径: ~/.cache/modelscope/hub/FunAudioLLM/Fun-ASR-Nano-2512/
可通过环境变量 FUNASR_MODEL_PY_PATH 指定 remote_code 的 model.py 位置。

依赖安装:
    pip install funasr --upgrade
    pip install modelscope
    pip install transformers
    pip install torch torchaudio
"""

import argparse
import contextlib
import importlib.util
import json
import logging
import os
import sys
import gc
import signal
import select
import time
from pathlib import Path

# 尽量关闭第三方下载/推理进度条，避免污染 worker 协议通道。
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stderr,   # 日志输出到 stderr，stdout 留给转录结果
)

# ── 模型配置 ────────────────────────────────────────────────────────────────

MODEL_ID = "FunAudioLLM/Fun-ASR-Nano-2512"
_ACTIVE_MODEL = None

# 财经场景默认热词：常见股票代码 + 指数名称
# 可通过 --hotwords 或 --hotwords-file 覆盖/追加
DEFAULT_FINANCE_HOTWORDS = [
    # 美股常见代码
    "NVDA", "AMD", "AAPL", "GOOG", "GOOGL", "META", "MSFT", "AMZN",
    "TSLA", "NFLX", "BABA", "JD", "PDD", "BIDU", "NIO", "XPEV",
    "SPY", "QQQ", "TQQQ", "SQQQ", "VIX",
    # 指数和术语
    "SPX", "NDX", "DJI", "纳斯达克", "标普", "道琼斯",
    # 财经术语（中文）
    "财报", "EPS", "PE", "估值", "做多", "做空", "期权", "Call", "Put",
]

# ── 工具函数 ────────────────────────────────────────────────────────────────

def ms_to_hms(ms: int) -> str:
    """毫秒转 [HH:MM:SS] 格式，与 audio_analyzer.py 的格式完全兼容"""
    total_s = max(0, ms) // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"[{h:02d}:{m:02d}:{s:02d}]"


def _normalize_model_py_path(raw_path: str) -> Path:
    """将文件/目录路径标准化为 model.py 文件路径。"""
    path = Path(raw_path).expanduser()
    if path.is_dir():
        path = path / "model.py"
    return path


def _builtin_nano_model_py() -> Path | None:
    """
    获取 funasr 包内置的 fun_asr_nano/model.py 路径（若存在）。
    该路径包含 ctc.py 与 tools/ 依赖，适合作为 remote_code。
    """
    try:
        spec = importlib.util.find_spec("funasr.models.fun_asr_nano")
        if spec and spec.submodule_search_locations:
            package_dir = Path(next(iter(spec.submodule_search_locations)))
            model_py = package_dir / "model.py"
            if model_py.exists():
                return model_py.resolve()
    except Exception:
        pass
    return None


def preload_remote_code(remote_code_path: str) -> None:
    """
    直接按文件路径执行 remote_code，避免 funasr 内部以固定模块名 `model`
    导入时发生同名冲突，导致注册表未写入。
    """
    path = Path(remote_code_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"remote_code 不存在: {path}")

    module_dir = str(path.parent)
    if module_dir not in sys.path:
        sys.path.append(module_dir)

    # 使用唯一模块名执行，避免与其他 `model` 模块冲突。
    module_name = f"_funasr_remote_{abs(hash(str(path)))}"
    if module_name in sys.modules:
        return

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 remote_code: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise


def resolve_model_py() -> str:
    """
    Fun-ASR-Nano-2512 需要 remote_code 指向 model.py。
    优先级:
      1) 环境变量 FUNASR_MODEL_PY_PATH（可为文件路径或目录路径）
      2) 默认 ./model.py
      3) ModelScope 缓存目录中的 model.py
      4) funasr 包内置的 fun_asr_nano/model.py
    返回 model.py 的绝对路径字符串。
    """
    configured_path = os.getenv("FUNASR_MODEL_PY_PATH", "").strip()
    searched: list[Path] = []

    if configured_path:
        configured_model_py = _normalize_model_py_path(configured_path)
        searched.append(configured_model_py)
        if configured_model_py.exists():
            logging.info(f"使用 FUNASR_MODEL_PY_PATH: {configured_model_py}")
            return str(configured_model_py.resolve())
        logging.warning(f"FUNASR_MODEL_PY_PATH 不存在: {configured_model_py}")

    local_model_py = Path("./model.py")
    searched.append(local_model_py)
    if local_model_py.exists():
        logging.info(f"使用项目内 model.py: {local_model_py}")
        return str(local_model_py.resolve())

    # 优先检查显式配置的 MODELSCOPE_CACHE（若有）
    modelscope_cache_env = os.getenv("MODELSCOPE_CACHE", "").strip()
    if modelscope_cache_env:
        cache_model_py = (
            Path(modelscope_cache_env).expanduser() / "models" / MODEL_ID / "model.py"
        )
        searched.append(cache_model_py)
        if cache_model_py.exists():
            logging.info(f"使用 ModelScope 缓存 model.py: {cache_model_py}")
            return str(cache_model_py.resolve())

    # 默认缓存路径（modelscope 未设置时）
    default_cache_model_py = (
        Path.home() / ".cache" / "modelscope" / "hub" / "models" / MODEL_ID / "model.py"
    )
    searched.append(default_cache_model_py)
    if default_cache_model_py.exists():
        logging.info(f"使用默认缓存 model.py: {default_cache_model_py}")
        return str(default_cache_model_py.resolve())

    builtin_model_py = _builtin_nano_model_py()
    if builtin_model_py:
        logging.info(f"使用 funasr 内置 model.py: {builtin_model_py}")
        return str(builtin_model_py)

    searched_str = " | ".join(str(p) for p in searched)
    raise FileNotFoundError(
        f"未找到可用的 model.py。可设置 FUNASR_MODEL_PY_PATH。已搜索: {searched_str}"
    )


def resolve_model_dir() -> str | None:
    """
    解析本地模型目录（若存在则优先本地加载，避免重复触发 ModelScope 下载流程）。
    优先级:
      1) FUNASR_MODEL_DIR
      2) MODELSCOPE_CACHE/models/<MODEL_ID>
      3) ~/.cache/modelscope/hub/models/<MODEL_ID>
    """
    configured_dir = os.getenv("FUNASR_MODEL_DIR", "").strip()
    if configured_dir:
        path = Path(configured_dir).expanduser()
        if path.is_dir():
            return str(path.resolve())
        logging.warning(f"FUNASR_MODEL_DIR 不是有效目录: {path}")

    modelscope_cache_env = os.getenv("MODELSCOPE_CACHE", "").strip()
    if modelscope_cache_env:
        env_cache_model_dir = Path(modelscope_cache_env).expanduser() / "models" / MODEL_ID
        if env_cache_model_dir.is_dir():
            return str(env_cache_model_dir.resolve())

    default_model_dir = Path.home() / ".cache" / "modelscope" / "hub" / "models" / MODEL_ID
    if default_model_dir.is_dir():
        return str(default_model_dir.resolve())

    return None


def load_hotwords_from_file(filepath: str) -> list[str]:
    """从文件加载热词，每行一个，支持 # 注释"""
    words = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    words.append(line)
        logging.info(f"从文件加载了 {len(words)} 个热词: {filepath}")
    except FileNotFoundError:
        logging.error(f"热词文件不存在: {filepath}")
        sys.exit(1)
    return words


# ── 资源清理（异常/终止时释放内存）──────────────────────────────────────────────

def release_model_resources() -> None:
    """释放模型对象并尝试清理推理缓存，降低异常后残留内存占用。"""
    global _ACTIVE_MODEL
    if _ACTIVE_MODEL is not None:
        try:
            del _ACTIVE_MODEL
        except Exception:
            pass
        _ACTIVE_MODEL = None

    try:
        gc.collect()
    except Exception:
        pass

    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            empty_cache = getattr(torch.mps, "empty_cache", None)
            if callable(empty_cache):
                empty_cache()
    except Exception:
        pass


def _handle_termination(signum, frame):
    del frame
    try:
        sig_name = signal.Signals(signum).name
    except Exception:
        sig_name = str(signum)
    logging.warning(f"收到终止信号 {sig_name}，正在释放模型资源...")
    release_model_resources()
    raise SystemExit(128 + int(signum))


def register_signal_handlers() -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_termination)
        except Exception:
            pass


# ── 模型加载 ────────────────────────────────────────────────────────────────

def load_model():
    """
    加载 Fun-ASR-Nano-2512 + fsmn-vad。
    首次运行会自动从 ModelScope 下载模型（约 600MB）。
    device 自动检测：Apple Silicon 使用 mps，其他 CPU。
    """
    try:
        import transformers  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "缺少依赖 transformers。请执行: python -m pip install transformers"
        ) from e
    try:
        import tiktoken  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "缺少依赖 tiktoken。请执行: python -m pip install tiktoken"
        ) from e

    from funasr import AutoModel
    from funasr.register import tables
    remote_code_path = resolve_model_py()
    model_source = resolve_model_dir() or MODEL_ID
    if model_source != MODEL_ID:
        logging.info(f"使用本地模型目录: {model_source}")
    else:
        logging.info(f"使用模型 ID 加载: {MODEL_ID}")
    logging.info(f"remote_code 路径: {remote_code_path}")
    preload_remote_code(remote_code_path)
    if tables.model_classes.get("FunASRNano") is None:
        raise RuntimeError(
            "remote_code 已加载但 FunASRNano 仍未注册，请检查 FUNASR_MODEL_PY_PATH 是否指向正确 model.py"
        )

    # 设备选择策略（稳定优先）：
    # - 默认 FUNASR_DEVICE=cpu，降低统一内存占用峰值
    # - 可显式设置 FUNASR_DEVICE=auto/mps/cuda:0
    import torch
    configured_device = os.getenv("FUNASR_DEVICE", "cpu").strip().lower()
    if configured_device in {"", "auto"}:
        if torch.backends.mps.is_available():
            device = "mps"
            logging.info("FUNASR_DEVICE=auto，检测到 MPS，将尝试使用 MPS。")
        else:
            device = "cpu"
            logging.info("FUNASR_DEVICE=auto，未检测到 MPS，使用 CPU。")
    elif configured_device.startswith("cuda"):
        if torch.cuda.is_available():
            device = configured_device
            logging.info(f"使用 CUDA 设备: {device}")
        else:
            logging.warning(f"请求的设备 {configured_device} 不可用，回退 CPU。")
            device = "cpu"
    elif configured_device == "mps":
        if torch.backends.mps.is_available():
            device = "mps"
            logging.info("使用 MPS 设备。")
        else:
            logging.warning("请求 MPS 但不可用，回退 CPU。")
            device = "cpu"
    else:
        device = "cpu"
        logging.info(f"使用设备: {device}")

    logging.info(f"正在加载模型 {MODEL_ID}（首次运行需下载约 600MB）...")

    # 默认关闭 VAD，减少额外模型占用；可通过 FUNASR_ENABLE_VAD=1 打开
    use_vad_default = os.getenv("FUNASR_ENABLE_VAD", "0").strip().lower() in {"1", "true", "yes", "on"}

    def _build(device_name: str, use_vad: bool):
        kwargs = {
            "model": model_source,
            "trust_remote_code": True,        # Nano 模型必须，含自定义代码
            "remote_code": remote_code_path,  # 初始化阶段传入，确保 FunASRNano 被注册
            "device": device_name,
            "hub": "ms",                      # 从 ModelScope 下载（国内更快）
            "check_latest": False,            # 使用本地缓存，避免每次联网检查
            "disable_update": True,           # 避免每次更新探测
            "log_level": "ERROR",             # 抑制 FunASR 内部冗余日志
        }
        if use_vad:
            kwargs["vad_model"] = "fsmn-vad"
            kwargs["vad_kwargs"] = {"max_single_segment_time": 30000}
        return AutoModel(**kwargs)

    build_plan = []
    build_plan.append((device, use_vad_default))
    if use_vad_default:
        build_plan.append((device, False))
    if device != "cpu":
        build_plan.append(("cpu", use_vad_default))
        if use_vad_default:
            build_plan.append(("cpu", False))

    last_err = None
    model = None
    dedup = set()
    for dev_name, use_vad in build_plan:
        key = (dev_name, use_vad)
        if key in dedup:
            continue
        dedup.add(key)
        try:
            logging.info(f"尝试加载模型: device={dev_name}, vad={use_vad}")
            model = _build(device_name=dev_name, use_vad=use_vad)
            break
        except Exception as e:
            last_err = e
            logging.warning(f"模型加载失败: device={dev_name}, vad={use_vad}, err={e}")
            continue
    if model is None:
        raise RuntimeError(f"模型加载失败，已尝试 {len(dedup)} 种组合，最后错误: {last_err}")

    logging.info("模型加载完成。")
    global _ACTIVE_MODEL
    _ACTIVE_MODEL = model
    setattr(model, "_remote_code_path", remote_code_path)
    return model


# ── 核心转录 ────────────────────────────────────────────────────────────────

def transcribe(model, audio_path: str, hotwords: list[str]) -> list[dict]:
    """
    执行转录，返回句子列表。
    每个句子格式:
        {"text": "今天 NVDA 上涨了。", "start": 1230, "end": 4560}
        start/end 单位：毫秒
    """
    logging.info(f"开始转录: {audio_path}")
    if hotwords:
        logging.info(f"使用热词 ({len(hotwords)} 个): {hotwords[:10]}{'...' if len(hotwords) > 10 else ''}")

    # remote_code 需要指向本地 model.py
    remote_code_path = getattr(model, "_remote_code_path", None)
    if not remote_code_path:
        remote_code_path = resolve_model_py()

    def _generate(enable_sentence_timestamp: bool):
        return model.generate(
            input=[audio_path],
            cache={},
            batch_size=1,
            hotwords=hotwords if hotwords else [],
            language="auto",                 # 自动检测，支持中英文混合
            itn=True,                        # 逆文本正则化：数字/单位格式化
            sentence_timestamp=enable_sentence_timestamp,
            remote_code=remote_code_path,
        )

    # 官方 README 当前未强调 sentence_timestamp 的稳定支持；
    # 默认关闭以提升稳定性，需显式开启时再尝试。
    sentence_ts_mode = os.getenv("FUNASR_SENTENCE_TIMESTAMP", "0").strip().lower()
    force_disable_sentence_ts = sentence_ts_mode in {"0", "false", "no", "off"}
    force_enable_sentence_ts = sentence_ts_mode in {"1", "true", "yes", "on"}

    if force_disable_sentence_ts:
        logging.info("FUNASR_SENTENCE_TIMESTAMP=0，使用 sentence_timestamp=False。")
        result = _generate(False)
    elif force_enable_sentence_ts:
        logging.info("FUNASR_SENTENCE_TIMESTAMP=1，使用 sentence_timestamp=True。")
        result = _generate(True)
    else:
        logging.info("FUNASR_SENTENCE_TIMESTAMP=auto，先尝试 True，失败后降级 False。")
        try:
            result = _generate(True)
        except Exception as e:
            err_detail = f"{type(e).__name__}: {e!r}"
            logging.warning(
                "sentence_timestamp=True 转录失败（%s），降级为 sentence_timestamp=False 重试...",
                err_detail,
            )
            result = _generate(False)

    sentences = []

    if result is None:
        logging.error("FunASR 返回空结果(None)")
        return sentences

    # 兼容不同 funasr 版本返回结构：
    # 1) list[dict]  2) dict
    raw = None
    if isinstance(result, dict):
        raw = result
    elif isinstance(result, (list, tuple)):
        if len(result) == 0:
            logging.error("FunASR 返回空列表结果")
            return sentences
        first = result[0]
        if isinstance(first, dict):
            raw = first
        else:
            logging.error(f"FunASR 返回了不支持的列表元素类型: {type(first).__name__}")
            return sentences
    else:
        logging.error(f"FunASR 返回了不支持的结果类型: {type(result).__name__}")
        return sentences

    # Fun-ASR-Nano-2512 返回结构示例：
    # {
    #   "text": "完整文本",
    #   "sentence_info": [
    #       {"text": "句子1", "start": 0, "end": 2340},
    #       {"text": "句子2", "start": 2500, "end": 5100},
    #   ]
    # }
    sentence_info = raw.get("sentence_info", [])
    if isinstance(sentence_info, dict):
        sentence_info = list(sentence_info.values())
    elif not isinstance(sentence_info, list):
        sentence_info = []

    if sentence_info:
        for seg in sentence_info:
            text = seg.get("text", "").strip()
            if not text:
                continue
            sentences.append({
                "text": text,
                "start": int(seg.get("start", 0)),
                "end": int(seg.get("end", 0)),
            })
        logging.info(f"转录完成，共 {len(sentences)} 个句子。")
    else:
        # 降级：整段作为一条（无时间戳）
        logging.warning("未获取到 sentence_info，降级为整段输出（无时间戳）。")
        full_text = str(raw.get("text", "") or "").strip()
        if full_text:
            sentences.append({"text": full_text, "start": 0, "end": 0})

    return sentences


# ── 长音频分片转录 ──────────────────────────────────────────────────────────

def get_audio_duration(audio_path: str) -> float:
    """使用 ffprobe 获取音频时长（秒），失败返回 0.0。"""
    import subprocess as _sp
    try:
        result = _sp.run(
            [
                "ffprobe", "-i", audio_path,
                "-show_entries", "format=duration",
                "-v", "quiet", "-of", "csv=p=0",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except FileNotFoundError:
        logging.warning("ffprobe 未安装，无法获取音频时长，将跳过分片直接处理。")
    except Exception as e:
        logging.warning(f"ffprobe 获取时长失败: {e}")
    return 0.0


def split_audio_chunks(
    audio_path: str, chunk_seconds: int = 60
) -> list[tuple[str, float]]:
    """
    使用 ffmpeg 将长音频切成固定时长的片段。
    返回 [(chunk_file_path, offset_seconds), ...]。
    所有 chunk 文件存放在临时目录中，调用者负责清理。
    """
    import subprocess as _sp
    import tempfile
    import shutil

    duration = get_audio_duration(audio_path)
    if duration <= 0:
        logging.info("无法获取时长，跳过分片。")
        return [(audio_path, 0.0)]
    # 如果只比阈值长一点点（<30s），不分片，避免生成极短尾片段
    if duration <= chunk_seconds + 30:
        logging.info(f"音频时长 {duration:.0f}s 未超过阈值 ({chunk_seconds}+30s)，直接处理。")
        return [(audio_path, 0.0)]

    suffix = Path(audio_path).suffix or ".mp3"
    temp_dir = tempfile.mkdtemp(prefix="funasr_chunks_")
    chunks: list[tuple[str, float]] = []

    offset = 0.0
    chunk_idx = 0
    while offset < duration:
        chunk_path = os.path.join(temp_dir, f"chunk_{chunk_idx:04d}{suffix}")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(offset),
            "-i", audio_path,
            "-t", str(chunk_seconds),
            "-vn",                    # 丢弃视频流
            "-acodec", "copy",        # 不重新编码，极快
            chunk_path,
        ]
        try:
            _sp.run(cmd, capture_output=True, text=True, timeout=60, check=True)
            if Path(chunk_path).exists() and Path(chunk_path).stat().st_size > 0:
                chunks.append((chunk_path, offset))
                logging.info(
                    f"  分片 {chunk_idx}: offset={offset:.0f}s, "
                    f"file={Path(chunk_path).name}"
                )
                chunk_idx += 1
            else:
                logging.warning(f"分片文件为空: {chunk_path}")
                break
        except FileNotFoundError:
            logging.error("ffmpeg 未安装，无法分片。")
            break
        except Exception as e:
            logging.warning(f"ffmpeg 分片失败 (offset={offset:.0f}s): {e}")
            break
        offset += chunk_seconds

    if not chunks:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logging.warning("分片失败，回退为直接处理整段音频。")
        return [(audio_path, 0.0)]

    logging.info(f"音频已分为 {len(chunks)} 个片段（每段≤{chunk_seconds}s）。")
    return chunks


def transcribe_long_audio(
    model,
    audio_path: str,
    hotwords: list[str],
    chunk_seconds: int = 60,
) -> list[dict]:
    """
    对长音频自动分片转录。
    - 短音频（≤ chunk_seconds）直接调用 transcribe()
    - 长音频使用 ffmpeg 分片 → 逐段 transcribe() → 调整时间戳 → 合并
    """
    import shutil

    chunks = split_audio_chunks(audio_path, chunk_seconds)

    # 单片段（含 fallback）直接转录
    if len(chunks) == 1 and chunks[0][0] == audio_path:
        return transcribe(model, audio_path, hotwords)

    all_sentences: list[dict] = []
    temp_dir = str(Path(chunks[0][0]).parent) if chunks[0][0] != audio_path else None

    try:
        for i, (chunk_path, offset_s) in enumerate(chunks):
            offset_ms = int(offset_s * 1000)
            logging.info(f"▶ 转录片段 {i + 1}/{len(chunks)} (offset={offset_s:.0f}s)")
            try:
                sentences = transcribe(model, chunk_path, hotwords)
            except Exception as e:
                logging.error(f"片段 {i + 1} 转录失败: {e}")
                continue

            # 调整时间戳：加上片段在原始音频中的偏移量
            for s in sentences:
                s["start"] += offset_ms
                s["end"] += offset_ms
            all_sentences.extend(sentences)

            logging.info(
                f"  片段 {i + 1}/{len(chunks)} 完成，{len(sentences)} 句。"
            )
            # 每个片段转录后释放中间张量，控制内存峰值
            gc.collect()
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logging.info("已清理临时分片文件。")

    logging.info(f"分片转录全部完成，共 {len(all_sentences)} 句。")
    return all_sentences


# ── 输出格式化 ───────────────────────────────────────────────────────────────

def sentences_to_text(sentences: list[dict]) -> str:
    """
    转换为带时间戳的纯文本。
    格式: [HH:MM:SS] 句子内容
    与 audio_analyzer.py 中 process_text_tasks 的输入格式完全兼容。
    """
    lines = []
    for seg in sentences:
        ts = ms_to_hms(seg["start"])
        lines.append(f"{ts} {seg['text']}")
    return "\n".join(lines)


def sentences_to_json(sentences: list[dict]) -> str:
    """转换为结构化 JSON，保留毫秒级时间戳"""
    return json.dumps(sentences, ensure_ascii=False, indent=2)


# ── CLI 入口 ─────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fun-ASR-Nano-2512 本地语音识别 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
输出格式:
  text  (默认) 带时间戳的纯文本，格式 [HH:MM:SS] 句子
               与 audio_analyzer.py 的 process_text_tasks 输入格式兼容
  json         结构化 JSON，包含毫秒级 start/end 时间戳

热词使用示例:
  # 命令行直接指定（空格分隔）
  python funasr_transcribe.py audio.mp3 --hotwords "NVDA AMD AAPL"

  # 从文件加载（每行一个词，# 开头为注释）
  python funasr_transcribe.py audio.mp3 --hotwords-file hotwords.txt

  # 不使用任何热词（包括默认财经热词）
  python funasr_transcribe.py audio.mp3 --no-default-hotwords

热词文件格式示例 (hotwords.txt):
  # 科技股
  NVDA
  AMD
  AAPL
  # 中概股
  BABA
  BIDU
        """,
    )
    parser.add_argument(
        "audio",
        nargs="?",
        help="音频文件路径（支持 mp3 / wav / m4a / flac / ogg）",
    )
    parser.add_argument(
        "--output", "-o",
        help="输出文件路径（默认输出到 stdout）",
    )
    parser.add_argument(
        "--format", "-f",
        choices=["text", "json"],
        default="text",
        help="输出格式（默认: text）",
    )
    parser.add_argument(
        "--hotwords",
        help="空格分隔的热词列表，例如: \"NVDA AMD AAPL\"",
    )
    parser.add_argument(
        "--hotwords-file",
        help="热词文件路径，每行一个热词，# 开头为注释",
    )
    parser.add_argument(
        "--no-default-hotwords",
        action="store_true",
        help="禁用内置的财经默认热词（仅使用 --hotwords 或 --hotwords-file 中的词）",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细日志（包括 FunASR 内部日志）",
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        help="以常驻 worker 模式运行：单次加载模型，批量处理多个音频请求",
    )
    parser.add_argument(
        "--worker-max-jobs",
        type=int,
        default=int(os.getenv("FUNASR_WORKER_MAX_JOBS", "0") or "0"),
        help="worker 最多处理任务数后自动退出（0 表示不限制）",
    )
    parser.add_argument(
        "--worker-idle-timeout",
        type=int,
        default=int(os.getenv("FUNASR_WORKER_IDLE_TIMEOUT", "180") or "180"),
        help="worker 空闲多少秒后自动退出（默认 180）",
    )
    parser.add_argument(
        "--worker-max-seconds",
        type=int,
        default=int(os.getenv("FUNASR_WORKER_MAX_SECONDS", "1800") or "1800"),
        help="worker 最长运行秒数后自动退出（默认 1800）",
    )
    parser.add_argument(
        "--worker-parent-pid",
        type=int,
        default=0,
        help="父进程 PID；若父进程退出，worker 自动结束（0 表示关闭该检查）",
    )
    return parser


def build_hotwords(args) -> list[str]:
    hotwords = [] if args.no_default_hotwords else list(DEFAULT_FINANCE_HOTWORDS)

    if args.hotwords:
        extra = [w.strip() for w in args.hotwords.split() if w.strip()]
        hotwords.extend(extra)
        logging.info(f"追加命令行热词 {len(extra)} 个")

    if args.hotwords_file:
        file_words = load_hotwords_from_file(args.hotwords_file)
        hotwords.extend(file_words)

    seen = set()
    return [w for w in hotwords if not (w in seen or seen.add(w))]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def run_worker(args) -> int:
    """常驻 worker：通过 stdin 接收 JSON 请求，通过 stdout 返回 JSON 响应。"""
    model = None
    base_hotwords = build_hotwords(args)

    max_jobs = max(0, args.worker_max_jobs)
    idle_timeout = max(0, args.worker_idle_timeout)
    max_seconds = max(0, args.worker_max_seconds)
    parent_pid = max(0, args.worker_parent_pid)
    request_timeout = 1.0

    if parent_pid > 0:
        logging.info(f"Worker 绑定父进程 PID={parent_pid}")
    if max_jobs > 0:
        logging.info(f"Worker 任务上限: {max_jobs}")
    if idle_timeout > 0:
        logging.info(f"Worker 空闲超时: {idle_timeout}s")
    if max_seconds > 0:
        logging.info(f"Worker 最长运行: {max_seconds}s")

    started_at = time.time()
    last_activity_at = started_at
    processed = 0
    exit_reason = "normal_exit"

    def _emit(payload: dict) -> None:
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    try:
        # worker 协议使用 stdout 传输 JSON；第三方库的普通 print 重定向到 stderr，
        # 避免污染协议通道。
        with contextlib.redirect_stdout(sys.stderr):
            model = load_model()
        _emit({"event": "ready"})

        while True:
            now = time.time()
            if parent_pid > 0 and not _pid_alive(parent_pid):
                exit_reason = f"parent_dead:{parent_pid}"
                break
            if max_seconds > 0 and (now - started_at) >= max_seconds:
                exit_reason = "max_seconds_reached"
                break
            if max_jobs > 0 and processed >= max_jobs:
                exit_reason = "max_jobs_reached"
                break
            if idle_timeout > 0 and (now - last_activity_at) >= idle_timeout:
                exit_reason = "idle_timeout"
                break

            ready, _, _ = select.select([sys.stdin], [], [], request_timeout)
            if not ready:
                continue

            line = sys.stdin.readline()
            if line == "":
                exit_reason = "stdin_closed"
                break
            line = line.strip()
            if not line:
                continue

            last_activity_at = time.time()

            try:
                req = json.loads(line)
            except json.JSONDecodeError as e:
                _emit({"ok": False, "error": f"invalid_json: {e}"})
                continue

            req_id = req.get("id")
            cmd = req.get("cmd", "transcribe")

            if cmd == "shutdown":
                _emit({"id": req_id, "ok": True, "event": "shutdown_ack"})
                exit_reason = "shutdown_command"
                break

            if cmd != "transcribe":
                _emit({"id": req_id, "ok": False, "error": f"unsupported_cmd: {cmd}"})
                continue

            audio_path = str(req.get("audio_path", "")).strip()
            if not audio_path:
                _emit({"id": req_id, "ok": False, "error": "missing_audio_path"})
                continue

            request_hotwords = req.get("hotwords")
            if isinstance(request_hotwords, list):
                hotwords = [str(x).strip() for x in request_hotwords if str(x).strip()]
            else:
                hotwords = base_hotwords

            chunk_seconds = int(os.getenv("FUNASR_CHUNK_SECONDS", "60") or "60")
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    sentences = transcribe_long_audio(model, audio_path, hotwords, chunk_seconds=chunk_seconds)
                if not sentences:
                    _emit({"id": req_id, "ok": False, "error": "empty_transcript"})
                    continue
                if args.format == "json":
                    transcript = sentences_to_json(sentences)
                else:
                    transcript = sentences_to_text(sentences)
                _emit({
                    "id": req_id,
                    "ok": True,
                    "transcript": transcript,
                    "sentence_count": len(sentences),
                })
                processed += 1
            except Exception as e:
                _emit({
                    "id": req_id,
                    "ok": False,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "error_repr": repr(e)[:500],
                })
            finally:
                # 每次任务后主动触发回收，降低长时间驻留的碎片化风险。
                try:
                    gc.collect()
                except Exception:
                    pass

        _emit({"event": "bye", "reason": exit_reason, "processed": processed})
        return 0
    finally:
        release_model_resources()


def main():
    register_signal_handlers()
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.worker:
        rc = run_worker(args)
        sys.exit(rc)

    if not args.audio:
        parser.error("audio 参数必填（worker 模式除外）")

    # ── 检查输入文件 ──
    audio_path = Path(args.audio).resolve()
    if not audio_path.exists():
        logging.error(f"音频文件不存在: {audio_path}")
        sys.exit(1)
    if audio_path.stat().st_size == 0:
        logging.error(f"音频文件为空: {audio_path}")
        sys.exit(1)

    # ── 构建热词列表 ──
    hotwords = build_hotwords(args)

    # ── 加载模型 ──
    model = None
    try:
        model = load_model()
    except Exception as e:
        logging.error(f"模型加载失败: {e}")
        sys.exit(1)

    try:
        # ── 执行转录 ──
        chunk_seconds = int(os.getenv("FUNASR_CHUNK_SECONDS", "60") or "60")
        sentences = transcribe_long_audio(model, str(audio_path), hotwords, chunk_seconds=chunk_seconds)
        if not sentences:
            logging.error("转录结果为空，请检查音频文件是否有效。")
            sys.exit(1)

        # ── 格式化输出 ──
        if args.format == "json":
            output_content = sentences_to_json(sentences)
        else:
            output_content = sentences_to_text(sentences)

        # ── 写入文件或 stdout ──
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(output_content, encoding="utf-8")
            logging.info(f"转录结果已保存: {out_path}")
        else:
            # 结果输出到 stdout，日志在 stderr，二者不干扰
            print(output_content)
    except Exception as e:
        logging.error(f"转录过程中发生错误: {e}", exc_info=args.verbose)
        sys.exit(1)
    finally:
        release_model_resources()


if __name__ == "__main__":
    main()
