# 🏗 项目架构与数据流 (Architecture Docs)

YoutubeNotes 被设计为一条严格的、低耦合的数据流水线系统。它可拆卸组合，且每步保持幂等或持久化状态日志。

## 一、系统流向拓扑 (Data Flow)

整个系统分为三大明确的阶段（Phase），通过根目录下的强类型协调脚本 `run_pipeline.py` 进行解耦调度。

1. **Phase 1: Download (下载期)**
   - **入口模块**: `src/ytbnotes/downloader/`
   - **执行过程**: 解析 `channels.yaml` 的 RSS 源 → 抓取近三天的候选集 → 与 `data/download_history.json` 比对去重 → **字幕优先探测**（见下方） → 字幕不可用时回退 `yt-dlp` 下载音频 → 更新 `download_history.json`。
   - **字幕优先路径** (`SUBTITLE_FIRST_ENABLED=1`，默认开启): 发现新视频后，先调用 `analyzer.subtitle.load_subtitle_transcript()` 探测字幕可用性并下载。若字幕质量达标，直接保存文本到 `data/subtitles/`，设 `input_type=subtitle`，跳过音频下载。若字幕不可用，根据 `SUBTITLE_TO_ASR_FALLBACK` 决定是否回退音频下载（设 0 则跳过该视频）。整个探测路径有 try/except 保护，异常时自动回退音频下载。
   - **RSS 回退策略**: YouTube RSS 提要偶发返回 404 或空条目。当 `feedparser` 返回空结果时，会可选地切换至 **YouTube Data API v3**（`channels.list` + `playlistItems.list`）查询频道最新视频。需在 `.env` 中配置 `YOUTUBE_DATA_API_KEY`（可选但强烈推荐）。
   - **状态处理**: 支持通过 `.env` 中的 `YTDLP_COOKIES_PATH` 设置规避 YouTube Login Required 限制策略。
   - **文件清理**: 新视频替换旧视频时，旧音频文件清理受 `CLEANUP_DOWNLOAD_FILES` 控制，旧字幕文件清理受 `CLEANUP_SUBTITLE_FILES` 控制，两者独立。

2. **Phase 2: Analyze (分析提取期)**
   - **入口模块**: `src/ytbnotes/analyzer/`
   - **执行过程**:
     - 检索出未被分析过的输入文件并提取视频元数据。
     - **输入分流**: 根据 `input_type` 字段（由 Phase 1 写入 tracking 记录）决定路径：
       - `input_type == "subtitle"`: 直接读取 `subtitle_path` 指向的 `.txt` 文件作为转录文本，跳过 ASR。
       - `input_type == "audio"`: 走传统 ASR 转录路径（见下方）。
     - **ASR 转录回退路径**: 将原始音频经 `ffmpeg` 到 `data/audio/`，唤起基于 `Fun-ASR-Nano` 的本地进程完成全长语音带时间戳的 STT 转录。
     - **大模型抽取 (LLM Processor)**: 提取文本提交至 DashScope/OpenAI 兼容接口处理，执行 prompt 组装，提取：`精炼文本`、`摘要列表`、`Tickers（带分析情绪）`、`Price Levels（原子化的价格预测点位）`。
     - **输出落盘**: 原料组合成完整的结构化字典落盘为 `.json` 及中继版本 `.md` 存入 `data/results/`，并在 `data/analysis_log.json` 中登记完成状态。

3. **Phase 3: Synchronize (Obsidian 知识同步期)**
   - **入口模块**: `src/ytbnotes/sync/`
   - **执行过程**: 通过比对 `data/results/` 的解析件与目标 `Vault` 状态：
     - 构建与覆盖 `视频核心笔记` 和脱水的 `完全转录笔记`。
     - 扫描涉及的相关股票信息，如果触发自动生成，则提取元信息自动组装 `股票概览笔记`（包含该 ticker 的关联分析历史、Mermaid情绪趋势图等）。
     - 通过 `graph_manager.py` 向中枢网络维护实体之间的强链接关系网。
     - 渲染和覆盖顶级聚合的 `MOC-(Map Of Content)` 主控台看板页。

## 二、关键模块 (Sub-Packages)

各个组件隔离在 `src/ytbnotes/` 并通过 `run_pipeline.py` 利用 `subprocess` （或直接引用）发起任务来限制异常扩散。

