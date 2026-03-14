"""
博主胜率 / 个股共识 聚合计算器
"""

import datetime
import logging
from collections import defaultdict
from typing import Optional

from ..tracker.models import Opinion, NON_VERIFIABLE_TYPES


def compute_blogger_profiles(opinions: list[Opinion]) -> list[dict]:
    """
    按博主聚合计算胜率。
    返回排行列表（按 90d 胜率降序）。
    """
    by_channel: dict[str, list[Opinion]] = defaultdict(list)
    for op in opinions:
        if op.prediction.type in NON_VERIFIABLE_TYPES:
            continue
        by_channel[op.channel].append(op)

    profiles = []
    for channel, ops in by_channel.items():
        analyst = ops[0].analyst if ops else ""
        total = len(ops)

        win_rates = {}
        win_rate_by_regime = {}
        avg_returns = {}
        verified_count = 0

        window_metrics = {}
        for window in ("30d", "90d", "180d"):
            wins = 0
            losses = 0
            regime_wins = {"bull": 0, "bear": 0, "neutral": 0}
            regime_losses = {"bull": 0, "bear": 0, "neutral": 0}
            returns = []
            win_returns = []
            loss_returns = []
            for op in ops:
                snap = op.verification.snapshots.get(window)
                if not snap or snap.result not in ("win", "loss"):
                    continue
                if snap.result == "win":
                    wins += 1
                else:
                    losses += 1

                regime = snap.regime if snap.regime in ("bull", "bear", "neutral") else "neutral"
                if snap.result == "win":
                    regime_wins[regime] += 1
                else:
                    regime_losses[regime] += 1

                if snap.return_pct is not None:
                    returns.append(snap.return_pct)
                    if snap.result == "win":
                        win_returns.append(snap.return_pct)
                    else:
                        loss_returns.append(snap.return_pct)

            decided = wins + losses
            if decided > 0:
                win_rates[window] = round(wins / decided, 3)
                verified_count = max(verified_count, decided)
            else:
                win_rates[window] = None

            win_rate_by_regime[window] = {}
            for regime in ("bull", "bear", "neutral"):
                regime_decided = regime_wins[regime] + regime_losses[regime]
                win_rate_by_regime[window][regime] = (
                    round(regime_wins[regime] / regime_decided, 3)
                    if regime_decided > 0
                    else None
                )

            avg_returns[window] = (
                round(sum(returns) / len(returns), 4) if returns else None
            )
            avg_win = (
                round(sum(win_returns) / len(win_returns), 4) if win_returns else None
            )
            avg_loss = (
                round(sum(loss_returns) / len(loss_returns), 4)
                if loss_returns
                else None
            )
            max_loss = min(loss_returns) if loss_returns else None
            pl_ratio = (
                round(avg_win / abs(avg_loss), 2)
                if avg_win and avg_loss and avg_loss != 0
                else None
            )

            window_metrics[window] = {
                "avg_win_return": avg_win,
                "avg_loss_return": avg_loss,
                "max_single_loss": max_loss,
                "profit_loss_ratio": pl_ratio,
            }

        # 信誉分：加权胜率 (30d * 0.2 + 90d * 0.3 + 180d * 0.5)，范围 0-10
        score_parts = []
        weights = {"30d": 0.2, "90d": 0.3, "180d": 0.5}
        total_weight = 0
        for w, wt in weights.items():
            if win_rates.get(w) is not None:
                score_parts.append(win_rates[w] * wt)
                total_weight += wt
        credibility = (
            round(sum(score_parts) / total_weight * 10, 1) if total_weight > 0 else None
        )

        # 找擅长 / 差的 tickers
        ticker_wins: dict[str, int] = defaultdict(int)
        ticker_losses: dict[str, int] = defaultdict(int)
        for op in ops:
            snap_90 = op.verification.snapshots.get("90d")
            if snap_90 and snap_90.result == "win":
                ticker_wins[op.ticker] += 1
            elif snap_90 and snap_90.result == "loss":
                ticker_losses[op.ticker] += 1

        best = sorted(ticker_wins, key=ticker_wins.get, reverse=True)[:3]
        worst = sorted(ticker_losses, key=ticker_losses.get, reverse=True)[:3]

        active = sum(
            1 for op in ops if op.verification.status in ("pending", "partial")
        )

        # 时间衰减计算
        today = datetime.date.today()
        opinion_dates = []
        for op in ops:
            if op.published_date:
                try:
                    opinion_dates.append(datetime.date.fromisoformat(op.published_date))
                except (ValueError, TypeError):
                    pass

        last_opinion_date = None
        days_since_last_opinion = None
        activity_weight = 1.0

        if opinion_dates:
            last_opinion_date = max(opinion_dates).isoformat()
            days_since_last_opinion = (today - max(opinion_dates)).days

            # 时间衰减权重：30天内=1.0，30-90天=0.8，90-180天=0.6，180-365天=0.4，365天+=0.2
            if days_since_last_opinion <= 30:
                activity_weight = 1.0
            elif days_since_last_opinion <= 90:
                activity_weight = 0.8
            elif days_since_last_opinion <= 180:
                activity_weight = 0.6
            elif days_since_last_opinion <= 365:
                activity_weight = 0.4
            else:
                activity_weight = 0.2

        metrics_90d = window_metrics.get("90d", {})

        # 信誉分加入时间衰减权重
        credibility_with_decay = (
            round(credibility * activity_weight, 1) if credibility else None
        )
        profiles.append(
            {
                "channel": channel,
                "analyst": analyst,
                "total_opinions": total,
                "verified_opinions": verified_count,
                "win_rate": win_rates,
                "win_rate_by_regime": win_rate_by_regime,
                "avg_return": avg_returns,
                "profit_loss_ratio": metrics_90d.get("profit_loss_ratio"),
                "max_single_loss": metrics_90d.get("max_single_loss"),
                "avg_win_return": metrics_90d.get("avg_win_return"),
                "avg_loss_return": metrics_90d.get("avg_loss_return"),
                "best_tickers": best,
                "worst_tickers": worst,
                "active_opinions": active,
                "credibility_score": credibility,
                "credibility_with_decay": credibility_with_decay,
                "last_opinion_date": last_opinion_date,
                "days_since_last_opinion": days_since_last_opinion,
                "activity_weight": activity_weight,
                "sample_sufficient": verified_count >= 30,
            }
        )

    # 按 90d 胜率排序
    profiles.sort(
        key=lambda p: (p["win_rate"].get("90d") or 0, p["verified_opinions"]),
        reverse=True,
    )
    return profiles


