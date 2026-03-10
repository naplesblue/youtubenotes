---
description: 运行完整的 YouTube 财经视频处理流程（下载 → 转录 → 分析 → 同步）
---

# 运行完整流程

## 增量说明（2026-03）
- 保留原流程定义，本页新增的是当前默认策略：
  - 下载侧：每频道仅拉最新 1 条，且仅处理最近 3 天窗口。
  - 分析侧：默认 `TRANSCRIPT_MODE=auto`，优先人工英文字幕，失败回退 ASR。
  - ASR：当前稳定运行基线为 `FUNASR_USE_WORKER=0` + `FUNASR_ENABLE_VAD=0` + 脚本内分片转录。

## 前置条件
- 虚拟环境已激活：`source /Users/Naples/YoutubeNotes/ytbnotes/bin/activate`
- `.env` 已配置 `DASHSCOPE_API_KEY`
- `channels.yaml` 已配置目标频道

## 步骤

// turbo-all

1. 下载新视频音频
```bash
cd /Users/Naples/YoutubeNotes && python youtube_downloader.py
```
输出目录：`youtube_downloads/<频道名>/`，记录写入 `download_history.json`
- 当前策略补充：
  - 每频道只看 RSS 最新 1 条
  - 超过 3 天窗口的视频不处理
  - `download_history.json` 自动裁剪为“3天内 + 每频道1条”
  - 默认自动清理旧/孤儿下载媒体（`YTDLP_CLEANUP_DOWNLOAD_FILES=1`）

2. 音频分析（字幕优先/ASR 回退 + LLM 摘要）
```bash
cd /Users/Naples/YoutubeNotes && python audio_analyzer.py
```
- 默认先探测人工英文字幕（排除 automatic captions），字幕可用且达标则直接文本分析
- 字幕不可用或不达标时回退到本地 FunASR
- ASR 使用本地 Fun-ASR-Nano-2512（自动分片，推荐每段 60s）
- 当前稳定配置建议禁用常驻 worker（`FUNASR_USE_WORKER=0`），避免长时间占用内存
- LLM 使用 DashScope Qwen API
- 输出：`analysis_results/<频道>/<日期>/` 下的 `.md` + `.json`
- 记录写入 `analysis_log.json`

3. 同步到 Obsidian
```bash
cd /Users/Naples/YoutubeNotes && python obsidian_sync.py
```

## 常见问题

### ASR 转录结果有大量重复文字
将 `.env` 中 `FUNASR_CHUNK_SECONDS` 设为 60（最佳值）：
```bash
FUNASR_CHUNK_SECONDS=60
```

### ASR 报 KeyError: 0
确保 `.env` 中 `FUNASR_ENABLE_VAD=0`（funasr 内置 VAD 与 Nano 模型不兼容）

### worker 失败后反复重试导致内存飙升
建议在 `.env` 保持以下配置：
```bash
FUNASR_USE_WORKER=0
FUNASR_FALLBACK_TO_CLI_ON_WORKER_ERROR=0
FUNASR_DEVICE=cpu
FUNASR_ENABLE_VAD=0
FUNASR_SENTENCE_TIMESTAMP=0
FUNASR_CHUNK_SECONDS=60
```

### 只想处理某个特定视频
```bash
ANALYZER_ONLY_VIDEO="视频标题关键词" python audio_analyzer.py
```

### 限制单次处理数量
```bash
ANALYZER_MAX_VIDEOS=3 python audio_analyzer.py
```
