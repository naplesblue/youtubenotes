# YoutubeNotes — 开发交接文档

> 本文档用于 AI 接手开发时快速了解项目现状，避免从零研读所有代码。  
> 版本：2026-03-13 | 对应 commit: `b0362df` + 未提交修改

---

## 一、项目概况

YoutubeNotes 是一个自动化工具链：

```
订阅 YouTube 财经频道
→ 字幕优先探测（Phase 1 内完成，避免无效音频下载）
→ 字幕不可用时回退音频下载 + 本地 ASR 转录
→ Qwen LLM 生成简报 + 提取个股观点 (mentioned_tickers)
→ Cerebras GPT-OSS-120B 精标注观点 (opinions)
→ yfinance 拉取行情，30d/90d/180d 回验胜率
→ 输出到 Obsidian 笔记库（视频简报 + 股票概览 + 胜率仪表盘）
```

---

## 二、目录结构

```
YoutubeNotes/
├── run_pipeline.py          # 主流程入口：下载→转录→分析→同步
├── run_tracker.py           # 预测追踪入口：提取→回验→报告(见第四章)
├── tools/
│   ├── run_discovery.sh     # 频道发现&筛选编排脚本（发现→筛选→报告，可定时运行）
│   ├── discover_channels.py # 候选频道发现（YouTube 搜索 + 种子频道关系链）
│   ├── channel_screen.py    # 频道旁路快筛（Cerebras LLM 打分，低成本评估候选频道）
│   ├── cerebras_poc.py      # Cerebras API POC 测试脚本（单文件验证）
│   ├── backfill_json.py     # 回填历史 JSON 到新格式
│   ├── extract_cookies.py   # 导出 Chrome Cookie 供 yt-dlp 使用
│   ├── migrate_notes.py     # 迁移旧版 Obsidian 笔记格式
│   └── youtube_rss.py       # 从视频 URL 反查频道 RSS
├── src/ytbnotes/
│   ├── downloader/          # Phase 1: RSS 下载 + 字幕优先探测 + YouTube Data API 备用
│   ├── transcribe/          # Phase 2a: FunASR 本地转录（字幕不可用时的 ASR 回退）
│   ├── analyzer/            # Phase 2b: Qwen LLM 简报 + mentioned_tickers 提取
│   │                        #   subtitle.py 被 downloader 跨包调用
│   ├── sync/                # Phase 3: Obsidian 笔记库同步
│   ├── tracker/             # Phase 4: 观点提取存储 ← 新增
│   │   ├── models.py        #   数据模型 (Opinion, Prediction, Verification)
│   │   ├── opinion_store.py #   opinions.json 原子读写 + 幂等去重
│   │   └── opinion_extractor.py  # Cerebras 精标注 + 批量回填
│   └── verifier/            # Phase 5: 行情回验 + 胜率评估 ← 新增
│       ├── market_data.py   #   yfinance 日K + 本地缓存
│       ├── evaluator.py     #   30d/90d/180d 窗口回验判定
│       ├── scorer.py        #   博主胜率 + 个股共识聚合
│       └── dashboard.py     #   Obsidian 仪表盘 Markdown 生成
├── data/
│   ├── subtitles/           # 字幕优先路径保存的 .txt 文件（按频道/日期/video_id.txt）
│   ├── results/             # LLM 分析输出 JSON（按频道/日期/video_id.json）
│   ├── opinions/
│   │   ├── opinions.json    # 所有 opinion 记录（主存储，~136 条）
│   │   └── market_cache/   # yfinance 日K缓存（{TICKER}_{年}.json）
│   └── reports/
│       ├── blogger_profiles.json   # 最新博主胜率聚合
│       └── ticker_consensus.json  # 最新个股共识聚合
└── docs/
    ├── architecture.md      # 系统架构文档
    └── dev-handoff.md       # 本文件
```

---

## 三、已完成的开发（Phase 1–5 MVP）

| 阶段 | 状态 | 核心文件 |
|------|------|----------|
| Phase 1: 下载调度 + 字幕优先 | ✅ 完成 | `downloader/downloader.py` |
| Phase 2a: 转录（ASR 回退） | ✅ 完成 | `transcribe/` |
| Phase 2b: LLM 分析 | ✅ 完成 | `analyzer/` |
| Phase 3: Obsidian 同步 | ✅ 完成 | `sync/sync.py` |
| Phase 4: 观点提取 (Cerebras) | ✅ 完成 | `tracker/` |
| Phase 5: 行情回验 + 胜率评分 | ✅ 完成 | `verifier/` |
| Obsidian 仪表盘 | ✅ 完成 | `verifier/dashboard.py` |

