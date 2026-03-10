import os
import sys
import time
import json
import select
import signal
import logging
import subprocess
from pathlib import Path

from .config import (
    FFMPEG_PATH,
    AUDIO_MODEL_NAME,
    FUNASR_SCRIPT_PATH,
)

def terminate_subprocess(proc: subprocess.Popen, process_name: str, grace_seconds: int = 8):
    """终止子进程（及其进程组），避免异常场景下残留进程长期占用内存。"""
    if not proc or proc.poll() is not None:
        return

    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        logging.warning(f"{process_name} 收到终止信号，等待退出...")
    except Exception as e:
        logging.warning(f"发送终止信号失败（{process_name}）: {e}")

    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        logging.warning(f"{process_name} 未在 {grace_seconds}s 内退出，已强制杀死。")
    except Exception as e:
        logging.warning(f"强制终止失败（{process_name}）: {e}")
    finally:
        try:
            proc.wait(timeout=3)
        except Exception:
            pass


def extract_audio(video_path, audio_dir):
    """
    准备 ASR 输入音频。
    返回 (audio_path, should_cleanup)：
      - should_cleanup=True: 表示本函数新生成的临时 mp3，流程结束后应删除
      - should_cleanup=False: 表示复用原始输入，不应删除
    """
    logging.info(f"正在准备音频输入: '{video_path}'")
    video_path_obj = Path(video_path)
    audio_dir_obj = Path(audio_dir)
    if not video_path_obj.exists():
        logging.error(f"视频文件未找到: '{video_path_obj}'")
        return None, False

    if video_path_obj.suffix.lower() == ".mp3":
        logging.info("输入文件已是 mp3，跳过 ffmpeg 转码。")
        return str(video_path_obj.resolve()), False

    audio_dir_obj.mkdir(parents=True, exist_ok=True)
    audio_output_path = audio_dir_obj / (video_path_obj.stem + '.mp3')
    command = [
        FFMPEG_PATH,
        '-i', str(video_path_obj),
        '-vn',
        '-codec:a', 'libmp3lame',
        '-q:a', '2',
        '-y',
        str(audio_output_path),
    ]
    logging.info(f"执行音频提取: {' '.join(command)}")
    try:
        result = subprocess.run(
            command, check=True, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=600
        )
        if result.stderr:
            logging.debug(f"ffmpeg stderr:\n{result.stderr}")
        logging.info(f"音频提取成功: {audio_output_path}")
        return str(audio_output_path.resolve()), True
    except FileNotFoundError:
        logging.error(f"ffmpeg 未找到，请检查 FFMPEG_PATH: '{FFMPEG_PATH}'")
        return None, False
    except subprocess.TimeoutExpired:
        logging.error(f"ffmpeg 超时: {video_path_obj}")
        audio_output_path.unlink(missing_ok=True)
        return None, False
    except subprocess.CalledProcessError as e:
        logging.error(f"ffmpeg 失败 (rc={e.returncode}): {video_path_obj}\n{e.stderr}")
        audio_output_path.unlink(missing_ok=True)
        return None, False
    except Exception as e:
        logging.error(f"提取音频时发生未知错误: {e}")
        audio_output_path.unlink(missing_ok=True)
        return None, False


