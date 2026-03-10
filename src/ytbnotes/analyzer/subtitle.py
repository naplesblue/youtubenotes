import os
import re
import json
import time
import html
import logging
import tempfile
import subprocess
from pathlib import Path

from .config import (
    YTDLP_PATH,
    SUBTITLE_PROBE_TIMEOUT,
    SUBTITLE_DOWNLOAD_TIMEOUT,
    SUBTITLE_PREFERRED_LANGS,
    SUBTITLE_ALLOW_AUTO_CAPTIONS,
    SUBTITLE_MIN_CHARS,
    SUBTITLE_MIN_CUES,
    SUBTITLE_MIN_COVERAGE,
    SUBTITLE_MIN_ENGLISH_RATIO,
    SUBTITLE_MIN_ZH_RATIO,
)
from .utils import seconds_to_time_str


def _normalize_lang_tag(lang: str) -> str:
    return str(lang or "").strip().lower().replace("_", "-")


def _is_english_lang_tag(lang: str) -> bool:
    n = _normalize_lang_tag(lang)
    return n == "en" or n.startswith("en-")


def _is_chinese_lang_tag(lang: str) -> bool:
    n = _normalize_lang_tag(lang)
    return n in {"zh", "zh-hans", "zh-hant", "zh-cn", "zh-tw", "zh-hk", "zh-sg"} or n.startswith("zh-")


def _lang_family(lang: str) -> str:
    """返回语言族：'en', 'zh', 或 'other'。"""
    if _is_english_lang_tag(lang):
        return "en"
    if _is_chinese_lang_tag(lang):
        return "zh"
    return "other"


def _extract_json_from_text(text: str):
    """容忍 stdout 噪声，尝试抽取 JSON 主体。"""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None


def probe_subtitle(video_url: str) -> dict:
    """
    探测视频可用字幕，按 SUBTITLE_PREFERRED_LANGS 优先级选取最佳语言。
    支持英文和中文人工字幕；可选择 auto_captions 作为最后兜底。
    返回 dict: {ok, selected_lang, lang_family, manual_langs, automatic_langs, reason, source}
    """
    result = {
        "ok": False,
        "selected_lang": None,
        "lang_family": None,      # "en" / "zh" / "other"
        "manual_langs": [],
        "automatic_langs": [],
        "reason": None,
        "source": None,           # "manual" / "auto_caption"
    }

    command = [
        YTDLP_PATH,
        "--ignore-config",
        "--skip-download",
        "-J",
        "--no-warnings",
        video_url,
    ]
    if os.getenv("YTDLP_USE_COOKIES", "0").strip() == "1":
        cookies_path = os.getenv("YTDLP_COOKIES_PATH", "youtube_cookies.txt")
        if Path(cookies_path).exists():
            command += ["--cookies", cookies_path]

    try:
        p = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SUBTITLE_PROBE_TIMEOUT,
        )
        if p.returncode != 0:
            result["reason"] = f"yt-dlp_probe_failed:{(p.stderr or '').strip()[-200:]}"
            return result
        info = _extract_json_from_text(p.stdout)
        if not isinstance(info, dict):
            result["reason"] = "probe_json_parse_failed"
            return result
    except subprocess.TimeoutExpired:
        result["reason"] = "probe_timeout"
        return result
    except Exception as e:
        result["reason"] = f"probe_exception:{type(e).__name__}:{e}"
        return result

    subtitles = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    if isinstance(subtitles, dict):
        result["manual_langs"] = sorted([str(k) for k in subtitles.keys()])[:30]
    if isinstance(automatic, dict):
        result["automatic_langs"] = sorted([str(k) for k in automatic.keys()])[:30]

    # --- 第一轮：从 manual subtitles 中按优先级选取 ---
    selected_lang = None
    if isinstance(subtitles, dict) and subtitles:
        normalized_map = {_normalize_lang_tag(k): str(k) for k in subtitles.keys()}
        for preferred in SUBTITLE_PREFERRED_LANGS:
            n_pref = _normalize_lang_tag(preferred)
            if n_pref in normalized_map:
                selected_lang = normalized_map[n_pref]
                result["source"] = "manual"
                break
        # 备用：模式匹配（en-* 或 zh-*）
        if not selected_lang:
            # 先试英文通配
            for lang in subtitles.keys():
                if _is_english_lang_tag(lang):
                    selected_lang = str(lang)
                    result["source"] = "manual"
                    break
        if not selected_lang:
            # 再试中文通配
            for lang in subtitles.keys():
                if _is_chinese_lang_tag(lang):
                    selected_lang = str(lang)
                    result["source"] = "manual"
                    break

    # --- 第二轮（可选）：auto_captions 中找英文或中文 ---
    if not selected_lang and SUBTITLE_ALLOW_AUTO_CAPTIONS and isinstance(automatic, dict) and automatic:
        normalized_auto_map = {_normalize_lang_tag(k): str(k) for k in automatic.keys()}
        for preferred in SUBTITLE_PREFERRED_LANGS:
            n_pref = _normalize_lang_tag(preferred)
            if n_pref in normalized_auto_map:
                selected_lang = normalized_auto_map[n_pref]
                result["source"] = "auto_caption"
                break
        if not selected_lang:
            for lang in automatic.keys():
                if _is_english_lang_tag(lang) or _is_chinese_lang_tag(lang):
                    selected_lang = str(lang)
                    result["source"] = "auto_caption"
                    break

    if not selected_lang:
        result["reason"] = "no_suitable_subtitle_found"
        return result

    result["selected_lang"] = selected_lang
    result["lang_family"] = _lang_family(selected_lang)
    result["ok"] = True
    return result


