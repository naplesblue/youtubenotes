import re
import json
import time
import logging
import datetime
from pathlib import Path

from .config import ANALYSIS_RESULTS_DIR, AUDIO_MODEL_NAME, TEXT_MODEL_NAME
from .utils import extract_summary_data, write_file_atomically, success, failure, Result
from .metadata import get_video_metadata

def load_analysis_log(log_file) -> Result:
    log_path = Path(log_file)
    if not log_path.exists():
        return success([])
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content:
                return success([])
            return success(json.loads(content))
    except json.JSONDecodeError as e:
        corrupt_backup = log_path.with_suffix(f'.corrupt.{int(time.time())}.json')
        try:
            log_path.rename(corrupt_backup)
            logging.error(f"分析日志损坏，已备份至 '{corrupt_backup}'。错误: {e}")
        except Exception as backup_err:
            logging.error(f"无法备份损坏日志: {backup_err}")
        return failure(f"JSON 解析错误: {e}", error_type='corrupt')
    except IOError as e:
        logging.error(f"无法读取分析日志 '{log_path}': {e}")
        return failure(f"IO 错误: {e}", error_type='permission')
    except Exception as e:
        logging.error(f"加载分析日志时发生未知错误: {e}")
        return failure(f"未知错误: {e}", error_type='unknown')


def update_analysis_log(log_file, log_entry) -> Result:
    result = load_analysis_log(log_file)
    log_data = result['value'] if result['ok'] else []
    
    # 过滤掉 30 天之前的记录
    try:
        cutoff = datetime.datetime.now() - datetime.timedelta(days=30)
        recent_logs = []
        for entry in log_data:
            ts_str = entry.get("analysis_timestamp")
            if ts_str:
                try:
                    ts = datetime.datetime.fromisoformat(ts_str)
                    if ts >= cutoff:
                        recent_logs.append(entry)
                except ValueError:
                    # 如果时间戳解析失败，保留该记录
                    recent_logs.append(entry)
            else:
                recent_logs.append(entry)
        log_data = recent_logs
    except Exception as e:
        logging.warning(f"分析日志保留近期记录时出错: {e}")

    log_data.append(log_entry)
    try:
        write_file_atomically(Path(log_file), json.dumps(log_data, indent=2, ensure_ascii=False))
        logging.debug(f"分析日志已更新: {log_file}")
        return success(None)
    except (IOError, TypeError) as e:
        logging.error(f"写入分析日志失败: {e}")
        return failure(f"写入失败: {e}", error_type='permission')


