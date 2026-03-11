# YoutubeNotes — 开发交接文档

> 本文档用于 AI 接手开发时快速了解项目现状，避免从零研读所有代码。  
> 版本：2026-03-11 | 对应 commit: `b2af8c0`

---

## 一、项目概况

YoutubeNotes 是一个自动化工具链：

```
订阅 YouTube 财经频道
→ 自动下载最新视频
→ 字幕优先 / 本地 ASR 转录
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
│   ├── cerebras_poc.py      # Cerebras API POC 测试脚本（单文件验证）
│   ├── backfill_json.py     # 回填历史 JSON 到新格式
│   ├── extract_cookies.py   # 导出 Chrome Cookie 供 yt-dlp 使用
│   ├── migrate_notes.py     # 迁移旧版 Obsidian 笔记格式
│   └── youtube_rss.py       # 从视频 URL 反查频道 RSS
├── src/ytbnotes/
│   ├── downloader/          # Phase 1: RSS 下载 + YouTube Data API 备用
│   ├── transcribe/          # Phase 2a: 字幕下载 / FunASR 本地转录
│   ├── analyzer/            # Phase 2b: Qwen LLM 简报 + mentioned_tickers 提取
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
| Phase 1: 下载调度 | ✅ 完成 | `downloader/downloader.py` |
| Phase 2a: 转录 | ✅ 完成 | `transcribe/` |
| Phase 2b: LLM 分析 | ✅ 完成 | `analyzer/` |
| Phase 3: Obsidian 同步 | ✅ 完成 | `sync/sync.py` |
| Phase 4: 观点提取 (Cerebras) | ✅ 完成 | `tracker/` |
| Phase 5: 行情回验 + 胜率评分 | ✅ 完成 | `verifier/` |
| Obsidian 仪表盘 | ✅ 完成 | `verifier/dashboard.py` |

**已验证的运行效果：**
- 19 个分析结果 → 136 条 opinions 提取（Cerebras $0.04）
- yfinance 拉取并缓存市场数据
- Obsidian 仪表盘写入 `YoutubeNotes/00-MOC-索引/01-Opinion-个股观点追踪.md`

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
| `CEREBRAS_API_KEY` | GPT-OSS-120B 精标注 | ✅ (tracker) |
| `YOUTUBE_DATA_API_KEY` | RSS 备用回退 | 推荐 |
| `SUBTITLE_MIN_ZH_RATIO` | 中文字幕质量阈值 | 可选 |
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
