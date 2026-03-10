---
description: 调试 FunASR 本地语音识别问题（转录失败、内存溢出、质量问题）
---

# 调试 FunASR 转录

## 增量说明（2026-03）
- 保留本页既有排障结论，新增当前已跑通基线：
  - `FUNASR_USE_WORKER=0`
  - `FUNASR_ENABLE_VAD=0`
  - 长音频依赖脚本内分片（`FUNASR_CHUNK_SECONDS`）而不是常驻 worker
- 如需尝试 worker，请仅在独立验证场景开启，并保持 `FUNASR_FALLBACK_TO_CLI_ON_WORKER_ERROR=0`，避免失败后重复加载模型。

## 环境信息
- 模型：`FunAudioLLM/Fun-ASR-Nano-2512`（~2GB，存于 `~/.cache/modelscope/hub/models/`）
- Python 环境：`/Users/Naples/YoutubeNotes/ytbnotes/bin/python3`（uv venv）
- 关键依赖：`funasr==1.3.1`, `torch==2.10.0`, `transformers==5.3.0`

## 直接 CLI 测试（绕过 audio_analyzer.py）

```bash
# 基本测试
/Users/Naples/YoutubeNotes/ytbnotes/bin/python3 \
  /Users/Naples/YoutubeNotes/funasr_transcribe.py \
  /path/to/audio.mp3 --format text --verbose

# 带环境变量
FUNASR_ENABLE_VAD=0 FUNASR_CHUNK_SECONDS=60 \
/Users/Naples/YoutubeNotes/ytbnotes/bin/python3 \
  /Users/Naples/YoutubeNotes/funasr_transcribe.py \
  /path/to/audio.mp3 --format text --verbose
```

## 已知问题与修复

### 1. KeyError: 0（VAD 不兼容）
- **症状**: `转录过程中发生错误: 0`
- **原因**: `funasr==1.3.1` 的 `inference_with_vad()` 假设 timestamp 为 list，但 Nano 返回 dict
- **修复**: 确保 `FUNASR_ENABLE_VAD=0`，使用 ffmpeg 分片代替

### 2. OOM（内存不足）
- **症状**: 内存飙升到 22-30GB，进程被 kill
- **原因**: 无 VAD/分片时，FunASR 整段处理长音频，内存随长度二次方增长
- **修复**: 优先使用 `FUNASR_CHUNK_SECONDS=60`（最佳值）

### 3. 幻觉/重复文字
- **症状**: 转录中出现 "高估高估高估..." 等大量重复
- **原因**: 单段音频太长（>3 分钟），模型注意力退化
- **修复**: 使用 `FUNASR_CHUNK_SECONDS=60`（最佳值）

### 4. model.py 找不到
- **搜索路径优先级**:
  1. `FUNASR_MODEL_PY_PATH` 环境变量
  2. `./model.py`（项目根目录）
  3. `~/.cache/modelscope/hub/models/FunAudioLLM/Fun-ASR-Nano-2512/model.py`
  4. funasr 包内置 `funasr/models/fun_asr_nano/model.py`

### 5. sentence_timestamp 导致失败并降级
- **症状**: 日志出现 `sentence_timestamp=True 转录失败（0）...`。
- **建议**: 直接固定 `FUNASR_SENTENCE_TIMESTAMP=0`，不要在生产任务中使用 `auto` 试错。

### 6. worker 噪声输出告警（可选场景）
- **症状**: 看到“收到无法解析的 worker 输出”之类告警。
- **原因**: worker 协议期望 stdout 为 JSON，第三方库文本输出混入会被忽略。
- **建议**: 在当前稳定基线继续使用 `FUNASR_USE_WORKER=0`；若调试 worker，保留 `FUNASR_VERBOSE=0`。

## .env 关键配置说明

```bash
FUNASR_DEVICE=cpu               # cpu|mps|auto（推荐 cpu，最稳定）
FUNASR_ENABLE_VAD=0             # 必须关闭（与 Nano 不兼容）
FUNASR_CHUNK_SECONDS=60         # 分片秒数最佳值
FUNASR_SENTENCE_TIMESTAMP=0     # 关闭句级时间戳（Nano 支持不稳定）
FUNASR_USE_WORKER=0             # 当前稳定基线：0=CLI 模式，1=常驻 worker 模式
FUNASR_FALLBACK_TO_CLI_ON_WORKER_ERROR=0  # 建议保持 0，避免失败后重复加载
FUNASR_MODEL_PY_PATH=           # 可选，model.py 路径（留空自动搜索）
FUNASR_MODEL_DIR=               # 可选，本地模型目录
```