def process_and_save_results(
    raw_transcript, refined_text, summary_lines,
    price_levels_json, video_path, history_data,
    mentioned_tickers_json=None,
    return_extra_paths=False,
):
    """
    将分析结果保存为 Markdown 文件和结构化 JSON。
    默认返回 (markdown_path, structured_json_path) 元组，失败返回 None。
    当 return_extra_paths=True 时，返回
    (markdown_path, structured_json_path, price_levels_json_path)。
    """
    logging.info("正在处理最终结果并保存 Markdown 文件...")
    video_path_obj = Path(video_path)

    summary_data   = extract_summary_data(summary_lines)
    video_metadata = get_video_metadata(str(video_path_obj), history_data)

    channel_name_safe = re.sub(r'[\\/*?:"<>|]', '_', video_metadata.get("channel_name", "未知频道"))
    date_str          = video_metadata.get("upload_date") or datetime.date.today().strftime("%Y-%m-%d")
    date_folder_str   = date_str.replace("-", "")
    output_dir        = Path(ANALYSIS_RESULTS_DIR) / channel_name_safe / date_folder_str

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logging.error(f"创建输出目录 '{output_dir}' 失败: {e}")
        return None

    safe_title          = re.sub(r'[\\/*?:"<>|]', '_', video_metadata.get('title', '无标题'))[:100]
    markdown_filename   = f"{date_folder_str} - {safe_title}"
    markdown_filepath   = output_dir / f"{markdown_filename}.md"

    md = []
    md.append("---")
    escaped_title   = video_metadata.get('title', '无标题').replace('"', '\\"')
    escaped_channel = video_metadata.get('channel_name', '未知频道').replace('"', '\\"')
    md.append(f'title: "{escaped_title}"')
    md.append(f'channel: "{escaped_channel}"')
    md.append(f"video_id: {video_metadata.get('video_id', 'N/A')}")
    if video_metadata.get('original_url'):
        md.append(f"original_url: {video_metadata['original_url']}")
    if video_metadata.get('upload_date'):
        md.append(f"upload_date: {video_metadata['upload_date']}")
    md.append(f"analysis_date: {datetime.date.today().isoformat()}")
    md.append(f"audio_model: {AUDIO_MODEL_NAME}")
    md.append(f"text_model: {TEXT_MODEL_NAME}")
    md.append("tags: [finance, youtube-notes]")
    md.append("status: processed")
    md.append("---")
    md.append("")

    md.append("# 【完整转录 (带内部时间戳)】")
    md.append("")
    md.append("<details>")
    md.append("<summary>点击展开/折叠完整转录</summary>")
    md.append("<br>")
    md.append(raw_transcript if raw_transcript else "*原始转录未获取或为空。*")
    md.append("</details>")
    md.append("")

    md.append("# 【精炼文本】")
    md.append("")
    md.append(
        refined_text
        if refined_text and refined_text != "【精炼文本】部分未找到或为空。"
        else "*精炼文本未生成或为空。*"
    )
    md.append("")

    md.append("# 【关键信息摘要（含时间戳）】")
    md.append("")
    if not summary_data:
        md.append("*未提取到关键信息摘要。*")
    else:
        for item in summary_data:
            ts_display       = item.get('timestamp_str', "[无时间戳]")
            item_text_cleaned = re.sub(r"^\s*[-*+]\s*", "", item.get('text', '')).strip()
            md.append(f"- {ts_display} {item_text_cleaned}")
            md.append("")

    md.append("# 【原子化点位概览】")
    md.append("")
    if price_levels_json:
        md.append("| 股票 | 价格 | 类型 | 上下文 | 时间戳 |")
        md.append("| :--- | :--- | :--- | :--- | :--- |")
        for level in price_levels_json:
            ticker    = level.get('ticker', 'N/A')
            price     = level.get('price', 'N/A')
            ltype     = level.get('type', 'N/A')
            context   = level.get('context', '')
            timestamp = level.get('timestamp', '')
            md.append(f"| [[{ticker}]] | {price} | {ltype} | {context} | {timestamp} |")
        md.append("")
        md.append(f"*共提取 {len(price_levels_json)} 个价格点位*")
    else:
        md.append("*未提取到原子化点位数据。*")
    md.append("")

    try:
        write_file_atomically(markdown_filepath, "\n".join(md), mode='w', encoding='utf-8')
        logging.info(f"Markdown 已保存: {markdown_filepath.resolve()}")
    except Exception as e:
        logging.error(f"写入 Markdown 失败: {e}")
        return None

    video_id = video_metadata.get('video_id', 'unknown')
    structured_json_filepath = output_dir / f"{video_id}.json"

    structured_data = {
        "metadata": {
            "title":       video_metadata.get('title', '无标题'),
            "channel":     video_metadata.get('channel_name', '未知频道'),
            "host":        video_metadata.get('host'),
            "video_id":    video_id,
            "youtube_url": video_metadata.get('original_url', ''),
            "date":        video_metadata.get('upload_date', ''),
            "status":      "processed",
        },
        "brief_text":       refined_text if refined_text else "",
        "summary":          refined_text if refined_text else "",
        "key_points":       [item.get('text', '') for item in summary_data if item.get('text')],
        "raw_transcript":   raw_transcript if raw_transcript else "",
        "mentioned_tickers": [],
        "people_mentioned": [],
    }

    if mentioned_tickers_json and isinstance(mentioned_tickers_json, list):
        host_name = video_metadata.get('host')
        for td in mentioned_tickers_json:
            # 强行覆盖 LLM 提取的 analyst 以避免人物笔记混乱
            # host_name 在 downloader 阶段已保证兜底为 channel_name
            extracted_analyst = td.get("analyst", "unknown")
            final_analyst = host_name if host_name else extracted_analyst
            
            structured_data["mentioned_tickers"].append({
                "ticker":       td.get("ticker", "UNKNOWN"),
                "company_name": td.get("company_name", "Unknown"),
                "sentiment":    td.get("sentiment", "neutral"),
                "analyst":      final_analyst,
                "price_levels": td.get("price_levels", []),
            })
    elif price_levels_json and isinstance(price_levels_json, list):
        ticker_map = {}
        for level in price_levels_json:
            t = level.get("ticker")
            if t and t not in ticker_map:
                ticker_map[t] = {"ticker": t, "company_name": "", "sentiment": "neutral",
                                 "analyst": "unknown", "price_levels": []}
            if t:
                ticker_map[t]["price_levels"].append({
                    "level":   level.get("price"),
                    "type":    level.get("type", "observation"),
                    "context": level.get("context", ""),
                })
        structured_data["mentioned_tickers"] = list(ticker_map.values())

    try:
        write_file_atomically(
            structured_json_filepath,
            json.dumps(structured_data, indent=2, ensure_ascii=False)
        )
        logging.info(f"结构化 JSON 已保存: {structured_json_filepath.resolve()}")
    except Exception as e:
        logging.warning(f"保存结构化 JSON 失败: {e}")

    price_levels_json_path = None
    if price_levels_json:
        price_json_path = output_dir / f"{date_folder_str} - {safe_title}_price_levels.json"
        try:
            write_file_atomically(
                price_json_path,
                json.dumps(price_levels_json, indent=2, ensure_ascii=False)
            )
            logging.info(f"点位 JSON 已保存: {price_json_path.resolve()}")
            price_levels_json_path = str(price_json_path.resolve())
        except Exception as e:
            logging.warning(f"保存点位 JSON 失败: {e}")

    result = (
        str(markdown_filepath.resolve()),
        str(structured_json_filepath.resolve()),
    )
    if return_extra_paths:
        return result + (price_levels_json_path,)
    return result
