"""
Opinion 仪表盘生成器

将博主胜率排行 + 个股多空共识生成为 Obsidian Markdown 笔记，
写入 Vault 的 00-MOC-索引 目录下。
"""

import datetime
import json
import logging
import os
import re
import yaml
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent

# 默认配置文件路径
DEFAULT_CONFIG_PATH = _PROJECT_DIR / "config.yaml"


def _load_vault_index_dir(config_path: Path | None = None) -> Path | None:
    """从 config.yaml 中读取 index 文件夹（即 00-MOC-索引）的绝对路径。

    config.yaml 结构：
      paths:
        vault: /path/to/obsidian/vault
        folders:
          index: YoutubeNotes/00-MOC-索引
    """
    fp = config_path or DEFAULT_CONFIG_PATH
    try:
        cfg = yaml.safe_load(fp.read_text(encoding="utf-8"))
        paths = cfg.get("paths", {})
        vault_root = paths.get("vault")
        folders = paths.get("folders", {})
        index_rel = folders.get("index")

        if not vault_root or not index_rel:
            return None

        vault = Path(os.path.expanduser(str(vault_root)))
        # index_rel 例如 "YoutubeNotes/00-MOC-索引"
        # vault 本身就是 OB vault 根目录，所以直接拼接即可
        index_abs = vault / index_rel
        return index_abs
    except Exception as e:
        logging.warning(f"读取配置失败: {e}")
        return None


