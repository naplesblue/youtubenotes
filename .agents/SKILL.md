---
name: YoutubeNotes
description: YouTube 财经视频自动化处理工具链：频道 RSS 下载 → 本地 ASR 转录 → Qwen 文本分析 → Obsidian 知识库同步
---

# YoutubeNotes 项目技能文档

## 项目概述

将 YouTube 财经频道视频自动转化为 Obsidian 笔记，核心流程全 Python 实现，架构解耦为四个核心功能域包 (`downloader`, `transcribe`, `analyzer`, `sync`)。

```text
├── run_pipeline.py          # 顶层应用执行门户
├── .env.example             # 密钥与运行时配置示例
├── config.example.yaml      # Obsidian 同步和过滤相关配置示例
├── pyproject.toml           # 现代 Python 依赖及打包配置
├── channels.yaml            # 指定监控的 YouTube 频道的 RSS 配置
│
├── src/
│   └── ytbnotes/
│       ├── downloader/      # RSS 源订阅与 yt-dlp 调度下载
│       ├── transcribe/      # FunASR 子进程和 Worker 管理
│       ├── analyzer/        # 核心：控制流、字幕探测、LLM 解析和文件生成 
│       └── sync/            # 转换本地输出，构建 Obsidian Markdown 文件和双链
│
├── tools/                   # 独立功能及运维辅助脚本
├── docs/                    # 分类说明文档
└── data/                    # (运行时产生) JSON数据库、中间音频与分析摘要
```

## 运行环境

- **macOS Apple Silicon** (M4 Pro, 24GB RAM)
- **Python**: 3.10+ (可通过 `uv` 等工具管理)
- **依赖**: 
  - 系统依赖: `ffmpeg` (处理音频), `yt-dlp` (下载源)
  - 库依赖见 `pyproject.toml` (可执行 `pip install .`)

## 核心脚手架概念和命令

在重构后的最新结构中，不建议再直接运行子模块，而是一并交给流水线入口脚本 `run_pipeline.py` 进行调度。

```bash
# 全功能启动
python run_pipeline.py

# 取消下载直接运行分析和同步 (适用重设 Prompt 后测试)
python run_pipeline.py --skip-download

# 仅同步到 Obsidian (适用于仅调整 config.yaml 和模板)
python run_pipeline.py --only-sync

# 沙盒预览执行项
python run_pipeline.py --dry-run
```

## 数据持久化与缓存

不再使用项目根目录散落方式，现在所有状态文件均集中于 `data/` 目录：
- `data/download_history.json`: 防重复下载追踪。
- `data/analysis_log.json`: 防重复送入 LLM 计算资源开销。
- `data/downloads/`: yt-dlp 拉取的原始媒介缓存 (支持自动剔除配置 `YTDLP_CLEANUP_DOWNLOAD_FILES`).
- `data/audio/`: 裁剪转置后的本地音频，用于 ASR，解析后销毁。
- `data/results/`: JSON 元数据 和 Markdown 中间态文本，交付 Sync 层映射至 Obsidian。

## Web 仪表盘

独立于 Obsidian 的全景数据仪表盘（Astro + React islands + Tailwind v4 + Recharts + TanStack Table）。

```bash
# 聚合最新数据 → web/public/data/dashboard.json
python tools/build_dashboard_data.py

# 静态构建 → web/dist/（可部署 GitHub Pages）
cd web && npm run build

# 本地开发预览
cd web && npm run dev
```

页面：概览(/) | 博主排行(/bloggers/) | 博主详情 | Ticker网格(/tickers/) | 个股详情 | 数据质量(/quality/)

## Obsidian 视觉特征

渲染逻辑由 `src/ytbnotes/sync/` 包全权接管，包含：
1. **MOC 仪表盘**: 生成包含高频 Mention 实体表、情绪 Mermaid 复合分布图的入口级索引文件。
2. **多态双链**: 独立实体通过 Alias `$AAPL`/`NASDAQ:MSFT` 等路由统一洗尽铅华并建立 `股票概览文件`。
3. **时序叠加**: 文件追加行为是幂等的 (`merge` 策略)，多次视频的原子点位会自动聚合到统一笔记的时间线内。

## 🚨 关键架构约束与防劣化指南 (Do Not Modify)

未来的 Agent 在重构、修复 Bug 或优化代码时，请务必遵守以下通过多次踩坑得出的架构红线。如果强行修改此处的默认保护机制，将极容易导致严重的系统崩溃与内存溢出 (OOM)：

1. **FunASR 内存爆满与长音频 VAD Bug**:
   - **问题源**: 官方支持库 `funasr==1.3.1` 配合本项目的 `Fun-ASR-Nano-2512` 模型时，直接调用带有 Voice Activity Detection 的 `inference_with_vad()` 处理音频会报错引发 `KeyError: 0`。
   - **长音频 OOM**: 禁用 VAD 后，如果直接将超过 10 分钟的大长视频喂入模型，会在 M4 的 24G 统一内存机器上直接引发 `Out Of Memory` 全局崩溃。
   - **现行强制保护方案/切勿破防**: 我们使用原生的 `ffmpeg` 在 `src/ytbnotes/transcribe/funasr.py` 对巨型音频做物理静态大分段切片（推荐通过 `FUNASR_CHUNK_SECONDS` 控制在 60-300 秒），分段输送并发给无需 VAD 的底模，并最后由 Python 代码拼装全局 `offset` 偏移。**请绝对不要为了“优化速度”强行撤掉这个分片合并层。**

2. **常驻 Worker 环境稳定性**:
   - 包含常驻 Worker 池代码逻辑 (`FunASRWorkerClient`) 的意图是减少庞大模型冷启耗时。
   - **当前生产基线**: `FUNASR_USE_WORKER=0`。单次 CLI CLI 子进程具有最好的天然内存释放特性；只要重构不要求极高的并发实时速度，就继续维持单播方式。
   - **防死循环重启**: 务必保持 `FUNASR_FALLBACK_TO_CLI_ON_WORKER_ERROR=0`，以防止 worker 遇到极其罕见的音频特征崩溃后，再次唤起同等的 CLI 进行自杀式内存吞噬。

3. **Ticker 清洗引擎的必要性**:
   - 不要轻易信任 LLM 返回的结构化 JSON 里提取出来的纯量股票代码 (`ticker`)，如 `$AAPL`, `NASDAQ:MSFT`, `BRK/B` 甚至是噪音 `CIRCLE`。
   - `obsidian_sync` 层必须经过 `config.yaml -> processing.ticker_aliases` 和正则清洗步骤，这是防止 Obsidian 图谱中产生“幽灵畸形文件”和“污染 Ticker”的唯一防线。请不要从同步逻辑中删去这个归一化校验器。
