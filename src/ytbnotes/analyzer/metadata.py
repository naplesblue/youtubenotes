import re
import json
import logging
import datetime
from pathlib import Path

from .utils import success, failure, Result

def find_videos_to_process(history_file, analysis_log_file) -> Result:
    """
    查找下载历史中存在、且尚未成功分析的视频文件。
    返回 Result({'ok': True, 'value': [path, ...]})。
    """
    logging.info("正在查找需要处理的视频...")
    videos_to_process = []
    successfully_analyzed_paths = set()

    analysis_log_path = Path(analysis_log_file)
    if analysis_log_path.exists():
        try:
            with open(analysis_log_path, 'r', encoding='utf-8') as f:
                analysis_data = json.load(f)
            for entry in analysis_data:
                if entry.get("status") == "success" and entry.get("video_file_path"):
                    try:
                        abs_path = str(Path(entry["video_file_path"]).resolve())
                        successfully_analyzed_paths.add(abs_path)
                    except Exception as e:
                        logging.warning(f"无法解析分析日志路径 '{entry['video_file_path']}': {e}")
            logging.info(f"从分析日志加载了 {len(successfully_analyzed_paths)} 条成功处理记录。")
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"无法加载分析日志 '{analysis_log_path}': {e}")
            return failure(f"分析日志加载失败: {e}", error_type='corrupt')
        except Exception as e:
            logging.error(f"加载分析日志时发生意外错误: {e}")
            return failure(f"分析日志加载失败: {e}", error_type='unknown')

    history_path = Path(history_file)
    if not history_path.exists():
        logging.error(f"下载历史文件未找到: '{history_path}'")
        return failure("下载历史文件不存在", error_type='not_found')
    try:
        with open(history_path, 'r', encoding='utf-8') as f:
            tracking_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logging.error(f"读取下载历史文件 '{history_path}' 时出错: {e}")
        return failure(f"下载历史损坏: {e}", error_type='corrupt')
    except Exception as e:
        logging.error(f"加载下载历史时发生意外错误: {e}")
        return failure(f"下载历史加载失败: {e}", error_type='unknown')

    skipped_exist_count = 0
    skipped_analyzed_count = 0
    for feed_url, videos in tracking_data.items():
        if not isinstance(videos, dict):
            logging.warning(f"feed '{feed_url}' 条目格式不正确，已跳过。")
            continue
        for video_id, details in videos.items():
            if isinstance(details, dict) and details.get('file_path'):
                try:
                    current_filepath = Path(details['file_path']).resolve()
                    current_filepath_str = str(current_filepath)
                    if not current_filepath.exists():
                        logging.warning(f"文件不存在，跳过: {current_filepath_str}")
                        skipped_exist_count += 1
                        continue
                    if current_filepath_str in successfully_analyzed_paths:
                        logging.debug(f"已分析过，跳过: {current_filepath.name}")
                        skipped_analyzed_count += 1
                        continue
                    videos_to_process.append(current_filepath_str)
                except Exception as e:
                    logging.warning(f"处理视频 '{video_id}' 记录时出错: {e}")
            else:
                logging.debug(f"跳过视频 '{video_id}'，缺少 file_path。")

    logging.info(f"找到 {len(videos_to_process)} 个待处理视频。")
    if skipped_exist_count:
        logging.info(f"因文件不存在跳过 {skipped_exist_count} 个。")
    if skipped_analyzed_count:
        logging.info(f"因已分析过跳过 {skipped_analyzed_count} 个。")
    return success(videos_to_process)


def get_video_metadata(video_path_str, history_data):
    """从历史数据中检索视频元数据，找不到时从文件名推断。"""
    video_path_obj = Path(video_path_str).resolve()
    filename = video_path_obj.name
    match = re.match(r"(\d{8})\s*-\s*(.*?)\s*(?:\[(?P<id>[^\]]+)\])?\.\w+$", filename)
    title_fallback    = match.group(2).strip() if match else video_path_obj.stem
    upload_date_str   = match.group(1) if match else None
    video_id_fallback = match.group('id').strip() if match and match.group('id') else None
    upload_date = None
    if upload_date_str:
        try:
            upload_date = datetime.datetime.strptime(upload_date_str, "%Y%m%d").date()
        except ValueError:
            logging.warning(f"无法从文件名解析日期 '{upload_date_str}'。")
    channel_from_path = video_path_obj.parent.name if video_path_obj.parent else "未知频道"

    for feed, videos in history_data.items():
        if not isinstance(videos, dict):
            continue
        for vid, details in videos.items():
            try:
                history_path_str = details.get("file_path")
                if history_path_str:
                    history_path_obj = Path(history_path_str).resolve()
                    if history_path_obj == video_path_obj:
                        return {
                            "title":        details.get("title", title_fallback),
                            "channel_name": details.get("channel_name", channel_from_path),
                            "host":         details.get("host"),
                            "video_id":     vid,
                            "original_url": details.get("original_url"),
                            "upload_date":  details.get("upload_date", str(upload_date) if upload_date else None),
                        }
            except Exception as e:
                logging.warning(f"解析历史记录路径失败: {e}")
                continue

    return {
        "title":        title_fallback,
        "channel_name": channel_from_path,
        "host":         None,
        "video_id":     video_id_fallback or "unknown_id",
        "original_url": None,
        "upload_date":  str(upload_date) if upload_date else None,
    }