def compute_ticker_consensus(opinions: list[Opinion]) -> list[dict]:
    """
    按个股聚合生成多空共识图。
    """
    by_ticker: dict[str, list[Opinion]] = defaultdict(list)
    for op in opinions:
        if op.prediction.type in NON_VERIFIABLE_TYPES:
            continue
        by_ticker[op.ticker].append(op)

    consensus_list = []
    for ticker, ops in by_ticker.items():
        company = ops[0].company_name if ops else ""
        bullish = [op for op in ops if op.sentiment == "bullish"]
        bearish = [op for op in ops if op.sentiment == "bearish"]
        neutral = [op for op in ops if op.sentiment == "neutral"]

        # 收集目标价 / 支撑价
        targets = [
            op.prediction.target_price for op in ops if op.prediction.target_price
        ]
        supports = [
            op.prediction.price
            for op in ops
            if op.prediction.type in ("support", "entry_zone") and op.prediction.price
        ]

        # 加权情绪 (用各博主 90d 胜率加权)
        total_weight = 0
        sentiment_score = 0
        for op in ops:
            snap = op.verification.snapshots.get("90d")
            wr = 0.5  # 默认
            if snap and snap.result in ("win", "loss"):
                # 简化：用该条的结果近似
                wr = 1.0 if snap.result == "win" else 0.0
            weight = wr
            direction_val = (
                1.0
                if op.sentiment == "bullish"
                else (-1.0 if op.sentiment == "bearish" else 0.0)
            )
            sentiment_score += direction_val * weight
            total_weight += weight

        weighted_sentiment = (
            round(sentiment_score / total_weight, 2) if total_weight > 0 else 0
        )

        # 每个博主的摘要
        seen_analysts = set()
        top_analysts = []
        for op in ops:
            key = (op.analyst, op.channel)
            if key in seen_analysts:
                continue
            seen_analysts.add(key)
            snap = op.verification.snapshots.get("90d")
            top_analysts.append(
                {
                    "analyst": op.analyst,
                    "channel": op.channel,
                    "sentiment": op.sentiment,
                    "win_rate_90d": None,  # 在实际使用时从 profiles 交叉查
                }
            )

        consensus_list.append(
            {
                "ticker": ticker,
                "company_name": company,
                "active_opinions": len(ops),
                "consensus": {
                    "bullish_count": len(bullish),
                    "bearish_count": len(bearish),
                    "neutral_count": len(neutral),
                    "weighted_sentiment": weighted_sentiment,
                    "avg_target_price": round(sum(targets) / len(targets), 2)
                    if targets
                    else None,
                    "avg_support_price": round(sum(supports) / len(supports), 2)
                    if supports
                    else None,
                },
                "top_analysts": top_analysts,
            }
        )

    consensus_list.sort(key=lambda c: c["active_opinions"], reverse=True)
    return consensus_list


def print_summary(profiles: list[dict], consensus: list[dict]) -> None:
    """打印文字摘要到 stdout。"""
    print("\n" + "=" * 60)
    print("📊 博主胜率排行")
    print("=" * 60)
    for p in profiles:
        wr_parts = []
        for w in ("30d", "90d", "180d"):
            v = p["win_rate"].get(w)
            wr_parts.append(f"{v * 100:.0f}%" if v is not None else "N/A")
        suf = " ⚠️样本不足" if not p["sample_sufficient"] else ""
        score = f"⭐{p['credibility_score']}" if p["credibility_score"] else "N/A"
        print(
            f"  {p['analyst']} ({p['channel']}) — "
            f"观点:{p['total_opinions']} 已验:{p['verified_opinions']} "
            f"胜率: {'/'.join(wr_parts)} 信誉:{score}{suf}"
        )

    if consensus:
        print(f"\n{'=' * 60}")
        print("📈 个股多空共识 (Top 10)")
        print("=" * 60)
        for c in consensus[:10]:
            cons = c["consensus"]
            target = (
                f"${cons['avg_target_price']}" if cons["avg_target_price"] else "N/A"
            )
            print(
                f"  {c['ticker']} ({c['company_name']}) — "
                f"🟢{cons['bullish_count']} 🔴{cons['bearish_count']} ⚪{cons['neutral_count']} "
                f"加权情绪:{cons['weighted_sentiment']:+.2f} 平均目标:{target}"
            )
    print()
