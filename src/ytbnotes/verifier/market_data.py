"""
行情数据拉取与缓存

使用 yfinance 获取美股日K数据，按 {TICKER}_{年}.json 缓存在本地。
"""

import json
import logging
import datetime
from pathlib import Path
from typing import Optional

from ..common.ticker_normalizer import market_ticker_candidates

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CACHE_DIR = _PROJECT_DIR / "data" / "opinions" / "market_cache"


def _cache_path(ticker: str, year: int, cache_dir: Path | None = None) -> Path:
    d = cache_dir or DEFAULT_CACHE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{ticker.upper()}_{year}.json"


def _load_cache(ticker: str, year: int, cache_dir: Path | None = None) -> dict:
    """加载缓存的日K数据。返回 {date_str: {open, high, low, close, volume}}。"""
    fp = _cache_path(ticker, year, cache_dir)
    if not fp.exists():
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(ticker: str, year: int, data: dict, cache_dir: Path | None = None) -> None:
    fp = _cache_path(ticker, year, cache_dir)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fetch_price_history(
    ticker: str,
    start_date: str,
    end_date: str,
    cache_dir: Path | None = None,
) -> dict[str, dict]:
    """
    获取日K数据。优先从缓存读取，缺失时增量拉取。
    返回 {date_str: {"open": float, "high": float, "low": float, "close": float}}
    """
    try:
        import yfinance as yf
    except ImportError:
        logging.error("yfinance 未安装，请执行: pip install yfinance")
        return {}

    candidates = market_ticker_candidates(ticker)
    if not candidates:
        logging.warning(f"无效 ticker，跳过行情拉取: {ticker}")
        return {}

    for market_ticker in candidates:
        history = _fetch_price_history_for_symbol(
            yf=yf,
            market_ticker=market_ticker,
            start_date=start_date,
            end_date=end_date,
            cache_dir=cache_dir,
        )
        if history:
            if market_ticker != ticker.upper():
                logging.debug(f"ticker 映射行情源: {ticker} -> {market_ticker}")
            return history

    return {}


def _fetch_price_history_for_symbol(
    yf,
    market_ticker: str,
    start_date: str,
    end_date: str,
    cache_dir: Path | None = None,
) -> dict[str, dict]:
    start_dt = datetime.date.fromisoformat(start_date)
    end_dt = datetime.date.fromisoformat(end_date)

    years = set()
    d = start_dt
    while d <= end_dt:
        years.add(d.year)
        if d.month == 12 and d.day == 31:
            d = d.replace(year=d.year + 1, month=1, day=1)
        else:
            d = d + datetime.timedelta(days=365)
    years.add(end_dt.year)

    merged: dict[str, dict] = {}
    for y in years:
        merged.update(_load_cache(market_ticker, y, cache_dir))

    need_fetch = False
    if not merged:
        need_fetch = True
    else:
        cached_dates = sorted(merged.keys())
        earliest = cached_dates[0]
        latest = cached_dates[-1]
        # 仅检查 latest 会漏掉“只有近期缓存、历史区间为空”的场景
        has_overlap = not (latest < start_date or earliest > end_date)
        if (earliest > start_date) or (latest < end_date) or (not has_overlap):
            need_fetch = True

    if need_fetch:
        logging.debug(f"yfinance: 拉取 {market_ticker} {start_date} → {end_date}")
        try:
            fetch_end = (end_dt + datetime.timedelta(days=3)).isoformat()
            df = yf.download(
                market_ticker,
                start=start_date,
                end=fetch_end,
                progress=False,
                auto_adjust=True,
            )
            if df is not None and not df.empty:
                for idx, row in df.iterrows():
                    date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                    merged[date_str] = {
                        "open": round(float(row["Open"].iloc[0]) if hasattr(row["Open"], "iloc") else float(row["Open"]), 2),
                        "high": round(float(row["High"].iloc[0]) if hasattr(row["High"], "iloc") else float(row["High"]), 2),
                        "low": round(float(row["Low"].iloc[0]) if hasattr(row["Low"], "iloc") else float(row["Low"]), 2),
                        "close": round(float(row["Close"].iloc[0]) if hasattr(row["Close"], "iloc") else float(row["Close"]), 2),
                    }
                by_year: dict[int, dict] = {}
                for ds, vals in merged.items():
                    y = int(ds[:4])
                    by_year.setdefault(y, {})[ds] = vals
                for y, ydata in by_year.items():
                    _save_cache(market_ticker, y, ydata, cache_dir)
                logging.debug(f"yfinance: {market_ticker} 获得 {len(df)} 条日K数据")
        except Exception as e:
            logging.error(f"yfinance 拉取 {market_ticker} 失败: {e}")

    return {
        ds: vals for ds, vals in sorted(merged.items())
        if start_date <= ds <= end_date
    }


def get_price_on_date(ticker: str, date: str, cache_dir: Path | None = None) -> Optional[float]:
    """获取某日收盘价。如该日无数据（休市），找最近前一个交易日。"""
    dt = datetime.date.fromisoformat(date)
    # 往前找最多 7 天
    start = (dt - datetime.timedelta(days=7)).isoformat()
    history = fetch_price_history(ticker, start, date, cache_dir)
    if not history:
        return None
    # 取 <= date 的最新一天
    candidates = sorted([d for d in history.keys() if d <= date])
    if not candidates:
        return None
    return history[candidates[-1]]["close"]


def get_market_regime(
    date: str,
    cache_dir: Path | None = None,
    benchmark_ticker: str = "SPY",
    ma_window: int = 50,
) -> str:
    """
    判断给定日期的市场环境：
    - 收盘价 > ma_window 日均线 -> bull
    - 收盘价 < ma_window 日均线 -> bear
    - 数据不足 / 无法判断 -> neutral
    """
    try:
        dt = datetime.date.fromisoformat(date)
    except ValueError:
        logging.warning(f"无效日期，无法判断市场环境: {date}")
        return "neutral"

    if ma_window <= 0:
        logging.warning(f"无效均线窗口，无法判断市场环境: {ma_window}")
        return "neutral"

    # 使用足够长的自然日窗口，覆盖 50 个交易日及节假日缺口
    lookback_days = max(ma_window * 3, 120)
    start = (dt - datetime.timedelta(days=lookback_days)).isoformat()
    history = fetch_price_history(benchmark_ticker, start, date, cache_dir)
    if not history:
        return "neutral"

    closes: list[float] = []
    for ds in sorted(history.keys()):
        if ds > date:
            continue
        close = history[ds].get("close")
        if isinstance(close, (int, float)):
            closes.append(float(close))

    if len(closes) < ma_window:
        return "neutral"

    current_close = closes[-1]
    ma_value = sum(closes[-ma_window:]) / ma_window

    if current_close > ma_value:
        return "bull"
    if current_close < ma_value:
        return "bear"
    return "neutral"