class FunASRWorkerClient:
    """常驻 FunASR worker 客户端：单次加载模型，多次转录调用。"""

    def __init__(
        self,
        script_path: str,
        startup_timeout: int = 900,
        request_timeout: int = 1800,
        worker_max_jobs: int = 6,
        worker_idle_timeout: int = 180,
        worker_max_seconds: int = 1800,
        worker_max_retries: int = 1,
        extra_hotwords: str = "",
        verbose: bool = False,
    ):
        self.script_path = str(Path(script_path).resolve())
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.worker_max_jobs = max(0, worker_max_jobs)
        self.worker_idle_timeout = max(0, worker_idle_timeout)
        self.worker_max_seconds = max(0, worker_max_seconds)
        self.worker_max_retries = max(0, worker_max_retries)
        self.extra_hotwords = (extra_hotwords or "").strip()
        self.verbose = verbose

        self.proc = None
        self.request_seq = 0

    @staticmethod
    def _compact_error_text(raw) -> str:
        text = str(raw or "").replace("\r", " ").replace("\n", " ").strip()
        if len(text) > 220:
            text = text[:220] + "..."
        return text

    def _build_command(self):
        cmd = [
            sys.executable,
            self.script_path,
            "--worker",
            "--format", "text",
            "--worker-parent-pid", str(os.getpid()),
            "--worker-max-jobs", str(self.worker_max_jobs),
            "--worker-idle-timeout", str(self.worker_idle_timeout),
            "--worker-max-seconds", str(self.worker_max_seconds),
        ]
        if self.extra_hotwords:
            cmd += ["--hotwords", self.extra_hotwords]
        if self.verbose:
            cmd.append("--verbose")
        return cmd

    def _is_running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def _read_json_line(self, timeout_seconds: int):
        if not self.proc or not self.proc.stdout:
            return None

        end_time = time.time() + timeout_seconds
        try:
            fd = self.proc.stdout.fileno()
        except Exception:
            return None
        while time.time() < end_time:
            remaining = max(0.0, end_time - time.time())
            try:
                readable, _, _ = select.select([fd], [], [], remaining)
            except Exception:
                return {"event": "eof"}
            if not readable:
                return None

            try:
                line = self.proc.stdout.readline()
            except Exception:
                return {"event": "eof"}
            if line == "":
                return {"event": "eof"}
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                start = line.find("{")
                end = line.rfind("}")
                if start != -1 and end > start:
                    candidate = line[start:end + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        pass
                logging.debug(f"收到非 JSON worker 输出，已忽略: {line[:200]}")
                continue
        return None

    def start(self) -> bool:
        if self._is_running():
            return True

        script_path_obj = Path(self.script_path)
        if not script_path_obj.exists():
            logging.error(f"funasr_transcribe.py 未找到: {script_path_obj}")
            return False

        popen_kwargs = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": None, "text": True, "encoding": "utf-8", "bufsize": 1}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["preexec_fn"] = os.setsid

        try:
            self.proc = subprocess.Popen(self._build_command(), **popen_kwargs)
        except Exception as e:
            logging.error(f"启动 FunASR worker 失败: {e}")
            self.proc = None
            return False

        ready_msg = self._read_json_line(self.startup_timeout)
        if not ready_msg or ready_msg.get("event") != "ready":
            logging.error(f"FunASR worker 启动失败或未返回 ready: {ready_msg}")
            self.stop()
            return False

        logging.info("FunASR worker 已就绪（模型单次加载）。")
        return True

    def stop(self):
        if not self.proc:
            return

        try:
            if self.proc.poll() is None and self.proc.stdin:
                shutdown_req = {"id": "shutdown", "cmd": "shutdown"}
                self.proc.stdin.write(json.dumps(shutdown_req, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
                self._read_json_line(5)
        except Exception:
            pass
        finally:
            terminate_subprocess(self.proc, "funasr_worker")
            self.proc = None

    def transcribe(self, audio_path: str):
        audio_path_str = str(Path(audio_path).resolve())
        total_attempts = self.worker_max_retries + 1
        for attempt in range(1, total_attempts + 1):
            if not self.start():
                return None

            self.request_seq += 1
            request_id = f"req-{int(time.time() * 1000)}-{self.request_seq}"
            payload = {
                "id": request_id,
                "cmd": "transcribe",
                "audio_path": audio_path_str,
            }

            try:
                if not self.proc or not self.proc.stdin:
                    raise RuntimeError("worker stdin 不可用")
                self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except Exception as e:
                logging.warning(f"发送请求到 FunASR worker 失败（attempt={attempt}）: {e}")
                self.stop()
                continue

            response = self._read_json_line(self.request_timeout)
            if not response:
                logging.warning(f"等待 FunASR worker 响应超时（attempt={attempt}）")
                self.stop()
                continue
            if response.get("event") == "eof":
                logging.warning(f"FunASR worker 意外退出（attempt={attempt}）")
                self.stop()
                continue
            if response.get("event") == "bye":
                logging.info(f"FunASR worker 已退出: {response.get('reason')}")
                self.stop()
                continue
            if response.get("id") != request_id:
                logging.warning(f"FunASR worker 返回了不匹配的响应: {response}")
                self.stop()
                continue
            if not response.get("ok"):
                err_text = self._compact_error_text(response.get("error"))
                err_type = self._compact_error_text(response.get("error_type"))
                err_repr = self._compact_error_text(response.get("error_repr"))
                logging.warning(
                    "FunASR worker 转录失败（attempt=%s, type=%s）: %s %s",
                    attempt,
                    err_type or "unknown",
                    err_text,
                    f"[{err_repr}]" if err_repr else "",
                )
                self.stop()
                continue

            transcript = response.get("transcript", "")
            if transcript and transcript.strip():
                return transcript
            logging.error("FunASR worker 返回空转录内容。")
            return None

        return None


def get_raw_transcript_with_timestamps(
    audio_path,
    api_key=None,
    worker_client: FunASRWorkerClient = None,
    allow_cli_fallback: bool = True,
):
    """
    使用本地 Fun-ASR-Nano-2512 转录音频，返回带 [HH:MM:SS] 时间戳的文本。
    调用 funasr_transcribe.py CLI，日志走 stderr，转录结果走 stdout，互不干扰。
    """
    logging.info(f"正在使用本地 {AUDIO_MODEL_NAME} 转录: {audio_path}")
    audio_path_obj = Path(audio_path)

    if not audio_path_obj.exists():
        logging.error(f"音频文件不存在: {audio_path_obj}")
        return None
    if audio_path_obj.stat().st_size == 0:
        logging.error(f"音频文件为空: {audio_path_obj}")
        return None

    if worker_client is not None:
        transcript = worker_client.transcribe(str(audio_path_obj))
        if transcript and transcript.strip():
            line_count = len(transcript.splitlines())
            logging.info(f"worker 转录完成，共 {line_count} 行。")
            return transcript
        if not allow_cli_fallback:
            logging.error("worker 转录失败，且已禁用单次 CLI 回退。")
            return None
        logging.warning("worker 转录失败，回退单次 CLI 调用。")

    funasr_script = Path(FUNASR_SCRIPT_PATH)
    if not funasr_script.exists():
        logging.error(
            f"funasr.py 未找到: {funasr_script}\n"
            f"请将模块安装好，或设置环境变量 FUNASR_SCRIPT_PATH。"
        )
        return None

    command = [
        sys.executable,
        str(funasr_script),
        str(audio_path_obj),
        "--format", "text",
    ]
    extra_hotwords = os.getenv("FUNASR_HOTWORDS", "").strip()
    if extra_hotwords:
        command += ["--hotwords", extra_hotwords]
        logging.info(f"追加自定义热词: {extra_hotwords}")
    if os.getenv("FUNASR_VERBOSE", "0").strip().lower() in {"1", "true", "yes", "on"}:
        command.append("--verbose")
        logging.info("FUNASR_VERBOSE 已启用，将输出详细堆栈日志。")

    logging.info("FunASR 本地转录中，请稍候...")
    proc = None
    try:
        popen_kwargs = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["preexec_fn"] = os.setsid

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            **popen_kwargs,
        )
        stdout_data, stderr_data = proc.communicate(timeout=1800)

        if proc.returncode != 0:
            logging.error(f"funasr_transcribe.py 返回错误 (rc={proc.returncode})")
            logging.error(f"stderr:\n{(stderr_data or '')[-2000:]}")
            return None

        transcript = (stdout_data or "").strip()
        if not transcript:
            logging.error("FunASR 转录结果为空，请检查音频文件是否有效。")
            return None

        line_count = len(transcript.splitlines())
        logging.info(f"本地转录完成，共 {line_count} 行。")
        logging.debug(f"转录前 200 字:\n{transcript[:200]}")
        return transcript

    except subprocess.TimeoutExpired:
        logging.error("funasr_transcribe.py 超时（>30 分钟）")
        terminate_subprocess(proc, "funasr_transcribe.py")
        return None
    except KeyboardInterrupt:
        logging.warning("检测到中断信号，正在终止 funasr_transcribe.py 子进程...")
        terminate_subprocess(proc, "funasr_transcribe.py")
        raise
    except FileNotFoundError:
        logging.error(f"Python 解释器未找到: {sys.executable}")
        return None
    except Exception as e:
        logging.error(f"调用 FunASR CLI 时发生错误: {e}", exc_info=True)
        terminate_subprocess(proc, "funasr_transcribe.py")
        return None
