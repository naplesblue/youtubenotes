"""
lib/core/note_renderer.py

YAML v1.0 标准化与 Markdown 渲染引擎。
对应 JS 版 NoteRenderer.js。
"""

from datetime import date, datetime, timezone
from typing import Any

import yaml


class NoteRenderer:
    def __init__(self, config: dict | None = None):
        self.config = config or {}

    # ── Front Matter ──────────────────────────────────────────────────────────

    def build_front_matter(self, data: dict) -> str:
        """使用 PyYAML 序列化，确保特殊字符正确转义。"""
        fm = dict(data)
        fm.setdefault("tags", [])
        content = yaml.dump(
            fm,
            allow_unicode=True,
            indent=2,
            default_flow_style=False,
            sort_keys=False,
        )
        return f"---\n{content}---\n"

    def _get_folder_name(self, key: str, fallback: str) -> str:
        cfg = self.config.get("config", {}) if isinstance(self.config, dict) else {}
        return (
            cfg.get("paths", {})
            .get("folders", {})
            .get(key, fallback)
        )

    # ── 视频笔记 ──────────────────────────────────────────────────────────────

    def render_video_note(
        self,
        *,
        video_id: str,
        metadata: dict,
        summary: str,
        key_points: list[str],
        mentioned_tickers: list[dict],
        people_mentioned: list[str],
        transcript_note_link: str | None = None,
        note_title: str | None = None,
    ) -> str:
        today      = date.today().isoformat()
        iso_now    = datetime.now(timezone.utc).isoformat()
        meta       = metadata or {}
        tickers    = mentioned_tickers or []
        people     = people_mentioned or []
        summary_text = summary or ""
        display_title = note_title or meta.get("title", video_id)
        video_folder = self._get_folder_name("videos", "02-视频笔记")

        fm_data = {
            "id":       video_id,
            "type":     "video-note",
            "created":  today,
            "modified": today,
            "aliases":  [video_id],
            "tags":     ["finance", "youtube-notes", "video-analysis"],
            "title":    meta.get("title", video_id),
            "source": {
                "platform":  "youtube",
                "video_id":  meta.get("video_id", video_id),
                "channel":   meta.get("channel", "Unknown"),
                "url":       meta.get("youtube_url", ""),
                "published": meta.get("date", today),
            },
            # summary 不放进 frontmatter（YAML 中大段文本可读性差），仅在 body 渲染
            "sentiment": self._infer_sentiment(tickers),
            "mentioned_tickers": [
                {
                    "ticker":    t.get("ticker"),
                    "company":   t.get("company_name"),
                    "sentiment": t.get("sentiment", "neutral"),
                    "analyst":   t.get("analyst", "unknown"),
                }
                for t in tickers
            ],
            "people_mentioned": people,
            "status":        meta.get("status", "processed"),
            "quality_score": meta.get("quality_score", 0.85),
        }

        lines = [self.build_front_matter(fm_data)]
        lines.append(f"# {display_title}\n")

        # 概览
        lines.append("## 📊 概览\n")
        lines.append(f"**分析师/来源**: {meta.get('channel', 'N/A')}")
        lines.append(f"**日期**: {meta.get('date', today)}")
        lines.append(f"**视频链接**: [YouTube]({meta.get('youtube_url', '#')})")
        lines.append(f"**处理时间**: {iso_now}\n")

        # 摘要
        if summary_text:
            lines.append("## 📝 核心观点\n")
            lines.append(f"{summary_text}\n")

        # 关键要点
        if key_points:
            lines.append("## 🔑 关键要点\n")
            for idx, point in enumerate(key_points, 1):
                lines.append(f"{idx}. {point}")
            lines.append("")

        # 提及标的
        if tickers:
            lines.append("## 📈 提及标的\n")
            lines.append("| 代码 | 公司名称 | 关键价格水平 | 观点 |")
            lines.append("|------|----------|--------------|------|")
            for t in tickers:
                levels_str = ", ".join(
                    str(lv.get("level")) for lv in (t.get("price_levels") or [])
                ) or "N/A"
                safe_levels  = levels_str.replace("|", "\\|").replace("\n", " ")
                safe_company = str(t.get("company_name", "N/A")).replace("|", "\\|").replace("\n", " ")
                lines.append(f"| [[{t['ticker']}]] | {safe_company} | {safe_levels} | {t.get('sentiment', 'neutral')} |")
            lines.append("")

        # 相关人物
        if people:
            lines.append("## 👤 相关人物\n")
            for person in people:
                safe = str(person).replace("[[", "").replace("]]", "")
                lines.append(f"- [[{safe}]]")
            lines.append("")

        # 原始数据链接
        lines.append("## 📎 原始数据\n")
        if transcript_note_link:
            lines.append(f"- 完整时间线转录: {transcript_note_link}")
        lines.append(f"- [[{video_id}_price_levels|价格水平详情]]")
        lines.append(f"- 分析文件: `{video_id}.json`\n")

        # Dataview 联动
        if tickers:
            lines.append("## 🔗 相关个股历史分析记录\n")
            lines.append("> 以下 Dataview 查询会自动展示该视频中提及股票的历史分析记录\n")
            for t in tickers:
                ticker = t["ticker"]
                lines.append(f"### [[{ticker}]] 历史分析\n")
                lines.append("```dataview")
                lines.append("TABLE WITHOUT ID")
                lines.append('  file.link as "视频",')
                lines.append('  source.channel as "频道",')
                lines.append('  source.published as "日期"')
                lines.append(f'FROM "{video_folder}"')
                lines.append(f'WHERE contains(mentioned_tickers.ticker, "{ticker}")')
                lines.append("SORT source.published DESC")
                lines.append("```\n")

        return "\n".join(lines)

    def render_transcript_note(
        self,
        *,
        video_id: str,
        metadata: dict,
        transcript_text: str,
        brief_note_link: str | None = None,
        note_title: str | None = None,
    ) -> str:
        today   = date.today().isoformat()
        iso_now = datetime.now(timezone.utc).isoformat()
        meta    = metadata or {}
        body_text = transcript_text.strip() if transcript_text else ""
        display_title = note_title or f"{video_id} 完整转录"

        fm_data = {
            "id":      f"{video_id}-transcript",
            "type":    "video-transcript",
            "created": today,
            "modified": today,
            "aliases": [f"{video_id}-transcript"],
            "tags":    ["finance", "youtube-notes", "transcript"],
            "title":   display_title,
            "source": {
                "platform":  "youtube",
                "video_id":  meta.get("video_id", video_id),
                "channel":   meta.get("channel", "Unknown"),
                "url":       meta.get("youtube_url", ""),
                "published": meta.get("date", today),
            },
            "status": meta.get("status", "processed"),
        }

        lines = [self.build_front_matter(fm_data)]
        lines.append(f"# {display_title}\n")
        lines.append("## 📊 概览\n")
        lines.append(f"**分析师/来源**: {meta.get('channel', 'N/A')}")
        lines.append(f"**日期**: {meta.get('date', today)}")
        lines.append(f"**视频链接**: [YouTube]({meta.get('youtube_url', '#')})")
        lines.append(f"**处理时间**: {iso_now}")
        if brief_note_link:
            lines.append(f"**关联简报**: {brief_note_link}")
        lines.append("")
        lines.append("## 🧾 完整时间线转录\n")
        if body_text:
            lines.append(body_text)
        else:
            lines.append("*当前结构化数据中未包含完整转录文本。*")
        lines.append("")

        return "\n".join(lines)

    # ── 价格水平笔记 ──────────────────────────────────────────────────────────

    def render_price_level_note(
        self,
        *,
        ticker: str,
        company_name: str,
        levels: list[dict],
        source_videos: list[str],
    ) -> str:
        today  = date.today().isoformat()
        levels = levels or []
        source_videos = list(dict.fromkeys(source_videos or []))  # 去重保序

        by_type = self._group_by_type(levels)
        latest_support    = self._find_latest(levels, "support")
        latest_resistance = self._find_latest(levels, "resistance")
        latest_target     = self._find_latest(levels, "target")

        fm_data = {
            "id":       f"price-level-{ticker}",
            "type":     "price-level",
            "created":  today,
            "modified": today,
            "tags":     ["price-levels", ticker],
            "ticker":   ticker,
            "company":  company_name or "Unknown",
            "level_count": len(levels),
            "sources": [
                {
                    "video_id":    lv.get("source_video"),
                    "analyst":     lv.get("analyst"),
                    "last_update": lv.get("date_added"),
                }
                for lv in levels
            ],
            "latest_support":    latest_support.get("level") if latest_support else None,
            "latest_resistance": latest_resistance.get("level") if latest_resistance else None,
            "latest_target":     latest_target.get("level") if latest_target else None,
            "source_videos":     source_videos,
            "related_persons":   list({lv.get("analyst") for lv in levels if lv.get("analyst")}),
        }

        type_labels = {
            "support":     "📗 支撑位",
            "resistance":  "📕 阻力位",
            "target":      "🎯 目标价",
            "stop":        "🛑 止损位",
            "entry":       "✅ 入场位",
            "observation": "👁️ 观察位",
        }
        type_descs = {
            "support":     "价格下跌时可能遇到买盘支撑的水平",
            "resistance":  "价格上涨时可能遇到卖盘压力的水平",
            "target":      "分析师设定的目标价格",
            "stop":        "建议的止损价格",
            "entry":       "建议的入场价格",
            "observation": "需要持续观察的价格水平",
        }

        lines = [self.build_front_matter(fm_data)]
        lines.append(f"# {ticker} 价格水平追踪\n")
        lines.append(f"> 公司: {company_name or 'Unknown'}")
        lines.append(f"> 最后更新: {today}")
        lines.append(f"> 共追踪 {len(levels)} 条价格记录\n")

        # ── 价位区间可视化 ──
        supports = sorted(
            [lv for lv in levels if lv.get("type") == "support" and self._to_float(lv.get("level")) is not None],
            key=lambda l: self._to_float(l["level"]),
        )
        resistances = sorted(
            [lv for lv in levels if lv.get("type") == "resistance" and self._to_float(lv.get("level")) is not None],
            key=lambda l: self._to_float(l["level"]),
            reverse=True,
        )
        targets = sorted(
            [lv for lv in levels if lv.get("type") == "target" and self._to_float(lv.get("level")) is not None],
            key=lambda l: self._to_float(l["level"]),
            reverse=True,
        )
        if supports or resistances or targets:
            lines.append("> [!abstract] 价位区间图\n>")
            for t in targets[:3]:
                ctx = str(t.get('context', '')).replace('\n', ' ')[:40]
                lines.append(f"> 🎯 **目标价: {t['level']}** {ctx}")
            for r in resistances[:3]:
                ctx = str(r.get('context', '')).replace('\n', ' ')[:40]
                lines.append(f"> 🔴 **阻力位: {r['level']}** {ctx}")
            lines.append("> ")
            lines.append("> ─────────── 当前交易区间 ───────────")
            lines.append("> ")
            for s in reversed(supports[-3:]):
                ctx = str(s.get('context', '')).replace('\n', ' ')[:40]
                lines.append(f"> 🟢 **支撑位: {s['level']}** {ctx}")
            lines.append("")

        for type_key, label in type_labels.items():
            group = by_type.get(type_key, [])
            if not group:
                continue
            lines.append(f"## {label} ({len(group)})\n")
            lines.append("| 价格 | 日期 | 来源视频 | 分析师 | 语境 |")
            lines.append("|------|------|----------|--------|------|")
            for lv in group:
                ctx = str(lv.get("context", "")).replace("|", "\\|")[:30]
                lines.append(
                    f"| {lv.get('level')} | {lv.get('date_added')} "
                    f"| {lv.get('source_video')} | {lv.get('analyst')} | {ctx} |"
                )
            lines.append("")

        # 时间轴
        lines.append("## 📅 时间轴视图\n")
        chrono = sorted(levels, key=lambda l: l.get("date_added") or "", reverse=True)
        for lv in chrono[:20]:
            lines.append(
                f"- **{lv.get('date_added')}** | {lv.get('level')} {lv.get('type')} "
                f"| {lv.get('source_video')} | {lv.get('analyst')}"
            )
        if len(chrono) > 20:
            lines.append(f"- ... 还有 {len(chrono) - 20} 条记录")
        lines.append("")

        # 说明
        lines.append("## 📝 说明\n")
        for key, label in type_labels.items():
            lines.append(f"- **{label}**: {type_descs[key]}")
        lines.append("")

        # 相关笔记
        lines.append("## 🔗 相关笔记\n")
        lines.append(f"- [[{ticker}]] - 公司概览")
        for vid in source_videos[:10]:
            lines.append(f"- {vid} - 来源视频")
        lines.append("")

        # Dataview
        lines.append("## 📊 相关视频分析列表\n")
        lines.append(f"> 自动汇总所有提及 [[{ticker}]] 的视频笔记\n")
        lines.append("```dataview")
        lines.append("TABLE WITHOUT ID")
        lines.append('  file.link as "视频",')
        lines.append('  source.channel as "频道",')
        lines.append('  source.published as "日期"')
        lines.append(f'FROM "{self._get_folder_name("videos", "02-视频笔记")}"')
        lines.append(f'WHERE contains(mentioned_tickers.ticker, "{ticker}")')
        lines.append("SORT source.published DESC")
        lines.append("```\n")

        lines.append("## 📈 分析师情绪汇总\n")
        lines.append(f"> 汇总不同分析师对 [[{ticker}]] 的情绪观点\n")
        lines.append("```dataview")
        lines.append("TABLE WITHOUT ID")
        lines.append('  T.analyst as "分析师",')
        lines.append('  T.sentiment as "情绪",')
        lines.append('  source.published as "分析日期"')
        lines.append(f'FROM "{self._get_folder_name("videos", "02-视频笔记")}"')
        lines.append("FLATTEN mentioned_tickers as T")
        lines.append(f'WHERE T.ticker = "{ticker}"')
        lines.append("SORT source.published DESC")
        lines.append("```")

        return "\n".join(lines)

    # ── 人物笔记 ──────────────────────────────────────────────────────────────

    def render_person_note(
        self,
        *,
        person: str,
        tickers_mentioned: list[str],
        videos_appeared: list[str],
    ) -> str:
        today   = date.today().isoformat()
        tickers = tickers_mentioned or []
        videos  = videos_appeared or []
        slug    = person.lower().replace(" ", "-")
        video_folder = self._get_folder_name("videos", "02-视频笔记")
        stock_folder = self._get_folder_name("stock_overview", "01-股票概览")

        fm_data = {
            "id":               f"person-{slug}",
            "type":             "person",
            "created":          today,
            "modified":         today,
            "tags":             ["person", "analyst"],
            "name":             person,
            "role":             "analyst",
            "tickers_mentioned": tickers,
            "videos_appeared":   videos,
        }

        return self.build_front_matter(fm_data) + f"""
# {person}

## 📝 简介

<!-- 待补充 -->

## 📊 该分析师的所有视频 (Dataview)

> 动态展示 [[{person}]] 参与分析的所有视频

```dataview
LIST
FROM "{video_folder}"
WHERE contains(people_mentioned, "{person}")
SORT source.published DESC
```

## 📈 该分析师关注的标的

> 汇总 [[{person}]] 分析过的所有股票及情绪偏向

```dataviewjs
const person = "{person}";
const stockFolder = "{stock_folder}";
const pages = dv.pages('"{video_folder}"')
  .where(p => p.people_mentioned && p.people_mentioned.includes(person));

const agg = {{}};

for (let p of pages) {{
    if (!p.mentioned_tickers) continue;
    for (let t of p.mentioned_tickers) {{
        if (t.analyst !== person) continue;
        if (!agg[t.ticker]) {{
            agg[t.ticker] = {{
                company: t.company || "",
                bullish: 0,
                bearish: 0,
                neutral: 0
            }};
        }}
        let s = (t.sentiment || "neutral").toLowerCase();
        if (s.includes("bull")) agg[t.ticker].bullish++;
        else if (s.includes("bear")) agg[t.ticker].bearish++;
        else agg[t.ticker].neutral++;
    }}
}}

const rows = [];
for (let ticker of Object.keys(agg).sort()) {{
    let d = agg[ticker];
    let bar = "🟢".repeat(d.bullish) + "🔴".repeat(d.bearish) + "⚪".repeat(d.neutral);
    if (!bar) bar = "—";
    rows.push([
        `[[${{stockFolder}}/${{ticker}}|${{ticker}}]]`,
        d.company,
        d.bullish,
        d.bearish,
        d.neutral,
        bar
    ]);
}}

dv.paragraph(dv.markdownTable(["个股", "公司", "看多", "看空", "观望", "情绪分布"], rows));
```
"""

    # ── MOC ───────────────────────────────────────────────────────────────────

    def render_moc(
        self,
        *,
        stats: dict,
        timestamp: str,
        ticker_stats: list[dict] | None = None,
        recent_videos: list[dict] | None = None,
        channel_names: list[str] | None = None,
    ) -> str:
        stock_folder = self._get_folder_name("stock_overview", "01-股票概览")
        video_folder = self._get_folder_name("videos", "02-视频笔记")
        transcript_folder = self._get_folder_name("transcripts", "05-完整转录")
        price_folder = self._get_folder_name("price_levels", "03-价格水平")
        people_folder = self._get_folder_name("people", "04-人物")

        ticker_stats = ticker_stats or []
        recent_videos = recent_videos or []
        channel_names = channel_names or []

        total_notes = stats.get("created", 0) + stats.get("updated", 0)

        fm_data = {
            "id":       "moc-main-index",
            "type":     "moc",
            "created":  timestamp,
            "modified": timestamp,
            "tags":     ["MOC", "index", "dashboard"],
            "stats": {
                "processed": stats.get("processed", 0),
                "created":   stats.get("created", 0),
                "updated":   stats.get("updated", 0),
            },
        }

        lines = [self.build_front_matter(fm_data)]
        lines.append(f"# 📊 AI 投资笔记仪表盘\n")
        lines.append(
            f"> 🤖 上次同步: {timestamp}"
            f" | 跟踪频道: {len(channel_names)}"
            f" | 收录视频: {stats.get('processed', 0)}"
            f" | 笔记总数: {total_notes}\n"
        )

        # ── 热门标的 ──
        if ticker_stats:
            lines.append("## 🔥 热门标的（按提及频次）\n")
            lines.append("| 排名 | 标的 | 提及 | 情绪 | 频道 |")
            lines.append("|:---:|:---|:---:|:---:|:---|")
            sentiment_emoji = {
                "bullish": "🟢 偏多", "very_bullish": "🟢 强多",
                "bearish": "🔴 偏空", "very_bearish": "🔴 强空",
                "neutral": "🟡 中性",
            }
            for i, ts in enumerate(ticker_stats[:15], 1):
                emoji = sentiment_emoji.get(ts.get("sentiment", "neutral"), "🟡 中性")
                channels = ", ".join(ts.get("channels", [])) or "—"
                lines.append(
                    f"| {i} | [[{ts['ticker']}]] | {ts['count']}次 | {emoji} | {channels} |"
                )
            lines.append("")

        # ── 最新视频简报 ──
        if recent_videos:
            lines.append("## 📅 最新视频简报\n")
            for v in recent_videos[:10]:
                vid = v.get("video_id", "")
                link_target = v.get("note_link", vid)  # 完整 Vault 相对路径
                ch = v.get("channel", "")
                title = v.get("title", vid)
                pub = v.get("date", "")
                tickers = v.get("tickers", [])
                ticker_tags = " ".join(f"`{t}`" for t in tickers[:5])
                lines.append(
                    f"- **{pub}** 🎬 {ch} — [[{link_target}|{title}]]"
                    f"{' — ' + ticker_tags if ticker_tags else ''}"
                )
            lines.append("")

        # ── 情绪分布饼图 ──
        if ticker_stats:
            sent_counts = {"看多": 0, "中性": 0, "看空": 0}
            for ts in ticker_stats:
                s = ts.get("sentiment", "neutral")
                if s in ("bullish", "very_bullish"):
                    sent_counts["看多"] += ts.get("count", 1)
                elif s in ("bearish", "very_bearish"):
                    sent_counts["看空"] += ts.get("count", 1)
                else:
                    sent_counts["中性"] += ts.get("count", 1)
            if any(v > 0 for v in sent_counts.values()):
                lines.append("## 🧭 市场情绪分布\n")
                lines.append("```mermaid")
                lines.append("pie title 标的情绪分布")
                for label, count in sent_counts.items():
                    if count > 0:
                        lines.append(f'    "{label}" : {count}')
                lines.append("```\n")

        # ── 频道列表 ──
        if channel_names:
            lines.append("## 📡 跟踪频道\n")
            for ch in sorted(channel_names):
                lines.append(f"- {ch}")
            lines.append("")

        # ── 目录导航 ──
        lines.append("## 📂 按类型浏览\n")
        lines.append(f"- [[{stock_folder}|📈 股票概览]] — 所有跟踪标的的聚合观点")
        lines.append(f"- [[{video_folder}|🎬 视频简报]] — 单个视频的分析摘要")
        lines.append(f"- [[{transcript_folder}|🧾 完整转录]] — 带时间戳的全文")
        lines.append(f"- [[{price_folder}|💰 价格水平]] — 支撑/阻力/目标价追踪")
        lines.append(f"- [[{people_folder}|👤 分析师]] — 关键人物聚合\n")

        lines.append("---\n")
        lines.append("*本文件由 YoutubeNotes AI 自动生成*\n")

        return "\n".join(lines)

    def render_stock_overview_note(
        self,
        *,
        ticker: str,
        company_name: str,
        entries: list[dict],
    ) -> str:
        today = date.today().isoformat()
        entries = entries or []
        video_folder = self._get_folder_name("videos", "02-视频笔记")
        price_folder = self._get_folder_name("price_levels", "03-价格水平")

        channels = sorted({str(e.get("channel", "")).strip() for e in entries if str(e.get("channel", "")).strip()})
        analysts = sorted({str(e.get("analyst", "")).strip() for e in entries if str(e.get("analyst", "")).strip()})

        sentiment_counts = {
            "very_bullish": 0,
            "bullish": 0,
            "neutral": 0,
            "bearish": 0,
            "very_bearish": 0,
        }
        sentiment_scores = []
        for e in entries:
            s = str(e.get("sentiment", "neutral") or "neutral").strip().lower()
            if s not in sentiment_counts:
                s = "neutral"
            sentiment_counts[s] += 1
            sentiment_scores.append(self._sentiment_to_score(s))
        avg_sentiment = (
            round(sum(sentiment_scores) / len(sentiment_scores), 3)
            if sentiment_scores else 0.5
        )

        latest = entries[0] if entries else {}
        latest_levels = latest.get("price_levels") or []

        fm_data = {
            "id": f"stock-overview-{ticker}",
            "type": "stock-overview",
            "created": today,
            "modified": today,
            "tags": ["stock-overview", ticker, "finance", "youtube-notes"],
            "ticker": ticker,
            "company": company_name or ticker,
            "mention_count": len(entries),
            "channels": channels,
            "analysts": analysts,
            "avg_sentiment": avg_sentiment,
            "latest_date": latest.get("date"),
            "latest_video_id": latest.get("video_id"),
        }

        lines = [self.build_front_matter(fm_data)]
        lines.append(f"# {ticker} 股票概览\n")
        lines.append(f"> 公司: {company_name or ticker}")
        lines.append(f"> 最后更新: {today}")
        lines.append(f"> 累计提及: {len(entries)} 次\n")

        lines.append("## 📌 最新观点\n")
        if latest:
            lines.append(f"- 日期: {latest.get('date', 'N/A')}")
            lines.append(f"- 频道: {latest.get('channel', 'N/A')}")
            lines.append(f"- 分析师: {latest.get('analyst', 'unknown')}")
            lines.append(f"- 情绪: {latest.get('sentiment', 'neutral')}")
            if latest.get("focus_excerpt"):
                lines.append(f"- 观点摘要: {latest.get('focus_excerpt')}")
            if latest.get("video_id"):
                lines.append(f"- 关联视频: [[{latest.get('video_id')}|视频简报]]")
            if latest.get("source_url"):
                lines.append(f"- YouTube: [原视频链接]({latest.get('source_url')})")
        else:
            lines.append("- 暂无观点数据")
        lines.append("")

        lines.append("## 🧭 情绪统计\n")
        lines.append("| 情绪 | 次数 |")
        lines.append("|------|------|")
        sentiment_labels = {
            "very_bullish": "🟢 极度看多",
            "bullish": "🟢 看多",
            "neutral": "🟡 中性",
            "bearish": "🔴 看空",
            "very_bearish": "🔴 极度看空",
        }
        for key in ("very_bullish", "bullish", "neutral", "bearish", "very_bearish"):
            lines.append(f"| {sentiment_labels[key]} | {sentiment_counts[key]} |")
        lines.append("")
        lines.append(f"- 平均情绪分值: **{avg_sentiment}**（0=极空，1=极多）")
        lines.append(f"- 覆盖频道数: **{len(channels)}**")
        lines.append(f"- 覆盖分析师数: **{len(analysts)}**")
        lines.append("")

        # ── Mermaid 情绪趋势图（需 ≥2 个数据点）──
        if len(entries) >= 2:
            # 按时间正序排列用于图表
            chrono_entries = list(reversed(entries))
            dates = []
            scores = []
            for e in chrono_entries:
                d = str(e.get("date", "")).strip()
                if d:
                    # 只取 MM-DD 部分让 x 轴简洁
                    short_date = d[5:] if len(d) >= 10 else d
                    dates.append(short_date)
                    scores.append(self._sentiment_to_score(
                        str(e.get("sentiment", "neutral"))
                    ))
            if len(dates) >= 2:
                lines.append("## 📈 情绪走势\n")
                lines.append("```mermaid")
                lines.append(f"xychart-beta")
                lines.append(f'    title "{ticker} 情绪变化"')
                x_labels = ", ".join(f'"{d}"' for d in dates)
                lines.append(f"    x-axis [{x_labels}]")
                lines.append('    y-axis "情绪分" 0 --> 1')
                score_str = ", ".join(str(s) for s in scores)
                lines.append(f"    line [{score_str}]")
                lines.append("```\n")

        lines.append("## 🎯 最新关键价位\n")
        if latest_levels:
            lines.append("| 类型 | 价位 | 语境 |")
            lines.append("|------|------|------|")
            for lv in latest_levels[:20]:
                lv_type = str(lv.get("type", "observation"))
                lv_val = str(lv.get("level", "N/A"))
                ctx = str(lv.get("context", "")).replace("|", "\\|")
                lines.append(f"| {lv_type} | {lv_val} | {ctx} |")
        else:
            lines.append("- 暂无结构化价位数据")
        lines.append("")

        lines.append("## 🕒 观点时间线（最新在前）\n")
        if not entries:
            lines.append("- 暂无记录")
            lines.append("")
        else:
            for e in entries:
                date_str = e.get("date", "N/A")
                channel = e.get("channel", "N/A")
                analyst = e.get("analyst", "unknown")
                sentiment = e.get("sentiment", "neutral")
                lines.append(f"### {date_str} | {channel} | {sentiment}\n")
                lines.append(f"- 分析师: {analyst}")
                if e.get("focus_excerpt"):
                    lines.append(f"- 摘要: {e.get('focus_excerpt')}")
                if e.get("video_id"):
                    lines.append(f"- 关联视频: [[{e.get('video_id')}|视频简报]]")
                if e.get("source_url"):
                    lines.append(f"- YouTube: [原视频链接]({e.get('source_url')})")
                key_points = e.get("key_points") or []
                if key_points:
                    lines.append("- 关键点:")
                    for kp in key_points[:3]:
                        lines.append(f"  - {kp}")
                levels = e.get("price_levels") or []
                if levels:
                    level_summary = []
                    for lv in levels[:6]:
                        lv_type = str(lv.get("type", "observation"))
                        lv_val = str(lv.get("level", "N/A"))
                        level_summary.append(f"{lv_type}:{lv_val}")
                    lines.append(f"- 价位: {', '.join(level_summary)}")
                lines.append("")

        lines.append("## 🔗 相关笔记\n")
        lines.append(f"- [[{price_folder}/{ticker}_levels|{ticker} 价格水平追踪]]")
        lines.append(f"- [[{video_folder}]]")
        lines.append("")

        lines.append("## 📊 Dataview：相关视频\n")
        lines.append("```dataview")
        lines.append("TABLE WITHOUT ID")
        lines.append('  file.link as "视频",')
        lines.append('  source.channel as "频道",')
        lines.append('  source.published as "日期"')
        lines.append(f'FROM "{video_folder}"')
        lines.append(f'WHERE contains(mentioned_tickers.ticker, "{ticker}")')
        lines.append("SORT source.published DESC")
        lines.append("```")
        lines.append("")

        return "\n".join(lines)

    # ── 辅助 ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _to_float(value) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _sentiment_to_score(sentiment: str) -> float:
        mapping = {
            "very_bullish": 0.9,
            "bullish": 0.75,
            "neutral": 0.5,
            "bearish": 0.25,
            "very_bearish": 0.1,
        }
        return mapping.get(str(sentiment or "neutral").strip().lower(), 0.5)

    def _infer_sentiment(self, tickers: list[dict]) -> str:
        if not tickers:
            return "neutral"
        sentiments = [t.get("sentiment", "neutral") for t in tickers]
        bullish = sum(1 for s in sentiments if s in ("bullish", "very_bullish"))
        bearish = sum(1 for s in sentiments if s in ("bearish", "very_bearish"))
        if bullish > bearish:
            return "bullish"
        if bearish > bullish:
            return "bearish"
        return "neutral"

    def _group_by_type(self, levels: list[dict]) -> dict[str, list[dict]]:
        types = ["support", "resistance", "target", "stop", "entry", "observation"]
        return {t: [lv for lv in levels if lv.get("type") == t] for t in types}

    def _find_latest(self, levels: list[dict], level_type: str) -> dict | None:
        group = [lv for lv in levels if lv.get("type") == level_type]
        if not group:
            return None
        return max(group, key=lambda l: l.get("date_added") or "")
