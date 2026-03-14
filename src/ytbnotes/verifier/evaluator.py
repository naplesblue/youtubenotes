"""
逐条 Opinion 回验引擎

根据观点类型 + 实际行情数据，判定 win / loss / pending。
"""

import datetime
import logging
from typing import Optional

from ..tracker.models import Opinion, VerificationSnapshot, NON_VERIFIABLE_TYPES
from .market_data import fetch_price_history, get_price_on_date, get_market_regime

# 回验窗口（天数）
WINDOWS = {
    "30d": 30,
    "90d": 90,
    "180d": 180,
}


def _evaluate_single_window(
    opinion: Opinion,
    window_key: str,
    window_days: int,
    today: datetime.date,
) -> VerificationSnapshot:
    """
    对单条 opinion 在一个时间窗口内做回验判定。
    """
    pub_date = datetime.date.fromisoformat(opinion.published_date)
    window_end = pub_date + datetime.timedelta(days=window_days)

    # 窗口还未到期
    if today < window_end:
        return VerificationSnapshot(result="pending")

    pred = opinion.prediction
    start_str = opinion.published_date
    end_str = window_end.isoformat()
    regime = get_market_regime(end_str)

    # 拉取窗口内行情
    history = fetch_price_history(opinion.ticker, start_str, end_str)
    if not history:
        logging.warning(f"[{opinion.opinion_id}] {window_key}: 无行情数据")
        return VerificationSnapshot(result="pending", regime=regime)

    # 窗口末收盘价
    sorted_dates = sorted(history.keys())
    end_price = history[sorted_dates[-1]]["close"]
    publish_price = opinion.price_at_publish

    # 如果没有发布价，取窗口第一天
    if publish_price is None and sorted_dates:
        publish_price = history[sorted_dates[0]]["close"]

    if publish_price is None or publish_price <= 0:
        return VerificationSnapshot(result="pending", regime=regime)

    return_pct = round((end_price - publish_price) / publish_price, 4)

    # 窗口内最高 / 最低
    window_high = max(d["high"] for d in history.values())
    window_low = min(d["low"] for d in history.values())

    result = _judge(pred.type, pred.direction, pred.price, pred.target_price, pred.stop_loss,
                    publish_price, end_price, window_high, window_low, history)

    return VerificationSnapshot(
        price=round(end_price, 2),
        return_pct=return_pct,
        result=result,
        regime=regime,
    )


def _judge(
    pred_type: str,
    direction: str,
    price: Optional[float],
    target_price: Optional[float],
    stop_loss: Optional[float],
    publish_price: float,
    end_price: float,
    window_high: float,
    window_low: float,
    history: dict,
) -> str:
    """
    根据观点类型判定 win/loss。

    判定规则：
    - target_price: 窗口内曾触及目标价 → win
    - entry_zone:   窗口末盈利且未触发止损 → win
    - support:      窗口内未跌破支撑价 → win
    - resistance:   窗口内未突破阻力价 → win
    - direction_call: 窗口末方向一致 → win
    """
    if pred_type == "target_price":
        if target_price is None:
            target_price = price
        if target_price is None:
            return "pending"
        if direction in ("long", "hold"):
            return "win" if window_high >= target_price else "loss"
        else:  # short
            return "win" if window_low <= target_price else "loss"

    elif pred_type == "entry_zone":
        if price is None:
            return "pending"
        if direction == "long":
            # 止损检查
            if stop_loss and window_low < stop_loss:
                return "loss"
            return "win" if end_price > price else "loss"
        elif direction == "short":
            if stop_loss and window_high > stop_loss:
                return "loss"
            return "win" if end_price < price else "loss"
        else:  # hold
            return "win" if end_price >= price else "loss"

    elif pred_type == "support":
        if price is None:
            return "pending"
        return "win" if window_low >= price else "loss"

    elif pred_type == "resistance":
        if price is None:
            return "pending"
        return "win" if window_high <= price else "loss"

    elif pred_type == "direction_call":
        if direction == "long":
            return "win" if end_price > publish_price else "loss"
        elif direction == "short":
            return "win" if end_price < publish_price else "loss"
        return "pending"

    return "pending"


def verify_opinion(opinion: Opinion, today: datetime.date | None = None) -> Opinion:
    """
    对一条 opinion 执行所有窗口的回验，更新其 verification 字段。
    返回更新后的 opinion（原地修改）。
    """
    today = today or datetime.date.today()

    # 跳过不可验证类型
    if not opinion.prediction.is_verifiable:
        opinion.verification.status = "excluded"
        return opinion

    # 填充 price_at_publish
    if opinion.price_at_publish is None and opinion.published_date:
        pub_price = get_price_on_date(opinion.ticker, opinion.published_date)
        if pub_price:
            opinion.price_at_publish = round(pub_price, 2)

    all_done = True
    for window_key, window_days in WINDOWS.items():
        existing = opinion.verification.snapshots.get(window_key)
        if existing and existing.result in ("win", "loss"):
            continue  # 已有最终结果，不重复验证

        snapshot = _evaluate_single_window(opinion, window_key, window_days, today)
        opinion.verification.snapshots[window_key] = snapshot
        if snapshot.result == "pending":
            all_done = False

    opinion.verification.status = "verified" if all_done else "partial"
    opinion.verification.last_verified = today.isoformat()
    return opinion
