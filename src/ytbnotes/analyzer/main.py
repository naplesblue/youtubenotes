import os
import sys
import time
import json
import logging
import datetime
from pathlib import Path

from .config import (
    HISTORY_FILE,
    AUDIO_DIR,
    ANALYSIS_LOG_FILE,
    AUDIO_MODEL_NAME,
    TEXT_MODEL_NAME,
    TRANSCRIPT_MODE,
    ANALYZER_ONLY_VIDEO,
    ANALYZER_MAX_VIDEOS,
    FUNASR_USE_WORKER,
    FUNASR_SCRIPT_PATH,
    FUNASR_WORKER_STARTUP_TIMEOUT,
    FUNASR_WORKER_REQUEST_TIMEOUT,
    FUNASR_WORKER_MAX_JOBS,
    FUNASR_WORKER_IDLE_TIMEOUT,
    FUNASR_WORKER_MAX_SECONDS,
    FUNASR_WORKER_MAX_RETRIES,
    FUNASR_FALLBACK_TO_CLI_ON_WORKER_ERROR
)
from .utils import parse_llm_output
from .metadata import find_videos_to_process, get_video_metadata
from .subtitle import load_subtitle_transcript
from .transcriber import extract_audio, get_raw_transcript_with_timestamps, FunASRWorkerClient
from .llm_processor import process_text_tasks
from .result_writer import process_and_save_results, update_analysis_log

