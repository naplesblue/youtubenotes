#!/usr/bin/env python3
"""
构建仪表盘聚合 JSON — web/public/data/dashboard.json

读取 opinions、blogger_profiles、ticker_consensus、market_cache、
download_history、video results，输出单个 dashboard.json 供 Astro 前端消费。
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from ytbnotes.common.ticker_normalizer import (
        market_ticker_candidates,
        normalize_ticker_symbol,
    )
except Exception:
    def market_ticker_candidates(raw_ticker: str, company_name: str = "") -> list[str]:
        t = (raw_ticker or "").strip().upper()
        return [t] if t else []

    def normalize_ticker_symbol(raw_ticker: str, company_name: str = "") -> str:
        return (raw_ticker or "").strip().upper()

# ── 输入路径 ─────────────────────────────────────────────────────────────────
OPINIONS_PATH = PROJECT_DIR / "data" / "opinions" / "opinions.json"
PROFILES_PATH = PROJECT_DIR / "data" / "reports" / "blogger_profiles.json"
CONSENSUS_PATH = PROJECT_DIR / "data" / "reports" / "ticker_consensus.json"
MARKET_CACHE_DIR = PROJECT_DIR / "data" / "opinions" / "market_cache"
DOWNLOAD_HISTORY_PATH = PROJECT_DIR / "data" / "download_history.json"
RESULTS_DIR = PROJECT_DIR / "data" / "results"

# ── 输出路径 ─────────────────────────────────────────────────────────────────
OUTPUT_DIR = PROJECT_DIR / "web" / "public" / "data"
OUTPUT_PATH = OUTPUT_DIR / "dashboard.json"

NON_VERIFIABLE_TYPES = {"reference_only", "stop_loss"}
GENERIC_ANALYST_NAMES = {"", "unknown", "发言人", "主播", "host", "analyst"}
ANALYST_ALIASES_BY_CHANNEL = {
    "视野环球财经": {
        "RINO": "犀牛 Rihno",
        "RENO": "犀牛 Rihno",
        "发言人": "犀牛 Rihno",
        "Sunny": "犀牛 Rihno",
        "犀牛": "犀牛 Rihno",
    }
}


def load_json(path: Path):
    if not path.exists():
        print(f"  [WARN] 文件不存在: {path}")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_opinions() -> list[dict]:
    data = load_json(OPINIONS_PATH)
    return data if isinstance(data, list) else []


def load_profiles() -> list[dict]:
    data = load_json(PROFILES_PATH)
    return data if isinstance(data, list) else []


def load_consensus() -> list[dict]:
    data = load_json(CONSENSUS_PATH)
    return data if isinstance(data, list) else []


def load_market_cache() -> dict[str, dict]:
    """返回 {ticker: {date_str: {open, high, low, close}}}"""
    result = {}
    if not MARKET_CACHE_DIR.exists():
        return result
    for f in MARKET_CACHE_DIR.glob("*.json"):
        # 文件名格式: TICKER_YEAR.json
        ticker = f.stem.rsplit("_", 1)[0]
        data = load_json(f)
        if data:
            if ticker not in result:
                result[ticker] = {}
            result[ticker].update(data)
    return result


def load_download_history() -> dict:
    data = load_json(DOWNLOAD_HISTORY_PATH)
    return data if isinstance(data, dict) else {}


def load_video_results() -> list[dict]:
    """扫描 data/results/ 下所有 video JSON，提取 metadata + mentioned_tickers."""
    videos = []
    if not RESULTS_DIR.exists():
        return videos
    for channel_dir in RESULTS_DIR.iterdir():
        if not channel_dir.is_dir():
            continue
        for date_dir in channel_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for json_file in date_dir.glob("*.json"):
                # 跳过 price_levels 文件
                if json_file.name.endswith("_price_levels.json"):
                    continue
                # 跳过 .md 文件（不应被 glob 捕获，但以防万一）
                if json_file.suffix != ".json":
                    continue
                data = load_json(json_file)
                if not data or "metadata" not in data:
                    continue
                meta = data["metadata"]
                videos.append({
                    "video_id": meta.get("video_id", json_file.stem),
                    "title": meta.get("title", ""),
                    "channel": meta.get("channel", channel_dir.name),
                    "host": meta.get("host", ""),
                    "date": meta.get("date", date_dir.name),
                    "youtube_url": meta.get("youtube_url", ""),
                    "mentioned_tickers": data.get("mentioned_tickers", []),
                    "key_points": data.get("key_points", [])[:3],
                })
    return videos


def _safe_float(value):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def _build_channel_analyst_map(videos: list[dict], opinions: list[dict]) -> dict[str, str]:
    host_counter: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    analyst_counter: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for v in videos:
        ch = (v.get("channel") or "").strip()
        host = (v.get("host") or "").strip()
        if ch and host:
            host_counter[ch][host] += 1

    for o in opinions:
        ch = (o.get("channel") or "").strip()
        an = (o.get("analyst") or "").strip()
        if ch and an:
            analyst_counter[ch][an] += 1

    result: dict[str, str] = {}
    for ch in set(host_counter.keys()) | set(analyst_counter.keys()):
        if host_counter.get(ch):
            result[ch] = max(host_counter[ch].items(), key=lambda x: x[1])[0]
            continue
        candidates = [(name, cnt) for name, cnt in analyst_counter[ch].items()
                      if name and name.strip().lower() not in GENERIC_ANALYST_NAMES]
        if candidates:
            result[ch] = max(candidates, key=lambda x: x[1])[0]
    return result


def _normalize_analyst_name(channel: str, analyst: str, channel_analyst_map: dict[str, str]) -> str:
    ch = (channel or "").strip()
    an = (analyst or "").strip()

    if ch in ANALYST_ALIASES_BY_CHANNEL:
        mapped = ANALYST_ALIASES_BY_CHANNEL[ch].get(an)
        if mapped:
            return mapped

    if an.strip().lower() in GENERIC_ANALYST_NAMES:
        fallback = channel_analyst_map.get(ch)
        if fallback:
            return fallback
    return an or channel_analyst_map.get(ch, an)


def _resolve_market_series(ticker: str, company_name: str, market_cache: dict[str, dict]) -> dict:
    keys = []
    canonical = normalize_ticker_symbol(ticker, company_name)
    if canonical:
        keys.append(canonical)
    keys.append((ticker or "").strip().upper())
    for c in market_ticker_candidates(ticker, company_name):
        keys.append(c)

    seen = set()
    best_key = None
    best_len = -1
    for k in keys:
        k = (k or "").strip().upper()
        if not k or k in seen:
            continue
        seen.add(k)
        series = market_cache.get(k)
        if isinstance(series, dict) and len(series) > best_len:
            best_len = len(series)
            best_key = k
    return market_cache.get(best_key, {}) if best_key else {}


def _filtered_price_means(tk_opinions: list[dict], latest_close: float | None) -> tuple[float | None, float | None]:
    target_vals = []
    support_vals = []
    refs = []

    for o in tk_opinions:
        pred = o.get("prediction", {}) or {}
        ptype = pred.get("type")

        price_at_pub = _safe_float(o.get("price_at_publish"))
        if price_at_pub and 0 < price_at_pub < 2_000_000:
            refs.append(price_at_pub)

        t = _safe_float(pred.get("target_price"))
        if t and t > 0:
            target_vals.append(t)

        p = _safe_float(pred.get("price"))
        if p and p > 0 and ptype in ("support", "entry_zone"):
            support_vals.append(p)

    if latest_close and latest_close > 0:
        refs.append(latest_close)

    ref_price = _median(refs)
    if ref_price:
        min_allowed = max(0.01, ref_price * 0.05)
        max_allowed = max(ref_price * 20.0, ref_price + 50.0)
    else:
        min_allowed = 0.01
        max_allowed = 2_000_000.0

    def keep(vals: list[float]) -> list[float]:
        return [v for v in vals if min_allowed <= v <= max_allowed]

    valid_targets = keep(target_vals)
    valid_supports = keep(support_vals)

    avg_target = round(sum(valid_targets) / len(valid_targets), 2) if valid_targets else None
    avg_support = round(sum(valid_supports) / len(valid_supports), 2) if valid_supports else None
    return avg_target, avg_support


def _normalize_opinions_analyst(opinions: list[dict], channel_analyst_map: dict[str, str]) -> list[dict]:
    normalized = []
    for o in opinions:
        item = dict(o)
        item["analyst"] = _normalize_analyst_name(
            channel=item.get("channel", ""),
            analyst=item.get("analyst", ""),
            channel_analyst_map=channel_analyst_map,
        )
        normalized.append(item)
    return normalized


def build_stats(opinions, videos, profiles, consensus, download_history):
    """构建统计概览。"""
    # 从 download_history 提取所有频道
    channels = set()
    for feed_data in download_history.values():
        for vid_data in feed_data.values():
            ch = vid_data.get("channel_name")
            if ch:
                channels.add(ch)
    # 也从 videos 中收集
    for v in videos:
        if v.get("channel"):
            channels.add(v["channel"])

    verifiable = [o for o in opinions if o.get("prediction", {}).get("type") not in NON_VERIFIABLE_TYPES]
    verified = [o for o in verifiable
                if o.get("verification", {}).get("status") in ("verified",)]
    pending = [o for o in verifiable
               if o.get("verification", {}).get("status") in ("pending", "partial")]

    # 数据时间跨度
    dates = []
    for v in videos:
        d = v.get("date")
        if d:
            dates.append(d)
    for o in opinions:
        d = o.get("published_date")
        if d:
            dates.append(d)
    dates.sort()

    all_tickers = set()
    for o in opinions:
        t = o.get("ticker")
        if t:
            all_tickers.add(t)

    return {
        "total_videos": len(videos),
        "total_channels": len(channels),
        "total_opinions": len(opinions),
        "verifiable_opinions": len(verifiable),
        "verified_count": len(verified),
        "pending_count": len(pending),
        "total_tickers": len(all_tickers),
        "total_bloggers": len(profiles),
        "date_range": {
            "start": dates[0] if dates else None,
            "end": dates[-1] if dates else None,
        },
    }


def build_data_quality(opinions, videos, consensus):
    """检查数据质量问题。"""
    issues = []

    # 1. price_at_publish 缺失
    missing_price = [o for o in opinions if o.get("price_at_publish") is None]
    if missing_price:
        issues.append({
            "type": "missing_price_at_publish",
            "title": "price_at_publish 缺失",
            "severity": "warning",
            "count": len(missing_price),
            "total": len(opinions),
            "percentage": round(len(missing_price) / len(opinions) * 100, 1) if opinions else 0,
            "affected": [{"opinion_id": o.get("opinion_id"), "ticker": o.get("ticker"),
                          "analyst": o.get("analyst"), "date": o.get("published_date")}
                         for o in missing_price[:20]],
        })

    # 2. Ticker 别名冲突
    ticker_names = defaultdict(set)
    for o in opinions:
        t = o.get("ticker")
        cn = o.get("company_name")
        if t and cn:
            ticker_names[t].add(cn)

    # 检查可能的别名对
    alias_conflicts = []
    tickers = list(ticker_names.keys())
    known_aliases = [
        ({"BRK", "BRK.B", "BRK-B"}, "Berkshire Hathaway"),
        ({"MARVEL", "MRVL"}, "Marvell"),
    ]
    for alias_set, desc in known_aliases:
        found = alias_set & set(tickers)
        if len(found) > 1:
            alias_conflicts.append({"tickers": sorted(found), "description": desc})

    if alias_conflicts:
        issues.append({
            "type": "ticker_alias_conflict",
            "title": "Ticker 别名冲突",
            "severity": "warning",
            "conflicts": alias_conflicts,
        })

    # 3. 分析师别名
    analyst_names = defaultdict(set)
    for o in opinions:
        ch = o.get("channel")
        an = o.get("analyst")
        if ch and an:
            analyst_names[ch].add(an)
    analyst_aliases = []
    # Check across channels for similar names
    all_analysts = set()
    for o in opinions:
        an = o.get("analyst")
        if an:
            all_analysts.add(an)
    known_analyst_aliases = [
        ({"RINO", "RENO", "犀牛 Rihno", "犀牛"}, "视野环球财经"),
    ]
    for alias_set, desc in known_analyst_aliases:
        found = alias_set & all_analysts
        if len(found) > 1:
            analyst_aliases.append({"names": sorted(found), "channel": desc})

    if analyst_aliases:
        issues.append({
            "type": "analyst_alias",
            "title": "分析师别名冲突",
            "severity": "info",
            "aliases": analyst_aliases,
        })

    # 4. 非标情绪值
    standard_sentiments = {"bullish", "bearish", "neutral"}
    nonstandard = defaultdict(int)
    for o in opinions:
        s = o.get("sentiment", "")
        if s and s not in standard_sentiments:
            nonstandard[s] += 1
    if nonstandard:
        issues.append({
            "type": "nonstandard_sentiment",
            "title": "非标情绪值",
            "severity": "warning",
            "values": dict(nonstandard),
        })

    # 5. 无观点的频道
    channels_with_opinions = set(o.get("channel") for o in opinions if o.get("channel"))
    all_channels = set(v.get("channel") for v in videos if v.get("channel"))
    channels_without = all_channels - channels_with_opinions
    if channels_without:
        issues.append({
            "type": "channels_without_opinions",
            "title": "无观点提取的频道",
            "severity": "info",
            "channels": sorted(channels_without),
            "with_opinions": len(channels_with_opinions),
            "total_channels": len(all_channels),
        })

    # 6. 空价位预测
    no_price = [o for o in opinions
                if o.get("prediction", {}).get("price") is None
                and o.get("prediction", {}).get("type") not in ("direction_call", "reference_only")]
    if no_price:
        issues.append({
            "type": "missing_prediction_price",
            "title": "缺少预测价位",
            "severity": "info",
            "count": len(no_price),
            "total": len(opinions),
        })

    return issues


def build_bloggers(profiles, opinions):
    """构建博主详情数据。"""
    opinions_by_channel = defaultdict(list)
    for o in opinions:
        ch = o.get("channel")
        if ch:
            opinions_by_channel[ch].append(o)

    bloggers = []
    for p in profiles:
        channel = p.get("channel", "")
        ch_opinions = opinions_by_channel.get(channel, [])

        # 按 ticker 分组统计
        ticker_counts = defaultdict(int)
        sentiment_dist = defaultdict(int)
        for o in ch_opinions:
            t = o.get("ticker")
            if t:
                ticker_counts[t] += 1
            s = o.get("sentiment", "neutral")
            sentiment_dist[s] += 1

        top_tickers = sorted(ticker_counts.items(), key=lambda x: -x[1])[:10]

        # 观点时间线（按日统计）
        daily_counts = defaultdict(int)
        for o in ch_opinions:
            d = o.get("published_date")
            if d:
                daily_counts[d] += 1

        bloggers.append({
            "channel": channel,
            "analyst": p.get("analyst", ""),
            "total_opinions": p.get("total_opinions", 0),
            "verified_opinions": p.get("verified_opinions", 0),
            "win_rate": p.get("win_rate", {}),
            "avg_return": p.get("avg_return", {}),
            "credibility_score": p.get("credibility_score"),
            "sample_sufficient": p.get("sample_sufficient", False),
            "top_tickers": [{"ticker": t, "count": c} for t, c in top_tickers],
            "sentiment_distribution": dict(sentiment_dist),
            "daily_activity": dict(sorted(daily_counts.items())),
            "opinions": [{
                "opinion_id": o.get("opinion_id"),
                "ticker": o.get("ticker"),
                "company_name": o.get("company_name"),
                "sentiment": o.get("sentiment"),
                "published_date": o.get("published_date"),
                "prediction": o.get("prediction", {}),
                "price_at_publish": o.get("price_at_publish"),
                "verification": o.get("verification", {}),
            } for o in sorted(ch_opinions, key=lambda x: x.get("published_date", ""), reverse=True)],
        })

    return bloggers


def build_tickers(consensus, opinions, market_cache, channel_analyst_map):
    """构建 ticker 详情数据。"""
    opinions_by_ticker = defaultdict(list)
    for o in opinions:
        t = o.get("ticker")
        if t:
            opinions_by_ticker[t].append(o)

    tickers = []
    for c in consensus:
        ticker = c.get("ticker", "")
        tk_opinions = opinions_by_ticker.get(ticker, [])

        # 获取行情数据（最近 90 天足矣）
        price_data = []
        market_series = _resolve_market_series(ticker, c.get("company_name", ""), market_cache)
        if market_series:
            for date_str, ohlc in sorted(market_series.items()):
                close = _safe_float(ohlc.get("close"))
                open_ = _safe_float(ohlc.get("open"))
                high = _safe_float(ohlc.get("high"))
                low = _safe_float(ohlc.get("low"))
                if close is None:
                    continue
                price_data.append({
                    "date": date_str,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                })

        latest_close = price_data[-1]["close"] if price_data else None
        avg_target, avg_support = _filtered_price_means(tk_opinions, latest_close)

        # 观点标记点（供叠加图用）
        opinion_markers = []
        for o in tk_opinions:
            opinion_markers.append({
                "date": o.get("published_date"),
                "analyst": o.get("analyst"),
                "sentiment": o.get("sentiment"),
                "type": o.get("prediction", {}).get("type"),
                "direction": o.get("prediction", {}).get("direction"),
                "price": o.get("prediction", {}).get("price"),
                "target_price": o.get("prediction", {}).get("target_price"),
                "confidence": o.get("prediction", {}).get("confidence"),
                "price_at_publish": o.get("price_at_publish"),
            })

        consensus_view = dict(c.get("consensus", {}) or {})
        consensus_view["avg_target_price"] = avg_target
        consensus_view["avg_support_price"] = avg_support

        top_analysts = []
        seen_analysts = set()
        for a in c.get("top_analysts", []):
            channel = a.get("channel", "")
            normalized_analyst = _normalize_analyst_name(
                channel=channel,
                analyst=a.get("analyst", ""),
                channel_analyst_map=channel_analyst_map,
            )
            key = (channel, normalized_analyst)
            if key in seen_analysts:
                continue
            seen_analysts.add(key)
            item = dict(a)
            item["analyst"] = normalized_analyst
            top_analysts.append(item)

        tickers.append({
            "ticker": ticker,
            "company_name": c.get("company_name", ""),
            "active_opinions": c.get("active_opinions", 0),
            "consensus": consensus_view,
            "top_analysts": top_analysts,
            "price_data": price_data,
            "opinion_markers": opinion_markers,
        })

    return tickers


def build_compact_opinions(opinions):
    """构建精简版观点列表（context 截断至 200 字）。"""
    result = []
    for o in opinions:
        pred = o.get("prediction", {})
        context = pred.get("context", "") or ""
        result.append({
            "opinion_id": o.get("opinion_id"),
            "video_id": o.get("video_id"),
            "channel": o.get("channel"),
            "analyst": o.get("analyst"),
            "published_date": o.get("published_date"),
            "ticker": o.get("ticker"),
            "company_name": o.get("company_name"),
            "sentiment": o.get("sentiment"),
            "prediction": {
                "type": pred.get("type"),
                "direction": pred.get("direction"),
                "price": pred.get("price"),
                "target_price": pred.get("target_price"),
                "stop_loss": pred.get("stop_loss"),
                "confidence": pred.get("confidence"),
                "conviction": pred.get("conviction"),
                "horizon": pred.get("horizon"),
                "context": context[:200],
            },
            "price_at_publish": o.get("price_at_publish"),
            "verification": o.get("verification", {}),
        })
    return result


def build_activity(opinions, videos, limit=30):
    """构建最近活动时间线。"""
    events = []

    for v in videos:
        events.append({
            "type": "video",
            "date": v.get("date", ""),
            "channel": v.get("channel", ""),
            "title": v.get("title", ""),
            "video_id": v.get("video_id", ""),
            "tickers": v.get("mentioned_tickers", []),
        })

    for o in opinions:
        events.append({
            "type": "opinion",
            "date": o.get("published_date", ""),
            "channel": o.get("channel", ""),
            "analyst": o.get("analyst", ""),
            "ticker": o.get("ticker", ""),
            "sentiment": o.get("sentiment", ""),
            "prediction_type": o.get("prediction", {}).get("type", ""),
        })

    events.sort(key=lambda x: x.get("date", ""), reverse=True)
    return events[:limit]


def main():
    print("=== 构建仪表盘数据 ===")
    print()

    # 加载所有数据
    print("[1/6] 加载 opinions...")
    opinions = load_opinions()
    print(f"  → {len(opinions)} 条观点")

    print("[2/6] 加载 blogger profiles...")
    profiles = load_profiles()
    print(f"  → {len(profiles)} 个博主")

    print("[3/6] 加载 ticker consensus...")
    consensus = load_consensus()
    print(f"  → {len(consensus)} 个 ticker")

    print("[4/6] 加载 market cache...")
    market_cache = load_market_cache()
    print(f"  → {len(market_cache)} 个 ticker 行情")

    print("[5/6] 加载 download history...")
    download_history = load_download_history()
    total_downloads = sum(len(v) for v in download_history.values())
    print(f"  → {total_downloads} 条下载记录")

    print("[6/6] 扫描 video results...")
    videos = load_video_results()
    print(f"  → {len(videos)} 个视频分析")
    channel_analyst_map = _build_channel_analyst_map(videos, opinions)
    opinions_normalized = _normalize_opinions_analyst(opinions, channel_analyst_map)

    print()
    print("聚合数据...")

    dashboard = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stats": build_stats(opinions_normalized, videos, profiles, consensus, download_history),
        "data_quality": build_data_quality(opinions_normalized, videos, consensus),
        "bloggers": build_bloggers(profiles, opinions_normalized),
        "tickers": build_tickers(consensus, opinions_normalized, market_cache, channel_analyst_map),
        "opinions": build_compact_opinions(opinions_normalized),
        "videos": videos,
        "activity": build_activity(opinions_normalized, videos),
    }

    # 写入
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"\n✓ 已写入 {OUTPUT_PATH}")
    print(f"  大小: {size_kb:.1f} KB")
    print(f"  统计: {dashboard['stats']}")


if __name__ == "__main__":
    main()
