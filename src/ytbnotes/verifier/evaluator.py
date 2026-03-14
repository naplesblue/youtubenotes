"""
逐条 Opinion 回验引擎

根据观点类型 + 实际行情数据，判定 win / loss / pending。
"""

import datetime
import logging
from dataclasses import dataclass, field
from typing import Optional

from ..tracker.models import Opinion, VerificationSnapshot, NON_VERIFIABLE_TYPES
from ..common.ticker_normalizer import normalize_ticker_symbol
from .market_data import fetch_price_history, get_price_on_date, get_market_regime

# 回验窗口（天数）
WINDOWS = {
    "30d": 30,
    "90d": 90,
    "180d": 180,
}

REGIME_MA_WINDOW = 50
REGIME_LOOKBACK_DAYS = max(REGIME_MA_WINDOW * 3, 120)


@dataclass
class VerificationContext:
    """回验共享上下文：一次拉取，多次复用。"""
    ticker_histories: dict[str, dict[str, dict]] = field(default_factory=dict)
    benchmark_history: dict[str, dict] = field(default_factory=dict)
    benchmark_ticker: str = "SPY"
    ma_window: int = REGIME_MA_WINDOW
    benchmark_prefetched: bool = False
    regime_cache: dict[str, str] = field(default_factory=dict)

    def get_history(self, ticker: str, start_date: str, end_date: str) -> dict[str, dict]:
        history = self.ticker_histories.get(ticker)
        if history is None:
            history = fetch_price_history(ticker, start_date, end_date)
            self.ticker_histories[ticker] = history
        return {
            ds: vals
            for ds, vals in history.items()
            if start_date <= ds <= end_date
        }

    def get_price_on_or_before(self, ticker: str, date: str) -> Optional[float]:
        start_dt = datetime.date.fromisoformat(date) - datetime.timedelta(days=7)
        start_str = start_dt.isoformat()
        history = self.get_history(ticker, start_str, date)
        if not history:
            return None
        candidates = [d for d in history.keys() if d <= date]
        if not candidates:
            return None
        close = history[max(candidates)].get("close")
        return float(close) if isinstance(close, (int, float)) else None

    def get_regime(self, date: str) -> str:
        if date in self.regime_cache:
            return self.regime_cache[date]

        history = self.benchmark_history
        if self.benchmark_prefetched and not history:
            # 基准已尝试预取但失败：避免每条观点重复请求网络
            self.regime_cache[date] = "neutral"
            return "neutral"

        if not history:
            regime = get_market_regime(
                date,
                benchmark_ticker=self.benchmark_ticker,
                ma_window=self.ma_window,
            )
            self.regime_cache[date] = regime
            return regime

        closes: list[float] = []
        for ds in sorted(history.keys()):
            if ds > date:
                continue
            close = history[ds].get("close")
            if isinstance(close, (int, float)):
                closes.append(float(close))

        if len(closes) < self.ma_window:
            regime = "neutral"
        else:
            current_close = closes[-1]
            ma_value = sum(closes[-self.ma_window:]) / self.ma_window
            if current_close > ma_value:
                regime = "bull"
            elif current_close < ma_value:
                regime = "bear"
            else:
                regime = "neutral"

        self.regime_cache[date] = regime
        return regime