def main():
    overall_start_time = time.time()
    
    # 强制设置基础日志级别如果在 CLI 中没有被覆盖
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        encoding='utf-8',
        force=True
    )

    logging.info("--- 开始音频分析任务 ---")

    dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
    if not dashscope_api_key:
        logging.critical(
            "致命错误: 环境变量 DASHSCOPE_API_KEY 未设置。"
            "请在 .env 文件中配置阿里云 DashScope API Key。脚本退出。"
        )
        sys.exit(1)
    logging.info(
        "配置加载成功。"
        f"ASR={AUDIO_MODEL_NAME}  "
        f"LLM={TEXT_MODEL_NAME}  "
        f"TRANSCRIPT_MODE={TRANSCRIPT_MODE}"
    )

    result = find_videos_to_process(HISTORY_FILE, ANALYSIS_LOG_FILE)
    if not result['ok']:
        logging.error(f"查找视频时出错 ({result.get('error_type')}): {result.get('error')}")
        sys.exit(1)

    videos_to_analyze = result['value']
    if not videos_to_analyze:
        logging.info("没有找到需要分析的新视频。任务结束。")
        sys.exit(0)

    if ANALYZER_ONLY_VIDEO:
        key = ANALYZER_ONLY_VIDEO.lower()
        filtered = []
        for p in videos_to_analyze:
            p_obj = Path(p)
            name = p_obj.name.lower()
            full = str(p_obj).lower()
            if key in name or key in full:
                filtered.append(p)
        if not filtered:
            logging.error(f"ANALYZER_ONLY_VIDEO 未匹配到待处理视频: {ANALYZER_ONLY_VIDEO}")
            sys.exit(1)
        logging.info(f"ANALYZER_ONLY_VIDEO 生效，匹配到 {len(filtered)} 个视频。")
        videos_to_analyze = filtered

    if ANALYZER_MAX_VIDEOS > 0 and len(videos_to_analyze) > ANALYZER_MAX_VIDEOS:
        logging.info(f"ANALYZER_MAX_VIDEOS={ANALYZER_MAX_VIDEOS}，将只处理前 {ANALYZER_MAX_VIDEOS} 个视频。")
        videos_to_analyze = videos_to_analyze[:ANALYZER_MAX_VIDEOS]

    logging.info(f"共找到 {len(videos_to_analyze)} 个视频待分析。")

    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            full_history_data = json.load(f)
    except Exception as e:
        logging.error(f"无法加载历史文件 '{HISTORY_FILE}': {e}，元数据可能不完整。")
        full_history_data = {}

    success_count = 0
    failure_count = 0
    total_videos  = len(videos_to_analyze)

    worker_client = None
    if FUNASR_USE_WORKER:
        worker_client = FunASRWorkerClient(
            script_path=FUNASR_SCRIPT_PATH,
            startup_timeout=FUNASR_WORKER_STARTUP_TIMEOUT,
            request_timeout=FUNASR_WORKER_REQUEST_TIMEOUT,
            worker_max_jobs=FUNASR_WORKER_MAX_JOBS,
            worker_idle_timeout=FUNASR_WORKER_IDLE_TIMEOUT,
            worker_max_seconds=FUNASR_WORKER_MAX_SECONDS,
            worker_max_retries=FUNASR_WORKER_MAX_RETRIES,
            extra_hotwords=os.getenv("FUNASR_HOTWORDS", "").strip(),
            verbose=os.getenv("FUNASR_VERBOSE", "0").strip().lower() in {"1", "true", "yes", "on"},
        )
        logging.info(
            "FunASR 常驻 worker 已启用: "
            f"max_jobs={FUNASR_WORKER_MAX_JOBS}, "
            f"idle_timeout={FUNASR_WORKER_IDLE_TIMEOUT}s, "
            f"max_seconds={FUNASR_WORKER_MAX_SECONDS}s, "
            f"max_retries={FUNASR_WORKER_MAX_RETRIES}, "
            f"cli_fallback={FUNASR_FALLBACK_TO_CLI_ON_WORKER_ERROR}"
        )
    else:
        logging.info("FUNASR_USE_WORKER=0，使用单次 CLI 模式。")

    try:
        for i, video_path_str in enumerate(videos_to_analyze):
            video_start_time = time.time()
            video_filename   = Path(video_path_str).name
            logging.info(f"--- 开始处理 {i+1}/{total_videos}: {video_filename} ---")

            log_entry = {
                "video_file_path":      video_path_str,
                "audio_file_path":      None,
                "markdown_file_path":   None,
                "structured_json_path": None,
                "price_levels_json_path": None,
                "transcript_source":    None,
                "subtitle_probe_result": None,
                "fallback_reason":      None,
                "analysis_timestamp":   datetime.datetime.now().isoformat(),
                "status":               "pending",
                "duration_seconds":     None,
                "error_message":        None,
            }

            extracted_audio_path  = None
            should_cleanup_audio  = False
            markdown_file_path    = None
            structured_json_path  = None
            price_levels_json_path = None
            current_video_status  = "failure"
            transcript_source = None
            subtitle_probe_result = None
            fallback_reason = None

            try:
                video_metadata = get_video_metadata(video_path_str, full_history_data)
                original_url = video_metadata.get("original_url")
                raw_transcript = None

                if TRANSCRIPT_MODE in {"auto", "subtitle"}:
                    if not original_url:
                        subtitle_result = {
                            "ok": False,
                            "probe": {"ok": False, "reason": "missing_original_url"},
                            "quality": None,
                            "error": "missing_original_url",
                        }
                    else:
                        subtitle_result = load_subtitle_transcript(original_url)

                    subtitle_probe_result = {
                        "ok": bool(subtitle_result.get("ok")),
                        "probe": subtitle_result.get("probe"),
                        "quality": subtitle_result.get("quality"),
                        "error": subtitle_result.get("error"),
                        "lang_family": subtitle_result.get("lang_family"),
                        "source": subtitle_result.get("source"),
                    }
                    log_entry["subtitle_probe_result"] = subtitle_probe_result

                    if subtitle_result.get("ok") and subtitle_result.get("transcript"):
                        raw_transcript = subtitle_result["transcript"]
                        lang_fam = subtitle_result.get("lang_family") or "unknown"
                        sub_src = subtitle_result.get("source") or "manual"
                        transcript_source = f"subtitle_{sub_src}_{lang_fam}"
                        logging.info(f"命中字幕 [{lang_fam} / {sub_src}]，跳过 ASR 转录。")
                    else:
                        err_msg = subtitle_result.get("error") or "subtitle_not_available"
                        if TRANSCRIPT_MODE == "subtitle":
                            raise RuntimeError(f"字幕模式未获取到可用人工英文字幕: {err_msg}")
                        fallback_reason = f"subtitle_to_asr:{err_msg}"
                        logging.info(f"字幕不可用或质量不达标，回退 ASR。原因: {err_msg}")

                if raw_transcript is None:
                    extracted_audio_path, should_cleanup_audio = extract_audio(video_path_str, AUDIO_DIR)
                    if not extracted_audio_path:
                        raise RuntimeError(f"音频提取失败: {video_filename}")
                    log_entry["audio_file_path"] = extracted_audio_path
                    logging.info(f"音频提取完成: {Path(extracted_audio_path).name}")

                    raw_transcript = get_raw_transcript_with_timestamps(
                        extracted_audio_path,
                        worker_client=worker_client,
                        allow_cli_fallback=FUNASR_FALLBACK_TO_CLI_ON_WORKER_ERROR,
                    )
                    if not raw_transcript:
                        raise RuntimeError(f"本地 ASR 转录失败: {video_filename}")
                    transcript_source = "asr"
                    logging.info("本地转录完成。")

                processed_text_output = process_text_tasks(raw_transcript)
                if not processed_text_output:
                    raise RuntimeError(f"{TEXT_MODEL_NAME} 文本处理失败。")
                logging.info("文本处理完成。")

                parsed = parse_llm_output(processed_text_output)
                refined_text         = parsed["refined_text"]
                summary_lines        = parsed["summary_lines"]
                price_levels_json    = parsed.get("price_levels_json", [])
                mentioned_tickers_json = parsed.get("mentioned_tickers_json", None)

                if refined_text == "【精炼文本】部分未找到或为空。" and not summary_lines:
                    logging.warning("LLM 输出解析未能找到精炼文本和摘要，结果可能不完整。")
                else:
                    logging.info("LLM 输出解析完成。")

                result_paths = process_and_save_results(
                    raw_transcript=raw_transcript,
                    refined_text=refined_text,
                    summary_lines=summary_lines,
                    price_levels_json=price_levels_json,
                    video_path=video_path_str,
                    history_data=full_history_data,
                    mentioned_tickers_json=mentioned_tickers_json,
                    return_extra_paths=True,
                )
                if not result_paths:
                    raise RuntimeError("保存结果文件失败。")

                markdown_file_path   = result_paths[0]
                structured_json_path = result_paths[1] if len(result_paths) > 1 else None
                price_levels_json_path = result_paths[2] if len(result_paths) > 2 else None

                current_video_status = "success"
                success_count += 1
                logging.info(f"视频 '{video_filename}' 分析成功。输出: {markdown_file_path}")

            except Exception as e:
                logging.error(f"处理视频 '{video_filename}' 时出错: {e}", exc_info=False)
                current_video_status = "failure"
                failure_count += 1
                log_entry["error_message"] = str(e)

            video_duration = round(time.time() - video_start_time, 2)
            log_entry.update({
                "status":               current_video_status,
                "markdown_file_path":   markdown_file_path,
                "structured_json_path": structured_json_path if current_video_status == "success" else None,
                "price_levels_json_path": price_levels_json_path if current_video_status == "success" else None,
                "transcript_source":    transcript_source,
                "subtitle_probe_result": subtitle_probe_result,
                "fallback_reason":      fallback_reason,
                "duration_seconds":     video_duration,
            })
            update_analysis_log(ANALYSIS_LOG_FILE, log_entry)

            if should_cleanup_audio and extracted_audio_path and Path(extracted_audio_path).exists():
                try:
                    Path(extracted_audio_path).unlink()
                    logging.info(f"已清理音频文件: {Path(extracted_audio_path).name}")
                except OSError as e:
                    logging.warning(f"无法删除音频文件 {extracted_audio_path}: {e}")

            logging.info(
                f"--- {i+1}/{total_videos} ({video_filename}) 完毕，"
                f"耗时 {video_duration}s，状态: {current_video_status} ---"
            )
    finally:
        if worker_client is not None:
            worker_client.stop()
            logging.info("FunASR worker 已停止并释放内存资源。")

    overall_duration = round(time.time() - overall_start_time, 2)
    logging.info("--- 所有视频分析任务完成 ---")
    logging.info(f"总计: {total_videos}  成功: {success_count}  失败: {failure_count}  总耗时: {overall_duration}s")
    sys.exit(0 if failure_count == 0 else 1)

if __name__ == "__main__":
    main()
