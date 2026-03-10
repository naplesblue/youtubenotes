import os
import re
import math
import json
import tempfile
import logging
import time
from pathlib import Path
from typing import TypedDict, Union, Any

# =============================================================================
# Result 类型定义
# =============================================================================

class Ok(TypedDict):
    ok: bool
    value: Any

class Err(TypedDict):
    ok: bool
    error: str
    error_type: str  # 'not_found', 'corrupt', 'permission', 'unknown'

Result = Union[Ok, Err]

def success(value: Any) -> Ok:
    return {'ok': True, 'value': value}

def failure(error: str, error_type: str = 'unknown') -> Err:
    return {'ok': False, 'error': error, 'error_type': error_type}

# =============================================================================
# 原子写入工具
# =============================================================================

def write_file_atomically(filepath, content, mode='w', encoding='utf-8'):
    """先写临时文件，再 os.replace 原子替换，防止写入中断导致文件损坏。"""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    temp_fd = None
    temp_path = None
    try:
        temp_fd, temp_path = tempfile.mkstemp(
            dir=filepath.parent,
            prefix=f'.tmp_{filepath.name}.',
            suffix='.tmp'
        )
        temp_path = Path(temp_path)
        if mode == 'w':
            with os.fdopen(temp_fd, 'w', encoding=encoding) as f:
                f.write(content)
            temp_fd = None
        elif mode == 'wb':
            with os.fdopen(temp_fd, 'wb') as f:
                f.write(content)
            temp_fd = None
        else:
            raise ValueError(f"Unsupported mode: {mode}")
        os.replace(temp_path, filepath)
        return True
    except Exception as e:
        logging.error(f"原子写入失败 {filepath}: {e}")
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except Exception:
                pass
        if temp_path and Path(temp_path).exists():
            try:
                Path(temp_path).unlink()
            except Exception:
                pass
        raise

# =============================================================================
# 输出解析
# =============================================================================

def parse_llm_output(text):
    """
    解析 LLM 输出文本，提取精炼文本、摘要行、点位 JSON、股票提及 JSON。
    """
    sections = {
        "transcript": None,
        "refined_text": "【精炼文本】部分未找到或为空。",
        "summary_lines": [],
        "price_levels_json": None,
        "mentioned_tickers_json": None,
        "people_mentioned": [],
        "key_points": [],
    }

    refined_pattern  = r"【精炼文本】\s*(.*?)\s*(?=(?:【关键信息摘要（含时间戳）】|$))"
    summary_pattern  = r"【关键信息摘要（含时间戳）】\s*(.*?)\s*(?=(?:【原子化点位数据 \(JSON\)】|【提及股票数据 \(JSON\)】|$))"
    json_pattern     = r"【原子化点位数据 \(JSON\)】\s*```json\s*(.*?)\s*```"
    tickers_pattern  = r"【提及股票数据 \(JSON\)】\s*```json\s*(.*?)\s*```"

    refined_match  = re.search(refined_pattern,  text, re.DOTALL | re.IGNORECASE)
    summary_match  = re.search(summary_pattern,  text, re.DOTALL | re.IGNORECASE)
    json_match     = re.search(json_pattern,     text, re.DOTALL | re.IGNORECASE)
    tickers_match  = re.search(tickers_pattern,  text, re.DOTALL | re.IGNORECASE)

    if refined_match:
        sections["refined_text"] = refined_match.group(1).strip()
    if summary_match:
        summary_content = summary_match.group(1).strip()
        if summary_content:
            sections["summary_lines"] = [
                line.strip() for line in summary_content.splitlines() if line.strip()
            ]
            logging.debug(f"parse_llm_output: 找到 {len(sections['summary_lines'])} 条摘要行。")
    if json_match:
        try:
            sections["price_levels_json"] = json.loads(json_match.group(1).strip())
        except json.JSONDecodeError as e:
            logging.warning(f"无法解析点位 JSON: {e}")
            sections["price_levels_json"] = []
    if tickers_match:
        try:
            sections["mentioned_tickers_json"] = json.loads(tickers_match.group(1).strip())
        except json.JSONDecodeError as e:
            logging.warning(f"无法解析股票提及 JSON: {e}")
            sections["mentioned_tickers_json"] = []

    return sections

parse_gemini_output = parse_llm_output

# =============================================================================
# 时间戳工具函数
# =============================================================================

def time_str_to_seconds(time_str):
    """HH:MM:SS[.ms] → 总秒数"""
    if not time_str:
        return None
    try:
        if '.' in time_str:
            main_part, ms_part = time_str.split('.')
            ms = int(ms_part.ljust(3, '0')[:3]) / 1000.0
        else:
            main_part = time_str
            ms = 0.0
        parts = list(map(int, main_part.split(':')))
        if len(parts) == 3:
            h, m, s = parts
        elif len(parts) == 2:
            h = 0; m, s = parts
        elif len(parts) == 1:
            h = 0; m = 0; s = parts[0]
        else:
            return None
        if h < 0 or m < 0 or m > 59 or s < 0 or s > 59:
            logging.warning(f"时间字符串 '{time_str}' 包含无效分量。")
            return None
        return h * 3600 + m * 60 + s + ms
    except ValueError:
        logging.warning(f"无法将时间字符串 '{time_str}' 转换为整数。")
        return None
    except Exception as e:
        logging.error(f"将时间字符串 '{time_str}' 转换为秒数时出错: {e}")
        return None