- `.downloader.`
  - `downloader.py`: `feedparser` 与 `yt-dlp` 的直接交互方；内嵌字幕优先探测（调用 `analyzer.subtitle.load_subtitle_transcript`）和 YouTube Data API v3 回退逻辑。
- `.transcribe.`
  - `funasr.py`: 处理极长视频时使用的切片重组机制工具类（避免内存溢出），支持 Worker 持驻留机制和按需冷启。
- `.analyzer.`
  - `config.py` & `metadata.py`: 管理和推算上下文的统一常量源。
  - `subtitle.py`: 按语言优先级链抩取人工字幕（英文/中文）并解析 VTT，执行语言族质量过滤门限判定（英文检查 `english_ratio`，中文检查 `zh_ratio`）。自动字幕（auto_captions）默认开启但受语言族白名单 `SUBTITLE_AUTO_CAPTION_LANG_FAMILIES` 限制，仅白名单内的语言族（默认 `en`）允许使用自动字幕，中文等非白名单语言在候选阶段即被排除。**被 downloader 和 analyzer 共同调用**，是跨包共享模块。
  - `llm_processor.py`: Prompt 工程存储地，执行 Dashscope 兼容协议。
  - `result_writer.py`: 负责序列化聚合的所有 LLM Raw / Refined / Entity 数据，并在产生异常时保证写入原子性。
- `.sync.`
  - `sync.py`: 核心转换机与 Obsidian 内容维护者。
  - `path_resolver.py`: 将目标环境在 Vault 的路由动态编织相对路经以防链接受损。
  - `note_renderer.py` / `graph_manager.py`: Jinja/Markdown 生成与实体关联图记录方。

## 三、防劣化与架构约束 (Architecture Constraints)
系统在核心节点上设定了防御性约束，请在后续迭代中严格遵守：
1. **长音频 OOM 与 VAD 崩溃防范**: 因底层 `Fun-ASR-Nano` 在 M4 机器上的内存瓶颈和 VAD 兼容性问题，必须依赖 `ffmpeg` 进行音频物理分片 (`FUNASR_CHUNK_SECONDS`) 并在外部拼装全局时间戳，严禁强行直连超大音频或开启 VAD。
2. **Worker 资源管控**: 推荐维持 `FUNASR_USE_WORKER=0` 进行按需子进程拉起，避免常驻内存泄露。
3. **严格 Ticker 归一化**: 必须途径 `obsidian_sync` 层处理配置表中的 `ticker_aliases` 和正则清洗，严禁直接信任 LLM 输出的原始代码字符，以防在知识库中扩散僵尸污染点位。

## 四、运维工具脚本 (tools/)

`tools/` 目录下的脚本均为独立运行、不影响主流程的辅助工具：

| 脚本 | 功能 |
|--------|-------|
| `run_discovery.sh` | **频道发现&筛选编排脚本**：串联 `discover → screen → report` 三步，可交给 AI 定时运行。输出结构化报告到 `brain/discovery_report.md`。 |
| `discover_channels.py` | **候选频道发现**：通过 YouTube Data API 搜索关键词 + 种子频道 featured channels 关系链，自动去重后输出到 `brain/candidates_discovered.yaml`。 |
| `channel_screen.py` | **频道旁路快筛**：输入候选频道，自动拉取近 5 期视频 → 字幕探测 → Cerebras gpt-oss-120b 轻量打分（观点密度、价位、持仓、噪声等） → 按评分自动分流到 `channels.yaml`（合格）/ `brain/candidates_watchlist.yaml`（观察）/ `brain/candidates_rejected.yaml`（淘汰）。支持批量模式和 `--dry-run`。 |
| `youtube_rss.py` | 输入任意视频链接，通过 `yt-dlp` 反查该频道的 `channel_id` 和 RSS 地址。**用于配置 `channels.yaml`。** |
| `extract_cookies.py` | 从本地 Chrome 浏览器导出 YouTube/Google 登录 Cookie，生成 `yt-dlp` 可直接使用的 Netscape 格式文件。需先安装 `browser-cookie3`。 |
| `backfill_json.py` | 将 `data/results/` 中历史 Markdown 笔记的精炼文本和完整转录回填到对应的结构化 JSON。**默认 dry-run，加 `--apply` 才执行写入。** |
| `migrate_notes.py` | 一次性迁移脚本：将旧版平铺式 Obsidian 视频笔记重整为按频道分目录 + 简报/转录分离的新结构。旧笔记会先备份。**默认 dry-run，加 `--apply` 才执行。** |