# --- 保留旧名称为别名，防止其他地方直接引用 ---
def probe_manual_english_subtitle(video_url: str) -> dict:
    """向后兼容别名，内部委托给 probe_subtitle。"""
    return probe_subtitle(video_url)


def _vtt_time_to_seconds(raw: str):
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.replace(",", ".")
    parts = s.split(":")
    try:
        if len(parts) == 3:
            h = int(parts[0]); m = int(parts[1]); sec = float(parts[2])
        elif len(parts) == 2:
            h = 0; m = int(parts[0]); sec = float(parts[1])
        elif len(parts) == 1:
            h = 0; m = 0; sec = float(parts[0])
        else:
            return None
        return h * 3600 + m * 60 + sec
    except Exception:
        return None


def _clean_subtitle_text(line: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(line or ""))
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_vtt_cues(vtt_path: Path):
    try:
        lines = vtt_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        logging.warning(f"读取字幕文件失败: {vtt_path} ({e})")
        return []

    cues = []
    current_start = None
    current_end = None
    text_lines = []

    def flush():
        nonlocal current_start, current_end, text_lines
        if current_start is not None and current_end is not None:
            merged = _clean_subtitle_text(" ".join([x for x in text_lines if x]))
            if merged:
                cues.append({"start": current_start, "end": current_end, "text": merged})
        current_start = None
        current_end = None
        text_lines = []

    for raw_line in lines + [""]:
        line = str(raw_line).strip()
        if not line:
            flush()
            continue
        upper = line.upper()
        if upper.startswith("WEBVTT") or upper.startswith("NOTE") or upper.startswith("STYLE") or upper.startswith("REGION"):
            continue
        if line.isdigit():
            continue
        if "-->" in line:
            flush()
            parts = line.split("-->")
            start_sec = _vtt_time_to_seconds(parts[0].strip().split(" ")[0])
            end_sec = _vtt_time_to_seconds(parts[1].strip().split(" ")[0]) if len(parts) > 1 else None
            if start_sec is None or end_sec is None or end_sec <= start_sec:
                current_start = None
                current_end = None
                text_lines = []
                continue
            current_start = start_sec
            current_end = end_sec
            text_lines = []
            continue
        if current_start is not None:
            text_lines.append(line)

    deduped = []
    prev_text = None
    for cue in cues:
        text = cue["text"]
        if prev_text is not None and text == prev_text:
            continue
        deduped.append(cue)
        prev_text = text
    return deduped


def subtitle_quality_gate(cues: list[dict], full_text: str, lang_family: str = "en"):
    """
    检验字幕质量。
    lang_family: "en" 检查英文占比; "zh" 检查中文字符占比（跳过英文比例规则）。
    """
    metrics = {
        "cue_count": len(cues),
        "char_count": len(full_text),
        "coverage_ratio": 0.0,
        "lang_ratio": 0.0,
        "passed": False,
        "failed_rules": [],
    }
    if not cues:
        metrics["failed_rules"].append("no_cues")
        return metrics

    starts = [c["start"] for c in cues]
    ends = [c["end"] for c in cues]
    total_span = max(0.0, max(ends) - min(starts))
    spoken_span = sum(max(0.0, c["end"] - c["start"]) for c in cues)
    coverage_ratio = 0.0 if total_span <= 0 else min(1.0, spoken_span / total_span)
    metrics["coverage_ratio"] = round(coverage_ratio, 4)

    if metrics["char_count"] < SUBTITLE_MIN_CHARS:
        metrics["failed_rules"].append(f"char_count<{SUBTITLE_MIN_CHARS}")
    if metrics["cue_count"] < SUBTITLE_MIN_CUES:
        metrics["failed_rules"].append(f"cue_count<{SUBTITLE_MIN_CUES}")
    if coverage_ratio < SUBTITLE_MIN_COVERAGE:
        metrics["failed_rules"].append(f"coverage<{SUBTITLE_MIN_COVERAGE}")

    # 语言比例检查（取决于 lang_family）
    if lang_family == "zh":
        # 中文字幕：检查中文字符占比
        zh_chars = len(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", full_text))
        total_chars = len(full_text.replace(" ", ""))
        zh_ratio = zh_chars / max(total_chars, 1)
        metrics["lang_ratio"] = round(zh_ratio, 4)
        if zh_ratio < SUBTITLE_MIN_ZH_RATIO:
            metrics["failed_rules"].append(f"zh_ratio<{SUBTITLE_MIN_ZH_RATIO}")
    else:
        # 英文字幕（默认）：检查英文 token 占比
        tokens = re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]+", full_text)
        if tokens:
            english_tokens = sum(1 for t in tokens if re.fullmatch(r"[A-Za-z]+", t))
            english_ratio = english_tokens / len(tokens)
        else:
            english_ratio = 0.0
        metrics["lang_ratio"] = round(english_ratio, 4)
        if english_ratio < SUBTITLE_MIN_ENGLISH_RATIO:
            metrics["failed_rules"].append(f"english_ratio<{SUBTITLE_MIN_ENGLISH_RATIO}")

    metrics["passed"] = not metrics["failed_rules"]
    return metrics