def _to_md_emoji(sentiment: str) -> str:
    return {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(sentiment, "❓")


def _win_rate_badge(rate: float | None, verified: int) -> str:
    if rate is None or verified == 0:
        return "⏳ 积累中"
    pct = int(rate * 100)
    bar = "🟩" * (pct // 20) + "⬜" * (5 - pct // 20)
    color = "🔥" if pct >= 65 else ("✅" if pct >= 50 else "⚠️")
    return f"{color} {pct}% {bar}"


def render_opinion_dashboard(
    profiles: list[dict],
    consensus: list[dict],
    total_opinions: int,
    active_opinions: int,
    generated_at: str,
) -> str:
    """生成 Markdown 仪表盘内容。"""
    lines = []

    lines.append("---")
    lines.append("tags: [MOC, opinion-tracker, 胜率追踪]")
    lines.append(f"updated: {generated_at}")
    lines.append("---")
    lines.append("")
    lines.append("# 📊 01 — 个股观点追踪 (Opinion Tracker)")
    lines.append("")
    lines.append(
        f"> 自动生成 · {generated_at} · 追踪观点 **{total_opinions}** 条 · 待验证 **{active_opinions}** 条"
    )
    lines.append("")
    lines.append("> [!NOTE]")
    lines.append(
        "> 所有观点均以**发布时间点锁定**，博主后续更新/反转将作为新观点单独记录。"
    )
    lines.append("> 胜率 = 在 30 / 90 / 180 天窗口内实现预期的观点比例。")
    lines.append("")

    # ── 博主胜率排行 ──────────────────────────────────────────────────────────
    lines.append("## 🏅 博主信誉排行")
    lines.append("")
    lines.append(
        "| 博主 | 频道 | 观点数 | 已验证 | 30d 胜率 | 90d 胜率 | 180d 胜率 | 盈亏比 | 最大单笔亏损 | 平均盈利 | 平均亏损 | 信誉分 |"
    )
    lines.append(
        "|------|------|--------|--------|----------|----------|-----------|--------|--------------|----------|----------|--------|"
    )

    for p in profiles:
        analyst = p.get("analyst") or p.get("channel", "?")
        channel = p.get("channel", "?")
        total = p.get("total_opinions", 0)
        verified = p.get("verified_opinions", 0)
        wr = p.get("win_rate", {})

        def fmt_wr(key: str) -> str:
            v = wr.get(key)
            if v is None:
                return "⏳"
            pct = int(v * 100)
            ico = "🔥" if pct >= 65 else ("✅" if pct >= 50 else "⚠️")
            return f"{ico} {pct}%"

        def fmt_pct(v: float | None) -> str:
            if v is None:
                return "—"
            return f"{v * 100:.1f}%"

        def fmt_ratio(v: float | None) -> str:
            if v is None:
                return "—"
            return f"{v:.2f}"

        pl_ratio = p.get("profit_loss_ratio")
        max_loss = p.get("max_single_loss")
        avg_win = p.get("avg_win_return")
        avg_loss = p.get("avg_loss_return")

        score = p.get("credibility_score")
        score_str = f"⭐ {score}" if score else "—"
        insuf = " ⚠️" if not p.get("sample_sufficient") else ""

        lines.append(
            f"| {analyst} | {channel} | {total} | {verified}{insuf} | {fmt_wr('30d')} | {fmt_wr('90d')} | {fmt_wr('180d')} | {fmt_ratio(pl_ratio)} | {fmt_pct(max_loss)} | {fmt_pct(avg_win)} | {fmt_pct(avg_loss)} | {score_str} |"
        )

    lines.append("")
    lines.append("> [!TIP]")
    lines.append(
        "> ⚠️ 标注表示验证样本数 < 30，胜率统计尚不具备统计意义。继续积累数据中。"
    )
    lines.append("")

    # ── 个股多空共识 ──────────────────────────────────────────────────────────
    lines.append("## 📈 个股多空共识")
    lines.append("")
    lines.append(
        "| 个股 | 公司 | 看多 | 看空 | 观望 | 共识情绪 | 平均目标价 | 博主数 |"
    )
    lines.append(
        "|------|------|------|------|------|----------|------------|--------|"
    )

    for c in consensus:
        ticker = c.get("ticker", "?")
        company = c.get("company_name", "")
        cons = c.get("consensus", {})
        bullish = cons.get("bullish_count", 0)
        bearish = cons.get("bearish_count", 0)
        neutral = cons.get("neutral_count", 0)
        ws = cons.get("weighted_sentiment", 0)
        target = cons.get("avg_target_price")
        analysts = len(
            {a.get("analyst") for a in c.get("top_analysts", []) if a.get("analyst")}
        )

        target_str = f"${target:.1f}" if target else "—"
        ws_str = f"+{ws:.2f}" if ws >= 0 else f"{ws:.2f}"
        sentiment_bar = "🟢" * bullish + "🔴" * bearish + "⚪" * neutral

        lines.append(
            f"| **{ticker}** | {company} | {bullish} | {bearish} | {neutral} | "
            f"{sentiment_bar} `{ws_str}` | {target_str} | {analysts} |"
        )

    lines.append("")

    # ── 各博主详细观点展开 ────────────────────────────────────────────────────
    lines.append("## 📋 博主活跃观点明细")
    lines.append("")
    lines.append("> 以下为各博主在 30d 窗口内仍待验证的观点，按博主分组展示。")
    lines.append("")

    return "\n".join(lines)


def get_active_opinions_by_channel(opinions: list) -> dict[str, list]:
    """获取各频道的活跃（pending/partial）观点。"""
    from src.ytbnotes.tracker.models import NON_VERIFIABLE_TYPES

    by_channel: dict[str, list] = {}
    for op in opinions:
        if op.prediction.type in NON_VERIFIABLE_TYPES:
            continue
        if op.verification.status in ("verified", "excluded"):
            # 检查是否所有窗口均有最终结果
            snaps = op.verification.snapshots
            if all(s.result in ("win", "loss") for s in snaps.values()):
                continue
        ch = op.channel
        by_channel.setdefault(ch, []).append(op)
    return by_channel


def render_active_opinions_section(opinions: list, profiles: list[dict]) -> list[str]:
    """渲染各博主待验证观点的 Markdown 段落。"""
    from src.ytbnotes.tracker.models import NON_VERIFIABLE_TYPES

    lines = []
    win_rate_by_channel = {p["channel"]: p.get("win_rate", {}) for p in profiles}
    by_channel = get_active_opinions_by_channel(opinions)

    for channel, ops in sorted(by_channel.items()):
        analyst = ops[0].analyst if ops else channel
        wr = win_rate_by_channel.get(channel, {})
        wr_90 = wr.get("90d")
        wr_str = f"{int(wr_90 * 100)}%" if wr_90 is not None else "积累中"

        lines.append(f"### {analyst} ({channel}) — 90d 胜率: {wr_str}")
        lines.append("")
        lines.append(
            "| 个股 | 方向 | 观点类型 | 入场价 | 目标价 | 止损 | 置信 | 时效 | 发布日 |"
        )
        lines.append(
            "|------|------|----------|--------|--------|------|------|------|--------|"
        )

        # 按 ticker 分组、按日期降序
        ops_sorted = sorted(
            ops, key=lambda o: (o.ticker, o.published_date), reverse=True
        )
        seen = set()
        for op in ops_sorted:
            key = (op.ticker, op.prediction.type, op.prediction.price)
            if key in seen:
                continue
            seen.add(key)

            emoji = _to_md_emoji(op.sentiment)
            direction = {"long": "📈 做多", "short": "📉 做空", "hold": "⏸ 观望"}.get(
                op.prediction.direction, op.prediction.direction
            )
            ptype_label = {
                "target_price": "目标价",
                "entry_zone": "入场区",
                "support": "支撑位",
                "resistance": "阻力位",
                "direction_call": "方向判断",
            }.get(op.prediction.type, op.prediction.type)

            price = f"${op.prediction.price:.1f}" if op.prediction.price else "—"
            target = (
                f"${op.prediction.target_price:.1f}"
                if op.prediction.target_price
                else "—"
            )
            stop = f"${op.prediction.stop_loss:.1f}" if op.prediction.stop_loss else "—"
            conf = {"high": "高 🔥", "medium": "中", "low": "低 🌙"}.get(
                op.prediction.confidence, "—"
            )
            horizon = {
                "short_term": "短线",
                "medium_term": "中期",
                "long_term": "长期",
            }.get(op.prediction.horizon, "—")

            lines.append(
                f"| {emoji} **{op.ticker}** | {direction} | {ptype_label} | "
                f"{price} | {target} | {stop} | {conf} | {horizon} | {op.published_date} |"
            )

        # 最近回验快照
        snap_lines = []
        for win_op in ops_sorted[:3]:
            snaps = win_op.verification.snapshots
            parts = []
            for wk in ("30d", "90d", "180d"):
                s = snaps.get(wk)
                if s and s.result:
                    icon = {"win": "✅", "loss": "❌", "pending": "⏳"}.get(
                        s.result, "?"
                    )
                    parts.append(f"{wk}:{icon}")
            if parts and win_op.ticker:
                snap_lines.append(f"{win_op.ticker}: {' · '.join(parts)}")

        if snap_lines:
            lines.append("")
            lines.append(f"*回验进度 — {' | '.join(snap_lines)}*")

        lines.append("")

    return lines


def write_dashboard_to_vault(
    opinions: list,
    profiles: list[dict],
    consensus: list[dict],
    config_path: Path | None = None,
    output_path_override: Path | None = None,
) -> Path | None:
    """
    将仪表盘 Markdown 写入 Obsidian Vault。
    返回写入路径，失败返回 None。
    """
    from src.ytbnotes.tracker.models import NON_VERIFIABLE_TYPES

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    total_opinions = sum(
        1 for op in opinions if op.prediction.type not in NON_VERIFIABLE_TYPES
    )
    active_opinions = sum(
        1
        for op in opinions
        if op.prediction.type not in NON_VERIFIABLE_TYPES
        and op.verification.status in ("pending", "partial")
    )

    # 主体内容
    content = render_opinion_dashboard(
        profiles, consensus, total_opinions, active_opinions, generated_at
    )

    # 活跃观点明细
    active_section = render_active_opinions_section(opinions, profiles)
    content += "\n".join(active_section)

    # 确定输出路径
    if output_path_override:
        out_path = output_path_override
    else:
        index_dir = _load_vault_index_dir(config_path)
        if not index_dir:
            logging.error(
                "无法从 config.yaml 读取 Vault 索引目录，请检查 vault_root 和 folders.index 配置"
            )
            return None
        out_path = index_dir / "01-Opinion-个股观点追踪.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    logging.info(f"仪表盘已写入: {out_path}")
    return out_path