def seconds_to_time_str(total_seconds):
    """总秒数 → HH:MM:SS.ms"""
    if total_seconds is None or total_seconds < 0:
        total_seconds = 0
    try:
        frac_seconds, int_seconds = math.modf(total_seconds)
        int_seconds = int(int_seconds)
        h = int_seconds // 3600
        m = (int_seconds % 3600) // 60
        s = int_seconds % 60
        ms = int(round(frac_seconds * 1000))
        if ms >= 1000:
            int_seconds += 1; ms = 0
            h = int_seconds // 3600
            m = (int_seconds % 3600) // 60
            s = int_seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    except Exception as e:
        logging.error(f"将秒数 '{total_seconds}' 转换为时间字符串时出错: {e}")
        return "00:00:00.000"

def parse_timestamp_to_hms(timestamp_str):
    """[HH:MM:SS] / [MM:SS] 等 → HH:MM:SS.ms，失败返回 None"""
    if not timestamp_str or not timestamp_str.startswith('[') or not timestamp_str.endswith(']'):
        logging.warning(f"无效时间戳格式: '{timestamp_str}'")
        return None
    time_part = timestamp_str[1:-1].strip()
    parts = time_part.split(':')
    h, m, s, ms = 0, 0, 0, 0
    try:
        if len(parts) == 3:
            h = int(parts[0])
            m = int(parts[1])
            if '.' in parts[2]:
                s_part, ms_part = parts[2].split('.')
                s = int(s_part)
                ms = int(ms_part.ljust(3, '0')[:3])
            else:
                s = int(parts[2])
            if m > 59 or s > 59 or h < 0 or m < 0 or s < 0:
                logging.warning(f"时间戳 '{timestamp_str}' 分量无效。")
                if s > 59 and h == 0 and m < 60:
                    ms_val = s; s = m; m = h; h = 0
                    if m > 59 or s > 59 or m < 0 or s < 0:
                        return None
                    ms = int(str(ms_val).ljust(3, '0')[:3])
                    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
                return None
            return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
        elif len(parts) == 2:
            m_or_h = int(parts[0])
            if '.' in parts[1]:
                s_part, ms_part = parts[1].split('.')
                s = int(s_part)
                ms = int(ms_part.ljust(3, '0')[:3])
                if m_or_h > 59 or s > 59 or m_or_h < 0 or s < 0:
                    return None
                return f"00:{m_or_h:02d}:{s:02d}.{ms:03d}"
            else:
                s = int(parts[1])
                if m_or_h > 59 or s > 59 or m_or_h < 0 or s < 0:
                    return None
                return f"00:{m_or_h:02d}:{s:02d}.000"
        elif len(parts) == 1:
            if '.' in parts[0]:
                s_part, ms_part = parts[0].split('.')
                s = int(s_part)
                ms = int(ms_part.ljust(3, '0')[:3])
            else:
                s = int(parts[0])
            if s > 59 or s < 0:
                return None
            return f"00:00:{s:02d}.{ms:03d}"
        else:
            logging.warning(f"时间戳 '{timestamp_str}' 格式无法识别")
            return None
    except ValueError:
        logging.warning(f"无法解析时间戳 '{timestamp_str}' 中的数字。")
        return None
    except Exception as e:
        logging.error(f"解析时间戳 '{timestamp_str}' 时出错: {e}")
        return None

def format_timestamp_for_filename(timestamp_str):
    """时间戳 → HHMMSSms 文件名字符串"""
    hms_ms_time = parse_timestamp_to_hms(timestamp_str)
    if hms_ms_time:
        return hms_ms_time.replace(':', '').replace('.', '')
    digits = re.findall(r'\d+', timestamp_str)
    if digits:
        combined = "".join(digits)
        if len(combined) < 6:
            combined = combined.ljust(6, '0')
        return combined
    return f"未知时间_{int(time.time())}"

def extract_summary_data(summary_lines):
    """从摘要行列表中提取 (timestamp_str, text) 对。"""
    extracted_data = []
    timestamp_pattern = re.compile(
        r"^\s*[-*]?\s*(\[\s*\d{1,2}:\d{1,2}(?::\d{1,2})?(?:\.\d+)?\s*\])"
    )
    for line in summary_lines:
        ts_match = timestamp_pattern.search(line)
        if ts_match:
            timestamp_str = ts_match.group(1)
            text = line[ts_match.end():].strip()
            text = re.sub(r"^\s*[-*+]\s*", "", text)
            extracted_data.append({"timestamp_str": timestamp_str, "text": text})
            logging.debug(f"  摘要提取: ts='{timestamp_str}', text='{text}'")
        else:
            logging.warning(f"无法从摘要行提取时间戳: {line}")
    return extracted_data