**已验证的运行效果：**
- 19 个分析结果 → 136 条 opinions 提取（Cerebras $0.04）
- yfinance 拉取并缓存市场数据
- Obsidian 仪表盘写入 `YoutubeNotes/00-MOC-索引/01-Opinion-个股观点追踪.md`

### 字幕优先重构（2026-03-13，未提交）

**核心变更**：将字幕探测从 Phase 2 (analyzer) 前移到 Phase 1 (downloader)，在决定是否下载音频之前先尝试获取字幕，避免无效的音频下载。

**改动文件**：

| 文件 | 改动内容 |
|------|----------|
| `downloader/downloader.py` | 新增字幕优先路径：探测→质量门控→保存→跳过音频下载；try/except 异常保护回退音频；清理逻辑分离（音频走 `CLEANUP_DOWNLOAD_FILES`，字幕走 `CLEANUP_SUBTITLE_FILES`） |
| `analyzer/main.py` | 读取 `input_type` 分流：`subtitle` 走文件读取，`audio` 走 ASR，互不干扰 |
| `analyzer/metadata.py` | `get_video_metadata` 同时匹配 `file_path` 和 `subtitle_path`，传递 `input_type`/`subtitle_probe_result` |

**新增环境变量**：

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `SUBTITLE_FIRST_ENABLED` | `1` | 启用字幕优先探测 |
| `SUBTITLE_TO_ASR_FALLBACK` | `1` | 字幕不可用时是否回退 ASR（设 0 则跳过视频） |
| `YTDLP_CLEANUP_SUBTITLE_FILES` | `1` | 独立控制旧字幕文件清理 |

**tracking 记录新增字段**：`subtitle_path`、`input_type`（`"subtitle"` / `"audio"`）、`subtitle_probe_result`

**跨包依赖**：`downloader` → `analyzer.subtitle`（`load_subtitle_transcript`）。当前无循环导入风险，后续可考虑将 `subtitle.py` 提升为共享模块。

**已知的 file_path 语义变化**：当 `input_type == "subtitle"` 时，`file_path` 和 `subtitle_path` 指向同一个 `.txt` 文件。下游代码已正确处理，但对维护者可能造成困惑。后续可考虑字幕类型时将 `file_path` 设为 `None`。

### 自动字幕语言族差异化门控（2026-03-13）

**背景**：英文频道若无人工字幕，会回退到音频下载 + 本地 FunASR 转录，浪费时间和算力。YouTube 英文自动字幕质量成熟可靠，应默认启用；但中文自动字幕质量不稳定，应继续拒绝。

**核心变更**：`SUBTITLE_ALLOW_AUTO_CAPTIONS` 默认值从 `0` 改为 `1`，同时新增 `SUBTITLE_AUTO_CAPTION_LANG_FAMILIES` 白名单（默认 `en`），在 `probe_subtitle()` 候选阶段按语言族过滤自动字幕。

**改动文件**：

| 文件 | 改动内容 |
|------|----------|
| `analyzer/config.py` | `SUBTITLE_ALLOW_AUTO_CAPTIONS` 默认值 `0` → `1`；新增 `SUBTITLE_AUTO_CAPTION_LANG_FAMILIES`（env 可配，默认 `{"en"}`） |
| `analyzer/subtitle.py` | `probe_subtitle()` 第二轮 auto_captions 匹配加入 `_lang_family()` 白名单检查，非白名单语言族在候选阶段即跳过 |

**行为变化**：
- 英文自动字幕：默认接受（`en` 在白名单中），质量门控照常通过
- 中文自动字幕：候选阶段被白名单过滤，不进入下载和质量检查
- 设 `SUBTITLE_ALLOW_AUTO_CAPTIONS=0`：恢复旧行为（完全拒绝自动字幕）
- 设 `SUBTITLE_AUTO_CAPTION_LANG_FAMILIES=en,zh`：同时接受英文和中文自动字幕

**不影响**：`subtitle_quality_gate()` 不变，downloader/analyzer 消费侧不变，`source` 字段（`manual`/`auto_caption`）透传不变。

### run_backfill 状态机与缓存稳健性升级（2026-03-13）

**背景**：`run_backfill.py` 原先“下载即记为已处理”，分析失败时会被后续批次永久跳过；同时 YouTube API 调用无超时重试、缓存无 TTL，长跑稳定性不足。