def load_subtitle_transcript(video_url: str) -> dict:
    """
    下载并验证最佳可用字幕（英文或中文人工字幕，可选 auto_captions）。
    返回 dict: {ok, transcript, probe, quality, error, lang_family, source}
    """
    output = {
        "ok": False,
        "transcript": None,
        "probe": None,
        "quality": None,
        "error": None,
        "lang_family": None,
        "source": None,
    }
    probe = probe_subtitle(video_url)
    output["probe"] = probe
    if not probe.get("ok"):
        output["error"] = probe.get("reason") or "subtitle_probe_failed"
        return output

    selected_lang = probe.get("selected_lang")
    lang_fam = probe.get("lang_family", "en")
    subtitle_source = probe.get("source", "manual")
    output["lang_family"] = lang_fam
    output["source"] = subtitle_source

    # 选择下载命令：manual 用 --write-sub，auto_captions 用 --write-auto-sub
    sub_flag = "--write-sub" if subtitle_source == "manual" else "--write-auto-sub"

    with tempfile.TemporaryDirectory(prefix="subtitle_probe_") as tmp_dir:
        output_template = str(Path(tmp_dir) / "%(id)s")
        cmd = [
            YTDLP_PATH,
            "--ignore-config",
            "--skip-download",
            sub_flag,
            "--sub-langs", str(selected_lang),
            "--sub-format", "vtt",
            "--output", output_template,
            "--no-warnings",
            video_url,
        ]
        if os.getenv("YTDLP_USE_COOKIES", "0").strip() == "1":
            cookies_path = os.getenv("YTDLP_COOKIES_PATH", "youtube_cookies.txt")
            if Path(cookies_path).exists():
                cmd += ["--cookies", cookies_path]

        try:
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=SUBTITLE_DOWNLOAD_TIMEOUT,
            )
            if p.returncode != 0:
                output["error"] = f"subtitle_download_failed:{(p.stderr or '').strip()[-200:]}"
                return output
        except subprocess.TimeoutExpired:
            output["error"] = "subtitle_download_timeout"
            return output
        except Exception as e:
            output["error"] = f"subtitle_download_exception:{type(e).__name__}:{e}"
            return output

        vtt_files = list(Path(tmp_dir).rglob("*.vtt"))
        if not vtt_files:
            output["error"] = "subtitle_vtt_not_found"
            return output
        vtt_files.sort(key=lambda pth: ((str(selected_lang).lower() in pth.name.lower()), pth.stat().st_size), reverse=True)
        selected_vtt = vtt_files[0]

        cues = parse_vtt_cues(selected_vtt)
        if not cues:
            output["error"] = "subtitle_cues_empty"
            return output

        lines = []
        for cue in cues:
            ts = seconds_to_time_str(cue["start"]).split(".")[0]
            lines.append(f"[{ts}] {cue['text']}")
        transcript = "\n".join(lines).strip()
        if not transcript:
            output["error"] = "subtitle_transcript_empty"
            return output

        quality = subtitle_quality_gate(cues, " ".join(c["text"] for c in cues), lang_family=lang_fam)
        output["quality"] = quality
        if not quality.get("passed"):
            output["error"] = "subtitle_quality_gate_failed:" + ",".join(quality.get("failed_rules") or [])
            return output

        output["ok"] = True
        output["transcript"] = transcript
        return output


def load_manual_english_subtitle_transcript(video_url: str) -> dict:
    """向后兼容别名，内部委托给 load_subtitle_transcript。"""
    return load_subtitle_transcript(video_url)