def build_verification_context(
    opinions: list[Opinion],
    today: datetime.date | None = None,
    benchmark_ticker: str = "SPY",
) -> VerificationContext:
    """
    预拉行情数据：
    - 每个 ticker 仅拉一次（覆盖该 ticker 全部观点的回验区间）
    - benchmark(默认 SPY) 仅拉一次（覆盖全部窗口终点 + 均线回看）
    """
    today = today or datetime.date.today()
    max_window_days = max(WINDOWS.values())

    ticker_ranges: dict[str, tuple[str, str]] = {}
    regime_end_dates: list[datetime.date] = []

    for op in opinions:
        if op.prediction.type in NON_VERIFIABLE_TYPES:
            continue
        if not op.ticker:
            continue
        if not op.published_date:
            continue
        try:
            pub_date = datetime.date.fromisoformat(op.published_date)
        except ValueError:
            continue

        start = op.published_date
        end = min(today, pub_date + datetime.timedelta(days=max_window_days)).isoformat()
        existing = ticker_ranges.get(op.ticker)
        if existing is None:
            ticker_ranges[op.ticker] = (start, end)
        else:
            ticker_ranges[op.ticker] = (min(existing[0], start), max(existing[1], end))

        for window_days in WINDOWS.values():
            window_end = pub_date + datetime.timedelta(days=window_days)
            if today >= window_end:
                regime_end_dates.append(window_end)

    ticker_histories: dict[str, dict[str, dict]] = {}
    if ticker_ranges:
        logging.info(
            f"行情预取：{len(ticker_ranges)} 个 ticker（每个 ticker 单次拉取）"
        )
    for ticker, (start, end) in ticker_ranges.items():
        ticker_histories[ticker] = fetch_price_history(ticker, start, end)

    benchmark_history: dict[str, dict] = {}
    if regime_end_dates:
        benchmark_start = (
            min(regime_end_dates) - datetime.timedelta(days=REGIME_LOOKBACK_DAYS)
        ).isoformat()
        benchmark_end = max(regime_end_dates).isoformat()
        benchmark_history = fetch_price_history(
            benchmark_ticker,
            benchmark_start,
            benchmark_end,
        )
        logging.info(
            f"市场基准预取：{benchmark_ticker} {benchmark_start} -> {benchmark_end} "
            f"({len(benchmark_history)} 条)"
        )

    return VerificationContext(
        ticker_histories=ticker_histories,
        benchmark_history=benchmark_history,
        benchmark_ticker=benchmark_ticker,
        ma_window=REGIME_MA_WINDOW,
        benchmark_prefetched=bool(regime_end_dates),
    )


def _evaluate_single_window(
    opinion: Opinion,
    window_key: str,
    window_days: int,
    today: datetime.date,
    ctx: VerificationContext | None = None,
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
    regime = ctx.get_regime(end_str) if ctx else get_market_regime(end_str)

    # 拉取窗口内行情
    history = (
        ctx.get_history(opinion.ticker, start_str, end_str)
        if ctx
        else fetch_price_history(opinion.ticker, start_str, end_str)
    )
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


def verify_opinion(
    opinion: Opinion,
    today: datetime.date | None = None,
    ctx: VerificationContext | None = None,
) -> Opinion:
    """
    对一条 opinion 执行所有窗口的回验，更新其 verification 字段。
    返回更新后的 opinion（原地修改）。
    """
    today = today or datetime.date.today()

    # 兼容历史脏数据：回验前统一 ticker 规范化（会随 save_opinions 落盘）
    normalized_ticker = normalize_ticker_symbol(opinion.ticker, opinion.company_name)
    if normalized_ticker and normalized_ticker != opinion.ticker:
        logging.info(
            f"[{opinion.opinion_id}] ticker 映射: {opinion.ticker} -> {normalized_ticker}"
        )
        opinion.ticker = normalized_ticker

    # 跳过不可验证类型
    if not opinion.prediction.is_verifiable:
        opinion.verification.status = "excluded"
        return opinion

    # 填充 price_at_publish
    if opinion.price_at_publish is None and opinion.published_date:
        pub_price = (
            ctx.get_price_on_or_before(opinion.ticker, opinion.published_date)
            if ctx
            else get_price_on_date(opinion.ticker, opinion.published_date)
        )
        if pub_price:
            opinion.price_at_publish = round(pub_price, 2)

    all_done = True
    for window_key, window_days in WINDOWS.items():
        existing = opinion.verification.snapshots.get(window_key)
        if existing and existing.result in ("win", "loss"):
            continue  # 已有最终结果，不重复验证

        snapshot = _evaluate_single_window(
            opinion,
            window_key,
            window_days,
            today,
            ctx=ctx,
        )
        opinion.verification.snapshots[window_key] = snapshot
        if snapshot.result == "pending":
            all_done = False

    opinion.verification.status = "verified" if all_done else "partial"
    opinion.verification.last_verified = today.isoformat()
    return opinion