**核心变更**：
- 引入回填状态机：`input_ready` → `analyzed` → `done`，并新增失败态 `failed_analyze` / `failed_sync`。
- 候选筛选改为仅跳过 `status=done`，失败/未完成条目可重试，不再丢任务。
- 回填流程接入字幕优先（与主流程一致）：命中字幕则落盘 `data/subtitles/...` 并设 `input_type=subtitle`，否则回退音频下载。
- YouTube API 接口增加 `timeout + retry + exponential backoff`。
- `backfill_cache.json` 增加 `fetched_at`，支持 TTL 和 `--refresh-cache` 强制刷新。

**改动文件**：

| 文件 | 改动内容 |
|------|----------|
| `run_backfill.py` | 状态机回写、字幕优先输入准备、失败重试语义修复 |
| `run_backfill.py` | `request_json_with_retry()` + `is_cache_entry_usable()` |
| `run_backfill.py` | 新增 CLI 参数 `--refresh-cache`、`--cache-ttl-hours` |

**新增参数**：

| 参数/变量 | 默认值 | 说明 |
|-----------|--------|------|
| `--refresh-cache` | `false` | 忽略本地 `backfill_cache.json`，强制重新拉取频道元数据 |
| `--cache-ttl-hours` | `24` | 缓存 TTL（小时），`0` 表示禁用缓存 |
| `BACKFILL_API_TIMEOUT_SECONDS` | `20` | YouTube API 请求超时（秒） |
| `BACKFILL_API_MAX_RETRIES` | `3` | API 请求最大重试次数 |
| `BACKFILL_API_BACKOFF_SECONDS` | `1.5` | 退避基数（指数退避） |
| `BACKFILL_CACHE_TTL_HOURS` | `24` | `--cache-ttl-hours` 的环境变量默认值 |

**tracking 记录补齐字段**：
- `status`
- `input_type`
- `file_path`
- `subtitle_path`
- `subtitle_probe_result`
- `published_time`

> 说明：为兼容现有 analyzer 逻辑，`input_type=subtitle` 时 `file_path` 仍指向字幕 `.txt`。

### 频道旁路快筛工具（2026-03-13）

**背景**：扩充博主清单需要低成本筛选候选频道。全量 pipeline（ASR + LLM 分析 + Obsidian 同步）太重，需要一个轻量旁路流程快速判断频道是否值得长期追踪。

**新增文件**：`tools/channel_screen.py`

**流程**：
1. 解析候选输入（频道 URL / 视频链接 / channel_id）
2. YouTube Data API 获取最近 N 个视频
3. 复用 `analyzer.subtitle` 探测并下载字幕
4. Cerebras gpt-oss-120b 轻量打分（5 个维度：观点密度、价位具体度、持仓信号、更新频率、噪声比）
5. 频道级聚合评分 → 自动分流

**分流规则**：
- 综合评分 ≥ 50 → 合格，自动追加 `channels.yaml`
- 30-49 → 观察，记录到 `brain/candidates_watchlist.yaml`
- < 30 → 淘汰，记录到 `brain/candidates_rejected.yaml`

**LLM 选型**：使用 Cerebras gpt-oss-120b 而非 Qwen。快筛为英文输入 + JSON 输出的指令遵循任务，不需要中文能力；Cerebras 推理速度约 2000+ tokens/s（~1 秒/视频），比 Qwen API（~30 秒/视频）快一个量级。

**环境变量**：`YOUTUBE_DATA_API_KEY`（必须）、`CEREBRAS_API_KEY`（必须）。可选覆盖：`SCREEN_LLM_BASE_URL`、`SCREEN_LLM_MODEL`。

---

## 四、运行命令

### 主流程
```bash
python run_pipeline.py   # 全流程：下载→转录→分析→同步
```

### 预测追踪流程
```bash
python run_tracker.py extract  # 从已有分析结果提取观点（调 Cerebras）
python run_tracker.py verify   # 拉取行情回验胜率
python run_tracker.py report   # 生成博主排行 + 写入 Obsidian 仪表盘
python run_tracker.py all      # 三步一次性执行
```

### 频道发现 & 筛选工作流
```bash
# 完整工作流：发现候选 → 筛选 → 报告（可交给 AI 定时运行）
bash tools/run_discovery.sh              # 全流程
bash tools/run_discovery.sh discover     # 仅发现候选
bash tools/run_discovery.sh screen       # 仅筛选已发现的候选
bash tools/run_discovery.sh report       # 仅输出当前状态报告
```

