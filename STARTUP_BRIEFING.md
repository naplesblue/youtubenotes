# Codex 启动说明（可直接复制到新会话）

> **详细项目文档**：`.agents/SKILL.md`
> **工作流参考**：`.agents/workflows/run-pipeline.md`、`.agents/workflows/debug-funasr.md`

## 你正在接手的项目
- 项目路径：`/Users/Naples/YoutubeNotes`
- 项目目标：自动处理 YouTube 财经频道内容，形成结构化分析并同步到 Obsidian
- Python 环境：`/Users/Naples/YoutubeNotes/ytbnotes/bin/python3`（uv 虚拟环境）
- 当前主流程（Python）：
  1. `youtube_downloader.py`：每频道仅拉 RSS 最新 1 条，按 3 天窗口维护下载状态
  2. `audio_analyzer.py`：`auto|subtitle|asr` 路由，字幕优先（仅人工英文字幕）+ ASR 回退
  3. `obsidian_sync.py`：同步分析结果到 Obsidian

## 重要事实（避免误判）
- `obsidian_sync.js` 和 `lib/*.js` 是历史遗留实现，当前主流程不使用
- 当前运行链路为 Python：`youtube_downloader.py -> audio_analyzer.py -> obsidian_sync.py`
- `config.yaml` 中 `paths.vault` 指向真实 Obsidian 路径（外部磁盘）

## 当前目录与核心文件
- 下载与记录：`youtube_downloader.py`、`channels.yaml`、`download_history.json`
- 分析：`audio_analyzer.py`、`funasr_transcribe.py`、`analysis_log.json`、`analysis_results/`
- 同步：`obsidian_sync.py`、`config.yaml`、`lib/config/*.py`、`lib/core/*.py`
- 环境与依赖：`.env`、`.env.example`、`requirements.txt`

## 当前关键策略（已实现）
1. 下载侧：
- 每频道仅处理 RSS 最新 1 条
- 仅保留近 3 天 `download_history.json` 状态
- 每频道仅保留一条最新记录
- 自动清理下载目录中的旧/孤儿媒体文件（可用 `YTDLP_CLEANUP_DOWNLOAD_FILES=0` 关闭）

2. 分析侧：
- `TRANSCRIPT_MODE=auto|subtitle|asr`（默认 `auto`）
- `auto` 模式优先探测人工英文字幕（`subtitles`），明确排除 `automatic_captions`
- 字幕通过质量门槛后直接走文本分析（中文输出），否则回退 ASR
- `analysis_log.json` 记录 `transcript_source`、`subtitle_probe_result`、`fallback_reason`

3. FunASR 稳定策略：
- `FUNASR_ENABLE_VAD=0`（与 Nano 组合不稳定）
- `FUNASR_DEVICE=cpu`（稳定优先）
- `FUNASR_SENTENCE_TIMESTAMP=0`（稳定优先）
- `FUNASR_USE_WORKER=0`（当前稳定基线：禁用常驻 worker，避免长期内存占用）
- `FUNASR_CHUNK_SECONDS=60`（长音频分片最佳值）

4. 同步侧稳健性策略（obsidian_sync.py）：
- 视频简报与完整转录分目录写入，并建立双向链接
- 股票概览按 ticker 聚合时间线（新在前，单票单文档）
- 同步前统一执行 ticker 清洗与映射，配置入口 `config.yaml -> processing.ticker_aliases`
- 人物笔记从 `mentioned_tickers.analyst` 自动提取分析师名创建

5. Obsidian 视觉增强：
- MOC 索引升级为仪表盘（热门标的表、最新视频、Mermaid 情绪饼图、频道列表）
- 视频笔记 frontmatter 不再包含 summary（仅在 body 渲染）
- 股票概览新增 Mermaid xychart 情绪趋势图（≥2 个数据点时自动生成）
- 价格水平新增 callout 价位区间图（目标/阻力/支撑可视化）

## 主流程最小环境变量
- 必填：`DASHSCOPE_API_KEY`
- 推荐：
  - `TRANSCRIPT_MODE=auto`
  - `FUNASR_ENABLE_VAD=0`
  - `FUNASR_DEVICE=cpu`
  - `FUNASR_SENTENCE_TIMESTAMP=0`
  - `FUNASR_USE_WORKER=0`
  - `FUNASR_CHUNK_SECONDS=60`
  - `YTDLP_CLEANUP_DOWNLOAD_FILES=1`
- 可选：
  - `SUBTITLE_PREFERRED_LANGS=en,en-us,en-gb`
  - `SUBTITLE_MIN_CHARS=800`
  - `SUBTITLE_MIN_CUES=30`
  - `SUBTITLE_MIN_COVERAGE=0.60`
  - `SUBTITLE_MIN_ENGLISH_RATIO=0.70`

## 常用运行命令
```bash
cd /Users/Naples/YoutubeNotes
source ytbnotes/bin/activate

# 一键执行全流程
python run_pipeline.py

# 跳过下载，只分析+同步
python run_pipeline.py --skip-download

# 只同步到 Obsidian
python run_pipeline.py --only-sync

# 分步执行（仍可用）
python youtube_downloader.py
python audio_analyzer.py
python obsidian_sync.py
```

## 当前已知限制 / 待办
- 人物笔记“仅首次创建”，不会自动增量更新历史出现记录
- `test_run.py` 旧断言与当前结构化 JSON 不一致
- 字幕路径依赖 YouTube 可用人工英文字幕；无字幕或质量不达标时会回退 ASR
- Obsidian 同步每次全量重建，视频数量增多后可考虑增量同步优化

## 🚨 核心架构红线 (防劣化约束)
为避免在后续重构迭代中破坏系统稳定性，请严格遵守以下规则：
1. **保留 ffmpeg 长音频物理切片**：绝不可试图强行完整长音频喂给 `Fun-ASR` 或强开 VAD 处理，底层模型存 VAD Bug (KeyError: 0)，而无 VAD 时 24G M4 加载巨型段必出 OOM 内存溢出。
2. **限制 Worker 的常驻策略**：当前稳健性基线为 `FUNASR_USE_WORKER=0`，请维持 CLI 即拉即用的子进程方式防止内存暴涨。
3. **严禁信任原始 LLM Tickers**：同步流中必须经过 `processing.ticker_aliases` 校验清洗，防止如 `$AAPL` 等格式不规整标签污染 Obsidian 内部双链生态。



## 最近验证（2026-03-09）
- 使用真实 `analysis_results` 共 15 个 JSON 全量同步验证，`processed=15`、`errors=0`
- MOC 仪表盘已生成（热门标的 15 只、情绪饼图、频道列表）
- 股票概览已生成情绪趋势图（如 TSLA 6 次时间线数据点）
- 价格水平已生成价位区间可视化 callout
- 人物笔记已从 analyst 字段自动创建

## 给新会话的建议开场语（可复制）
```text
请先阅读 /Users/Naples/YoutubeNotes/.agents/SKILL.md 和 STARTUP_BRIEFING.md，再继续本次任务。
当前主流程是 Python：youtube_downloader.py -> audio_analyzer.py -> obsidian_sync.py。
下载侧已是“每频道最新1条 + 3天窗口 + 自动清理旧媒体”。
分析侧支持 TRANSCRIPT_MODE=auto，优先人工英文字幕（排除 automatic captions），失败回退 ASR。
同步侧已加 ticker 清洗、Obsidian 仪表盘、情绪图表、价位可视化、分析师笔记自动创建。
```
