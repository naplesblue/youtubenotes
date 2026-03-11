# YoutubeNotes 🎬 📈

**YoutubeNotes** 是一款由 Python 编写的自动化 YouTube 财经视频信息提取与总结流水线。它可以监控指定的 YouTube 频道（通过 RSS），自动下载最新视频与音频，利用大语言模型（默认 Qwen）或本地语音识别（FunASR）生成结构化的摘要和图谱，并将结果直接无缝同步到本地的 Obsidian 知识库中。

> 核心链路: `RSS 下载 (YouTube Data API 备用) -> 字幕优先抓取 (中/英文人工字幕) -> 本地 ASR 转录兜底 -> 大模型摘要及提取 -> Obsidian Vault 同步与双链生成`

---

## ✨ 核心特性

- **🚀 全自动化端到端流水线**: `run_pipeline.py` 一键无缝触发从下载、分析、提取到 Obsidian 渲染的完整流水线。
- **📺 智能下载调度**: 利用 `feedparser` 解析 YouTube RSS 提要，通过 `yt-dlp` 高效地拉取最新视频。
  - **RSS 稳定性回退**: YouTube RSS 提要偶发 404 或返回空数据。当检测到故障时，会自动切换至 **YouTube Data API v3**（需配置 `YOUTUBE_DATA_API_KEY`，可选但推荐申请）查询频道最新视频，保证下载流程不中断。
  - 支持 Cookie 注入和反封锁规避策略。
- **🤖 三级降级转录策略 (Transcript Mode)**:
  - 优先探测并提取最高质量的**人工字幕**（支持英文 `en/en-US` 和中文 `zh-Hans/zh/zh-TW`，优先级可配置）。
  - 中文字幕与英文字幕分别采用独立的质量门（中文检测 `zh_ratio`，英文检测 `english_ratio`），确保语言判断准确不误杀。
  - 字幕不可用或质量不达标时，自动回退至本地轻量级 ASR 模型 (`Fun-ASR-Nano`) 实现高精度时间线转录。
  - 支持超长音频 Chunk 分块与多进程调度加速。
- **🧠 结构化数据提取**: 借助 `Qwen-Plus`（兼容 OpenAI API）大语言模型：
  - 提取关键带时间戳摘要
  - 在上下文识别并分析被提及的股票/加密货币点位（Ticker / Price Levels）
  - 生成实体级关系网络，并对错误 Ticker 实施自动映射清洗。
- **📓 Obsidian 深度整合**:
  - 生成精美的 MOC（Map of Content）仪表盘索引
  - 按 视频笔记、特定实体(股票/ETF)、特定人物及价位区间自动生成双链互联的文件群。
  - 内置 Mermaid 情绪趋势图以可视化市场预测。

## 📖 快速索引

为了避免文档冗余，详细内容按模块划分在 `docs/` 目录下：

- [**架构设计 (Architecture & Module Responsibilities)**](./docs/architecture.md)
- [**安装部署指南 (Setup & Configuration)**](./docs/setup.md)

## ⚡ 快速上手

请首先确保环境具备 `Python 3.10+` 和系统级依赖 `ffmpeg`、`yt-dlp`。
详见 [详细安装部署指南](./docs/setup.md)。

# 复制示例配置文件
cp .env.example .env
cp config.example.yaml config.yaml
cp channels.example.yaml channels.yaml
```

**运行一键流水线:**
```bash
# 全流程（下载 -> 分析 -> 同步）
python run_pipeline.py

# 预览执行计划
python run_pipeline.py --dry-run

# 仅执行同步阶段（如果你刚修改了 Obsidian 的 config.yaml 配置或是调试生成内容）
python run_pipeline.py --only-sync
```

## 📂 项目模块结构

项目遵循标准的 Python 开源套件结构，业务核心位于 `src/ytbnotes/`：

```text
YoutubeNotes/
├── run_pipeline.py          # 顶层应用执行门户
├── .env.example             # 密钥与运行时配置示例
├── config.example.yaml      # Obsidian 同步和过滤相关配置示例
├── channels.example.yaml    # 指定监控的 YouTube 频道的 RSS 配置示例
├── pyproject.toml           # 现代 Python 依赖及打包配置
│
├── src/
│   └── ytbnotes/
│       ├── downloader/      # RSS 源订阅与 yt-dlp 调度下载
│       ├── transcribe/      # FunASR 子进程和 Worker 管理
│       ├── analyzer/        # 核心：控制流、字幕探测、LLM 解析和文件生成 
│       └── sync/            # 转换本地输出，构建 Obsidian Markdown 文件和双链
│
├── tools/                   # 独立功能及运维辅助脚本
│   ├── youtube_rss.py       # 通过单个视频链接反查频道 RSS 地址（用于 channels.yaml 配置）
│   ├── extract_cookies.py   # 从 Chrome 导出 YouTube 登录 Cookie 为 yt-dlp 可用的 Netscape 格式
│   ├── backfill_json.py     # 将历史 Markdown 笔记中的 brief_text / raw_transcript 回填到对应 JSON（dry-run 模式默认安全）
│   └── migrate_notes.py     # 一次性迁移脚本：将旧版 Obsidian 平铺视频笔记重整为按频道分目录 + 简报/转录分离的新结构
├── docs/                    # 分类说明文档
└── data/                    # (运行时产生) JSON数据库、中间音频与分析摘要
```

## 📄 License协议
本项目遵循 [MIT License](./LICENSE)。

## 🚨 注意事项与架构约束 (Architecture Constraints)
为了保证本系统能在 M4 Apple Silicon 设备上稳定运行，特作以下约束声明：
- **禁止移除音频分片逻辑**: 因 `Fun-ASR-Nano` 在 M4 处理 10 分钟以上长音频（无 VAD）时会引起 Out Of Memory 崩溃，且其底层具备 VAD KeyError Bug。因此请保留由 `ffmpeg` 分片 (`FUNASR_CHUNK_SECONDS`) 的预处理调度。
- **限制后台并发常驻进程**: 建议生产环境维持 `FUNASR_USE_WORKER=0`，以即拉即销的进程模型避免常驻内存泄露。
- **强制约束 Obsidian 数据清洗**: Obsidian 同步必须途经 `ticker_aliases` 校验。禁止直接信任 LLM 输出的原始股票代码标签 (如包含 `$AAPL`、`BRK/B` 甚至是幻觉实体)，以防坏账扩散。
