# 🏗 项目架构与数据流 (Architecture Docs)

YoutubeNotes 被设计为一条严格的、低耦合的数据流水线系统。它可拆卸组合，且每步保持幂等或持久化状态日志。

## 一、系统流向拓扑 (Data Flow)

整个系统分为三大明确的阶段（Phase），通过根目录下的强类型协调脚本 `run_pipeline.py` 进行解耦调度。

1. **Phase 1: Download (下载期)**
   - **入口模块**: `src/ytbnotes/downloader/`
   - **执行过程**: 解析 `channels.yaml` 的 RSS 源 → 抓取近三天的候选集 → 与 `data/download_history.json` 比对去重 → 利用 `yt-dlp` 下载音频媒体至 `data/downloads/` → 更新 `download_history.json`。
   - **状态处理**: 支持通过 `.env` 中的 `YTDLP_COOKIES_PATH` 设置规避 YouTube Login Required 限制策略。

2. **Phase 2: Analyze (分析提取期)**
   - **入口模块**: `src/ytbnotes/analyzer/`
   - **执行过程**:
     - 检索出未被分析过的音频并提取视频元数据。
     - **转录引擎路由 (Subtitle / Transcriber)**: 试图下载自带的精确人工英文字幕；若失败则剥离原始音频 `ffmpeg` 到 `data/audio/`，唤起基于 `Fun-ASR-Nano` 的本地进程完成全长语音带时间戳的 STT 转录。
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
  - `downloader.py`: `feedparser` 与 `yt-dlp` 的直接交互方。
- `.transcribe.`
  - `funasr.py`: 处理极长视频时使用的切片重组机制工具类（避免内存溢出），支持 Worker 持驻留机制和按需冷启。
- `.analyzer.`
  - `config.py` & `metadata.py`: 管理和推算上下文的统一常量源。
  - `subtitle.py`: 解析 VTT，执行质量过滤门限判定。
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