### 单频道手动快筛
```bash
# 单频道筛选（频道 URL / 视频链接 / channel_id 均可）
python tools/channel_screen.py "https://www.youtube.com/@SomeChannel"

# 批量筛选（每行一个候选）
python tools/channel_screen.py --batch candidates.txt

# 只打分不写入 YAML
python tools/channel_screen.py --dry-run "https://www.youtube.com/@SomeChannel"
```

### 工具脚本
```bash
python tools/cerebras_poc.py data/results/老李玩钱/20260227/e6pJrGpMNfU.json
# 对单个分析结果做 Cerebras 精标注验证
```

---

## 五、关键配置

### `.env` 变量

| 变量 | 用途 | 是否必须 |
|------|------|----------|
| `OPENAI_API_KEY` | Qwen/分析 LLM | ✅ |
| `CEREBRAS_API_KEY` | GPT-OSS-120B 精标注 + 频道快筛 | ✅ (tracker, 快筛) |
| `YOUTUBE_DATA_API_KEY` | RSS 备用回退 | 推荐 |
| `SUBTITLE_FIRST_ENABLED` | 字幕优先探测开关（默认 1） | 可选 |
| `SUBTITLE_TO_ASR_FALLBACK` | 字幕失败回退 ASR（默认 1） | 可选 |
| `SUBTITLE_ALLOW_AUTO_CAPTIONS` | 允许自动字幕作为兜底（默认 1） | 可选 |
| `SUBTITLE_AUTO_CAPTION_LANG_FAMILIES` | 自动字幕语言族白名单，逗号分隔（默认 `en`） | 可选 |
| `SUBTITLE_MIN_ZH_RATIO` | 中文字幕质量阈值 | 可选 |
| `YTDLP_CLEANUP_DOWNLOAD_FILES` | 旧音频文件清理（默认 1） | 可选 |
| `YTDLP_CLEANUP_SUBTITLE_FILES` | 旧字幕文件清理（默认 1） | 可选 |
| `BACKFILL_API_TIMEOUT_SECONDS` | backfill YouTube API 超时秒数（默认 20） | 可选 |
| `BACKFILL_API_MAX_RETRIES` | backfill API 最大重试次数（默认 3） | 可选 |
| `BACKFILL_API_BACKOFF_SECONDS` | backfill API 指数退避基数（默认 1.5） | 可选 |
| `BACKFILL_CACHE_TTL_HOURS` | backfill 缓存 TTL 小时（默认 24） | 可选 |
| `OPINION_MODEL_NAME` | 默认 `gpt-oss-120b` | 可选 |
| `OPINION_BASE_URL` | 默认 `https://api.cerebras.ai/v1` | 可选 |

### `config.yaml` 结构（关键字段）

```yaml
paths:
  vault: /Volumes/雷电3/OB_知识库/青椒   # Obsidian vault 根目录
  folders:
    index: YoutubeNotes/00-MOC-索引     # MOC 仪表盘写入目录
    videos: YoutubeNotes/02-视频笔记
    stock_overview: YoutubeNotes/01-股票概览
```

> [!IMPORTANT]
> `dashboard.py` 通过 `paths.vault` + `paths.folders.index` 确定写入路径。
> 不是 `vault_root`，不是 `folders`，是嵌套在 `paths` 下。

---

## 六、数据模型速查

### Opinion（核心原子单元）
```python
# src/ytbnotes/tracker/models.py
@dataclass
class Opinion:
    opinion_id: str          # "{video_id}_{ticker}_{type}_{price}_{hash6}"
    video_id: str
    channel: str
    analyst: str
    published_date: str      # "2026-02-27"
    ticker: str
    sentiment: str           # bullish / bearish / neutral
    prediction: Prediction   # 下方
    price_at_publish: float  # 发布日收盘价（由 evaluator 自动填充）
    verification: Verification  # 30d/90d/180d 回验结果

@dataclass
class Prediction:
    type: str        # target_price / entry_zone / support / resistance /
                     #   direction_call / reference_only / stop_loss
    direction: str   # long / short / hold
    price: float     # 入场/支撑/阻力价位
    target_price: float
    stop_loss: float
    confidence: str  # high / medium / low
    horizon: str     # short_term / medium_term / long_term
```

### 胜率判定规则
| 类型 | WIN 条件 |
|------|---------|
| `target_price` | 窗口内曾触及目标价 |
| `entry_zone` | 窗口末价格 > 入场价 且未触发止损 |
| `support` | 窗口内最低价 >= 支撑价 |
| `resistance` | 窗口内最高价 <= 阻力价 |
| `direction_call` | 窗口末方向与预测一致 |
| `reference_only` | 不参与胜率统计 |

---

## 七、待开发任务（第二期）

> 优先级从高到低排列。

### P1: 集成到主流程

**目标**：每次 `run_pipeline.py` 完成后自动运行追踪器。

**文件**：`run_pipeline.py`（末尾加步骤）

```python
# 在 main() 末尾 Phase 3 同步之后：
if args.with_tracker:
    from src.ytbnotes.tracker.opinion_extractor import backfill_all_opinions
    from src.ytbnotes.verifier.evaluator import verify_opinion
    # ... 提取 + 回验 + 报告
```

---

### P2: Prompt 增强（让 Qwen 直接输出精标注字段）

**目标**：减少 Cerebras API 调用，降低成本。让 Qwen 在初次分析时就输出带有 `direction / confidence / horizon / target_price / stop_loss` 的 mentioned_tickers。

**文件**：`src/ytbnotes/analyzer/analyzer.py`（找到 mentioned_tickers 相关的 prompt 部分）

**改动**：在 mentioned_tickers schema 中增加字段：
```json
{
  "ticker": "NVDA",
  "sentiment": "bullish",
  "analyst": "老李",
  "price_levels": [...],
  "direction": "long",          // ← 新增
  "confidence": "medium",       // ← 新增
  "horizon": "medium_term",     // ← 新增
  "target_price": 220.0,        // ← 新增（如有）
  "stop_loss": 164.0            // ← 新增（如有）
}
```

---

### P3: 观点矛盾检测

**目标**：当博主在 30 天内对同一 ticker 发出相反方向观点时，标记为 `conflicted`，在排行榜中显示矛盾次数。

**文件**：`src/ytbnotes/verifier/scorer.py`

**新增函数**：
```python
def detect_contradictions(opinions: list[Opinion]) -> dict[str, int]:
    """
    返回 {channel: 矛盾次数}
    矛盾定义：同一台博主在 30d 内对同一 ticker 有 long + short 两种方向
    """
```

**联动**：`dashboard.py` 中博主行增加 "矛盾次数" 列。

---

### P4: Obsidian 个股观点追踪增强

**目标**：在现有的 `01-股票概览/{TICKER}.md` 笔记中，追加当前所有博主的精标注观点，而不仅仅是原来的简报摘要。

**文件**：`src/ytbnotes/sync/sync.py` → `_update_stock_overview_note()`  
**联动**：`src/ytbnotes/sync/note_renderer.py` → `render_stock_overview_note()`

---

### P5: Telegram 推送（订阅模式）

**目标**：每日/每周发送高置信度观点摘要 + 胜率更新。

**新建**：`src/ytbnotes/notify/telegram.py`

```python
def send_opinion_digest(
    profiles: list[dict],
    consensus: list[dict],
    bot_token: str,
    chat_id: str,
) -> None:
    """发送 Markdown 格式摘要到 Telegram。"""
```

**配置新增**：`.env` 中 `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`

---

## 八、已知问题与注意事项

| 问题 | 状态 | 说明 |
|------|------|------|
| yfinance FutureWarning | ✅ 已修复 | `row["Close"].iloc[0]` 替代 `float(row["Close"])` |
| Cerebras 偶发 429 限速 | ⚠️ 已有处理 | `backfill_all_opinions` 每次调用间 sleep 0.5s，限速时自动跳过（下次重跑幂等） |
| 新视频重复提取 | ✅ 已防护 | `opinion_store.upsert_opinions` 按 `opinion_id` 去重，重跑安全 |
| 观点矛盾问题 | 📋 P3 待做 | 见第七节，当前架构下所有历史观点均保留，不会删改 |
| SPX 不是股票 | ⚠️ 数据问题 | yfinance 无法正确获取 `SPX`，应改为 `^GSPC`；ticker 别名可在 `config.yaml` 的 `processing.ticker_aliases` 中添加 |
| price_at_publish 填充 | ⚠️ 部分缺失 | `evaluator.verify_opinion` 首次运行时填充，若 yfinance 当日无数据则降级取最近交易日 |

---

## 九、相关文档

| 文档 | 路径 | 内容 |
|------|------|------|
| 系统架构 | `docs/architecture.md` | 各阶段流程、模块说明 |
| 扩展方案设计 | `brain/prediction_tracking_design.md` | 数据模型、胜率评估逻辑、双模型架构 POC 结果 |
| README | `README.md` | 快速上手、环境配置、特性列表 |
