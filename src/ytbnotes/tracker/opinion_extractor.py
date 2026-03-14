"""
观点提取器

从已有的 analysis result JSON 中读取 mentioned_tickers，
调用 Cerebras GPT-OSS-120B 做精标注，生成 Opinion 记录。
"""

import os
import re
import json
import time
import logging
import hashlib
import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .models import (
    Opinion,
    Prediction,
    Verification,
    make_opinion_id,
    PREDICTION_TYPES,
    DIRECTIONS,
    CONFIDENCES,
    CONVICTIONS,
    HORIZONS,
)
from ..common.ticker_normalizer import normalize_ticker_symbol

# ─── Cerebras 配置 ───
CEREBRAS_API_KEY  = os.getenv("CEREBRAS_API_KEY", "").strip()
CEREBRAS_BASE_URL = os.getenv("OPINION_BASE_URL", "https://api.cerebras.ai/v1").strip() or "https://api.cerebras.ai/v1"
CEREBRAS_MODEL    = os.getenv("OPINION_MODEL_NAME", "qwen-3-235b-a22b-instruct-2507").strip() or "qwen-3-235b-a22b-instruct-2507"
CEREBRAS_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("OPINION_FALLBACK_MODELS", "gpt-oss-120b").split(",")
    if m.strip()
]
OPINION_MAX_REQUESTS_PER_MINUTE = int(os.getenv("OPINION_MAX_REQUESTS_PER_MINUTE", "12"))
OPINION_MAX_RETRIES = int(os.getenv("OPINION_MAX_RETRIES", "3"))
OPINION_RETRY_BACKOFF_SECONDS = float(os.getenv("OPINION_RETRY_BACKOFF_SECONDS", "2.0"))
OPINION_BACKFILL_FILE_SLEEP_SECONDS = float(os.getenv("OPINION_BACKFILL_FILE_SLEEP_SECONDS", "0"))

_MIN_REQUEST_INTERVAL = 60.0 / max(1, OPINION_MAX_REQUESTS_PER_MINUTE)
_NEXT_ALLOWED_REQUEST_AT = 0.0
_UNAVAILABLE_MODELS: set[str] = set()

# 项目根目录
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
RESULTS_DIR  = _PROJECT_DIR / "data" / "results"
EXTRACT_STATE_FILE = _PROJECT_DIR / "data" / "opinions" / "extract_state.json"
_DATE_DIR_RE = re.compile(r"^\d{8}$")
_STATE_SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _file_fingerprint(path: Path) -> dict:
    st = path.stat()
    return {
        "source_size": int(st.st_size),
        "source_mtime_ns": int(st.st_mtime_ns),
    }


def _state_matches_file(state_item: dict, fp: dict) -> bool:
    if not isinstance(state_item, dict):
        return False
    return (
        int(state_item.get("source_size", -1)) == int(fp.get("source_size", -2))
        and int(state_item.get("source_mtime_ns", -1)) == int(fp.get("source_mtime_ns", -2))
    )


def _load_extract_state() -> dict[str, dict]:
    fp = EXTRACT_STATE_FILE
    if not fp.exists():
        return {}
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
        return raw["items"]
    if isinstance(raw, dict):
        # 兼容旧结构：直接是 {video_id: state}
        return {k: v for k, v in raw.items() if isinstance(v, dict)}
    return {}


def _save_extract_state(items: dict[str, dict]) -> None:
    EXTRACT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _STATE_SCHEMA_VERSION,
        "updated_at": _utc_now_iso(),
        "items": items,
    }
    tmp = EXTRACT_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(EXTRACT_STATE_FILE)


def _cerebras_cache_path(result_json_path: Path) -> Path:
    return result_json_path.with_name(f"{result_json_path.stem}_cerebras_opinions.json")


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()


def _load_cerebras_cache(
    result_json_path: Path,
    video_id: str,
    requested_model: str,
    prompt_hash: str,
) -> list | None:
    cache_fp = _cerebras_cache_path(result_json_path)
    if not cache_fp.exists():
        return None
    try:
        raw = json.loads(cache_fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("video_id") != video_id:
        return None
    if raw.get("model_requested") != requested_model:
        return None
    if raw.get("prompt_hash") != prompt_hash:
        return None
    refined = raw.get("refined")
    return refined if isinstance(refined, list) else None


def _save_cerebras_cache(
    result_json_path: Path,
    video_id: str,
    requested_model: str,
    used_model: str,
    prompt_hash: str,
    refined: list,
) -> None:
    cache_fp = _cerebras_cache_path(result_json_path)
    payload = {
        "video_id": video_id,
        "model_requested": requested_model,
        "model_used": used_model,
        "prompt_hash": prompt_hash,
        "updated_at": _utc_now_iso(),
        "refined": refined,
    }
    cache_fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_date(value: str) -> datetime.date | None:
    v = str(value or "").strip()
    if not v:
        return None
    try:
        return datetime.date.fromisoformat(v)
    except Exception:
        pass
    if len(v) == 8 and v.isdigit():
        try:
            return datetime.datetime.strptime(v, "%Y%m%d").date()
        except Exception:
            return None
    return None


def _infer_video_date_from_path(path: Path) -> datetime.date | None:
    for part in reversed(path.parts):
        if _DATE_DIR_RE.match(part):
            dt = _parse_date(part)
            if dt:
                return dt
    return None


def _inspect_result_mode(result_json_path: Path) -> str:
    """
    仅用于 dry-run 的轻量检查：
    - no_tickers: 无 mentioned_tickers
    - direct: 可直出映射（不需 Cerebras）
    - cerebras: 需要调用 Cerebras
    - error: 文件解析失败
    """
    try:
        data = json.loads(result_json_path.read_text(encoding="utf-8"))
    except Exception:
        return "error"
    mentioned = data.get("mentioned_tickers", [])
    if not mentioned:
        return "no_tickers"
    if _has_direct_prediction_fields(mentioned):
        return "direct"
    return "cerebras"


def _throttle_request_rate() -> None:
    """全局串行节流：保证请求间隔不超过配置速率。"""
    global _NEXT_ALLOWED_REQUEST_AT
    now = time.monotonic()
    if now < _NEXT_ALLOWED_REQUEST_AT:
        time.sleep(_NEXT_ALLOWED_REQUEST_AT - now)
        now = time.monotonic()
    _NEXT_ALLOWED_REQUEST_AT = now + _MIN_REQUEST_INTERVAL


def _is_model_not_found_error(msg: str) -> bool:
    m = str(msg or "").lower()
    return (
        "model_not_found" in m
        or "does not exist" in m
        or ("404" in m and "model" in m)
    )


def _is_rate_limited_error(msg: str) -> bool:
    m = str(msg or "").lower()
    return (
        "429" in m
        or "rate limit" in m
        or "too many requests" in m
    )


def _build_refinement_prompt(mentioned_tickers: list, brief_text: str, video_date: str) -> str:
    """构建 Cerebras 精标注 prompt（与 POC 保持一致）。"""
    tickers_json = json.dumps(mentioned_tickers, ensure_ascii=False, indent=2)
    brief_excerpt = (brief_text or "")[:2000]

    return f"""你是一名量化金融数据工程师。你的任务是对 YouTube 财经博主的股票观点进行**结构化精标注**。

## 输入数据

### 视频日期
{video_date}

### 博主粗提取的股票观点（由另一个 LLM 初步提取）
```json
{tickers_json}
```

### 对应的财经简报摘要（供上下文参考）
{brief_excerpt}

## 你的任务

对每个 ticker 的每个 price_level，推断并标注以下精确字段：

1. **prediction_type**: 观点类型（严格枚举）
   - `target_price`: 博主明确给出的目标价
   - `entry_zone`: 建议建仓/加仓的价位
   - `support`: 支撑位判断
   - `resistance`: 阻力位/压力位判断
   - `direction_call`: 纯方向判断（无具体价位时）
   - `reference_only`: 仅作参考，不构成可验证预测（如"当前股价"、"前期高点"）

2. **direction**: 观点方向（long / short / hold）
3. **target_price**: 目标价（如有，否则 null）
4. **stop_loss**: 止损价（如有，否则 null）
5. **confidence**: 语气强度（high / medium / low）
6. **horizon**: 时间窗口（short_term / medium_term / long_term）
7. **context**: 用一句话总结这条观点的核心含义

## 输出格式

输出一个 JSON 数组，每个元素对应一个 ticker：
```json
[
  {{
    "ticker": "NVDA",
    "company_name": "英伟达",
    "analyst": "老李",
    "sentiment": "bullish",
    "opinions": [
      {{
        "prediction_type": "entry_zone",
        "direction": "long",
        "price": 176.0,
        "target_price": 200.0,
        "stop_loss": 164.0,
        "confidence": "medium",
        "horizon": "medium_term",
        "context": "建议开仓位"
      }}
    ]
  }}
]
```

## 重要规则
- **仅提取可验证的投资观点**，排除"当前股价"、"前期高点"等纯陈述性描述（标记为 `reference_only`）
- confidence 和 horizon 如果从文本无法确定，分别默认 `medium` 和 `medium_term`
- 必须输出合法 JSON，不要添加注释或 markdown 标记

请直接输出 JSON 数组。"""


def _call_cerebras(prompt: str) -> tuple[str | None, str | None]:
    """调用 Cerebras API。"""
    try:
        from openai import OpenAI
    except ImportError:
        logging.error("openai 包未安装，请执行: pip install openai")
        return None, None

    client = OpenAI(api_key=CEREBRAS_API_KEY, base_url=CEREBRAS_BASE_URL)

    candidates = []
    for model in [CEREBRAS_MODEL, *CEREBRAS_FALLBACK_MODELS]:
        if model and model not in candidates and model not in _UNAVAILABLE_MODELS:
            candidates.append(model)
    if not candidates:
        logging.error(
            "无可用 Cerebras 模型：主模型及回退模型均已标记不可用。"
        )
        return None, None

    last_error = None
    for model in candidates:
        for attempt in range(OPINION_MAX_RETRIES + 1):
            try:
                _throttle_request_rate()
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=4096,
                )
                text = response.choices[0].message.content or ""
                usage = response.usage
                if usage:
                    cost = usage.prompt_tokens * 0.25 / 1e6 + usage.completion_tokens * 0.69 / 1e6
                    logging.info(
                        f"Cerebras 调用完成(model={model}): in={usage.prompt_tokens} "
                        f"out={usage.completion_tokens} cost=${cost:.4f}"
                    )
                return text, model
            except Exception as e:
                last_error = e
                msg = str(e)

                if _is_model_not_found_error(msg):
                    _UNAVAILABLE_MODELS.add(model)
                    logging.warning(f"Cerebras 模型不可用，已停用本轮后续请求: {model}")
                    break

                if _is_rate_limited_error(msg) and attempt < OPINION_MAX_RETRIES:
                    wait_s = OPINION_RETRY_BACKOFF_SECONDS * (2 ** attempt)
                    logging.warning(
                        f"Cerebras 命中限流(model={model})，{wait_s:.1f}s 后重试 "
                        f"({attempt + 1}/{OPINION_MAX_RETRIES})"
                    )
                    time.sleep(wait_s)
                    continue

                logging.error(f"Cerebras API 调用失败(model={model}): {e}")
                return None, None

    logging.error(f"Cerebras API 调用失败: {last_error}")
    return None, None


def _parse_json_response(text: str) -> list | None:
    """从 LLM 响应中容错提取 JSON 数组。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```" in text:
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _to_optional_float(value) -> float | None:
    """尽量把输入转为 float，失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _normalize_enum(value, allowed: set[str], default: str) -> str:
    """标准化枚举值（大小写不敏感）。"""
    if value is None:
        return default
    normalized = str(value).strip().lower()
    return normalized if normalized in allowed else default


def _normalize_direction(direction, sentiment) -> str:
    """标准化方向字段；缺失时根据 sentiment 推断。"""
    direct = _normalize_enum(direction, DIRECTIONS, "")
    if direct:
        return direct
    sent = str(sentiment or "").strip().lower()
    if sent == "bullish":
        return "long"
    if sent == "bearish":
        return "short"
    return "hold"


def _normalize_prediction_type(level_type, has_price: bool) -> str:
    """把价格层级 type 映射到 Prediction.type。"""
    normalized = str(level_type or "").strip().lower()
    alias = {
        "target": "target_price",
        "tp": "target_price",
        "entry": "entry_zone",
        "buy_zone": "entry_zone",
        "pressure": "resistance",
        "stop": "stop_loss",
        "stoploss": "stop_loss",
    }
    mapped = alias.get(normalized, normalized)
    if mapped in PREDICTION_TYPES:
        return mapped
    return "reference_only" if has_price else "direction_call"


def _has_direct_prediction_fields(mentioned_tickers: list) -> bool:
    """
    判断 mentioned_tickers 是否已具备直出预测字段：
    direction/confidence/horizon/conviction。
    """
    required = {"direction", "confidence", "horizon", "conviction"}
    if not isinstance(mentioned_tickers, list) or not mentioned_tickers:
        return False
    dict_items = [x for x in mentioned_tickers if isinstance(x, dict)]
    if not dict_items:
        return False
    return all(required.issubset(set(item.keys())) for item in dict_items)


def _extract_opinions_direct(
    mentioned_tickers: list,
    video_id: str,
    channel: str,
    pub_date: str,
) -> list[Opinion]:
    """把 analyzer 直出的 mentioned_tickers 直接映射为 Opinion。"""
    opinions: list[Opinion] = []

    for ticker_item in mentioned_tickers:
        if not isinstance(ticker_item, dict):
            continue

        company = str(ticker_item.get("company_name", "") or "").strip()
        raw_ticker = str(ticker_item.get("ticker", "") or "")
        ticker = normalize_ticker_symbol(raw_ticker, company)
        if not ticker:
            logging.warning(
                f"[{video_id}] 跳过无法识别 ticker: raw='{raw_ticker}' company='{company}'"
            )
            continue
        if ticker != str(raw_ticker).strip().upper():
            logging.info(
                f"[{video_id}] ticker 映射: {raw_ticker} -> {ticker}"
            )
        analyst = ticker_item.get("analyst", "")
        sentiment = str(ticker_item.get("sentiment", "neutral")).strip().lower() or "neutral"

        direction = _normalize_direction(ticker_item.get("direction"), sentiment)
        confidence = _normalize_enum(ticker_item.get("confidence"), CONFIDENCES, "medium")
        horizon = _normalize_enum(ticker_item.get("horizon"), HORIZONS, "medium_term")
        conviction = _normalize_enum(ticker_item.get("conviction"), CONVICTIONS, "medium")

        levels = ticker_item.get("price_levels")
        if not isinstance(levels, list):
            levels = []

        generated = 0
        for lv in levels:
            if not isinstance(lv, dict):
                continue
            price = _to_optional_float(lv.get("level", lv.get("price")))
            pred_type = _normalize_prediction_type(lv.get("type"), has_price=price is not None)
            target_price = _to_optional_float(lv.get("target_price"))
            stop_loss = _to_optional_float(lv.get("stop_loss"))
            if pred_type == "target_price" and target_price is None:
                target_price = price

            oid = make_opinion_id(video_id, ticker, pred_type, price)
            prediction = Prediction(
                type=pred_type,
                direction=direction,
                price=price,
                target_price=target_price,
                stop_loss=stop_loss,
                confidence=confidence,
                conviction=conviction,
                horizon=horizon,
                context=lv.get("context", ""),
            )
            opinions.append(
                Opinion(
                    opinion_id=oid,
                    video_id=video_id,
                    channel=channel,
                    analyst=analyst,
                    published_date=pub_date,
                    ticker=ticker,
                    company_name=company,
                    sentiment=sentiment,
                    prediction=prediction,
                    price_at_publish=None,
                    extraction_source="qwen_direct",
                )
            )
            generated += 1

        if generated == 0:
            pred_type = "direction_call"
            oid = make_opinion_id(video_id, ticker, pred_type, None)
            prediction = Prediction(
                type=pred_type,
                direction=direction,
                price=None,
                target_price=None,
                stop_loss=None,
                confidence=confidence,
                conviction=conviction,
                horizon=horizon,
                context=ticker_item.get("context", ""),
            )
            opinions.append(
                Opinion(
                    opinion_id=oid,
                    video_id=video_id,
                    channel=channel,
                    analyst=analyst,
                    published_date=pub_date,
                    ticker=ticker,
                    company_name=company,
                    sentiment=sentiment,
                    prediction=prediction,
                    price_at_publish=None,
                    extraction_source="qwen_direct",
                )
            )

    return opinions


def extract_opinions_from_result(
    result_json_path: Path,
    refresh_cache: bool = False,
) -> tuple[list[Opinion], dict]:
    """
    从单个分析结果 JSON 提取 opinions。
    流程：读 mentioned_tickers → 调 Cerebras 精标注 → 生成 Opinion 列表。
    """
    data = json.loads(result_json_path.read_text(encoding="utf-8"))
    metadata = data.get("metadata", {})
    mentioned = data.get("mentioned_tickers", [])
    brief_text = data.get("brief_text", "")

    video_id = metadata.get("video_id", result_json_path.stem)
    channel = metadata.get("channel", "unknown")
    pub_date = metadata.get("date", "")
    run_meta = {
        "video_id": video_id,
        "mode": "none",
        "cache_hit": False,
        "cerebras_called": False,
        "model_used": None,
    }

    if not mentioned:
        logging.info(f"[{video_id}] 无 mentioned_tickers，跳过")
        return [], run_meta

    if _has_direct_prediction_fields(mentioned):
        opinions = _extract_opinions_direct(
            mentioned_tickers=mentioned,
            video_id=video_id,
            channel=channel,
            pub_date=pub_date,
        )
        run_meta["mode"] = "direct"
        logging.info(f"[{video_id}] 直出映射 {len(opinions)} 条 opinions（跳过 Cerebras）")
        return opinions, run_meta

    if not CEREBRAS_API_KEY:
        logging.error("未设置 CEREBRAS_API_KEY，无法做精标注")
        return [], run_meta

    prompt = _build_refinement_prompt(mentioned, brief_text, pub_date)
    p_hash = _prompt_hash(prompt)
    refined = None
    if not refresh_cache:
        refined = _load_cerebras_cache(
            result_json_path=result_json_path,
            video_id=video_id,
            requested_model=CEREBRAS_MODEL,
            prompt_hash=p_hash,
        )
        if refined is not None:
            run_meta["mode"] = "cerebras"
            run_meta["cache_hit"] = True
            run_meta["model_used"] = "cache"
            logging.info(f"[{video_id}] 命中 Cerebras 缓存，跳过 API 调用")

    if refined is None:
        run_meta["cerebras_called"] = True
        raw_response, model_used = _call_cerebras(prompt)
        run_meta["model_used"] = model_used
        if not raw_response:
            return [], run_meta
        refined = _parse_json_response(raw_response)
        if not refined:
            logging.error(f"[{video_id}] Cerebras 响应 JSON 解析失败")
            return [], run_meta
        run_meta["mode"] = "cerebras"
        _save_cerebras_cache(
            result_json_path=result_json_path,
            video_id=video_id,
            requested_model=CEREBRAS_MODEL,
            used_model=model_used or CEREBRAS_MODEL,
            prompt_hash=p_hash,
            refined=refined,
        )

    # 将 Cerebras 精标注转换为 Opinion 对象
    opinions: list[Opinion] = []
    for ticker_item in refined:
        company = str(ticker_item.get("company_name", "") or "").strip()
        raw_ticker = str(ticker_item.get("ticker", "") or "")
        ticker = normalize_ticker_symbol(raw_ticker, company)
        if not ticker:
            logging.warning(
                f"[{video_id}] 跳过无法识别 ticker: raw='{raw_ticker}' company='{company}'"
            )
            continue
        if ticker != str(raw_ticker).strip().upper():
            logging.info(
                f"[{video_id}] ticker 映射: {raw_ticker} -> {ticker}"
            )
        analyst = ticker_item.get("analyst", "")
        sentiment = ticker_item.get("sentiment", "neutral")

        for op in ticker_item.get("opinions", []):
            pred_type = op.get("prediction_type", "direction_call")
            price = op.get("price")

            oid = make_opinion_id(video_id, ticker, pred_type, price)
            prediction = Prediction(
                type=pred_type,
                direction=op.get("direction", "long"),
                price=price,
                target_price=op.get("target_price"),
                stop_loss=op.get("stop_loss"),
                confidence=op.get("confidence", "medium"),
                conviction=op.get("conviction", "medium"),
                horizon=op.get("horizon", "medium_term"),
                context=op.get("context", ""),
            )

            opinion = Opinion(
                opinion_id=oid,
                video_id=video_id,
                channel=channel,
                analyst=analyst,
                published_date=pub_date,
                ticker=ticker,
                company_name=company,
                sentiment=sentiment,
                prediction=prediction,
                price_at_publish=None,  # 后续由 verifier/market_data 填充
                extraction_source="cerebras_refinement",
            )
            opinions.append(opinion)

    logging.info(f"[{video_id}] 提取 {len(opinions)} 条 opinions ({len(refined)} tickers)")
    return opinions, run_meta


def discover_result_jsons(results_dir: Path | None = None) -> list[Path]:
    """发现 data/results 下所有主分析 JSON（排除 _price_levels / _cerebras_opinions）。"""
    root = results_dir or RESULTS_DIR
    found = []
    for p in root.rglob("*.json"):
        name = p.name
        if name.endswith("_price_levels.json") or name.endswith("_cerebras_opinions.json"):
            continue
        # 主 JSON 文件名通常是 video_id.json（11 字符左右）
        if len(p.stem) >= 8 and len(p.stem) <= 16:
            found.append(p)
    return sorted(found)


def _bootstrap_state_from_existing_opinions(
    jsons: list[Path],
    state_items: dict[str, dict],
) -> int:
    from .opinion_store import load_opinions

    existed = load_opinions()
    if not existed:
        return 0
    existing_video_ids = {o.video_id for o in existed if o.video_id}
    if not existing_video_ids:
        return 0

    seeded = 0
    for jp in jsons:
        vid = jp.stem
        if vid in state_items or vid not in existing_video_ids:
            continue
        fp = _file_fingerprint(jp)
        state_items[vid] = {
            "status": "done",
            "source_path": str(jp),
            "source_size": fp["source_size"],
            "source_mtime_ns": fp["source_mtime_ns"],
            "updated_at": _utc_now_iso(),
            "mode": "bootstrap_existing_opinions",
        }
        seeded += 1
    return seeded


def backfill_all_opinions(
    results_dir: Path | None = None,
    refresh: bool = False,
    refresh_cache: bool = False,
    retry_failed_only: bool = False,
    since_date: str | None = None,
    dry_run: bool = False,
) -> dict:
    """
    对所有已有分析结果做一次性回填。
    返回 {"files_processed", "total_opinions", "errors"} 统计。
    """
    from .opinion_store import upsert_opinions

    jsons = discover_result_jsons(results_dir)
    since_dt = _parse_date(since_date) if since_date else None
    if since_date and since_dt is None:
        raise ValueError(f"--since 日期格式无效: {since_date}（支持 YYYY-MM-DD 或 YYYYMMDD）")

    state_items = _load_extract_state()
    if not refresh and not state_items and not retry_failed_only:
        seeded = _bootstrap_state_from_existing_opinions(jsons, state_items)
        if seeded > 0 and not dry_run:
            _save_extract_state(state_items)
            logging.info(f"提取状态初始化: 从 opinions.json 引导 {seeded} 条已完成记录")
        elif seeded > 0 and dry_run:
            logging.info(f"[dry-run] 可初始化提取状态: {seeded} 条已完成记录")

    logging.info(
        f"发现 {len(jsons)} 个分析结果文件 | refresh={refresh} "
        f"refresh_cache={refresh_cache} retry_failed_only={retry_failed_only} "
        f"since={since_dt.isoformat() if since_dt else 'none'} dry_run={dry_run}"
    )

    stats = {
        "files_processed": 0,
        "total_opinions": 0,
        "errors": 0,
        "skipped_done": 0,
        "skipped_since": 0,
        "skipped_not_failed": 0,
        "cerebras_calls": 0,
        "cache_hits": 0,
        "direct_mapped": 0,
        "would_cerebras_calls": 0,
        "would_direct_mapped": 0,
        "would_no_tickers": 0,
        "dry_run": dry_run,
    }

    for jp in jsons:
        video_id = jp.stem
        fp = _file_fingerprint(jp)
        state_item = state_items.get(video_id, {})

        if since_dt:
            video_dt = _infer_video_date_from_path(jp)
            if video_dt and video_dt < since_dt:
                stats["skipped_since"] += 1
                continue

        if retry_failed_only:
            if state_item.get("status") != "failed":
                stats["skipped_not_failed"] += 1
                continue
        elif (not refresh) and state_item.get("status") == "done" and _state_matches_file(state_item, fp):
            stats["skipped_done"] += 1
            continue

        if dry_run:
            mode = _inspect_result_mode(jp)
            if mode == "cerebras":
                stats["would_cerebras_calls"] += 1
            elif mode == "direct":
                stats["would_direct_mapped"] += 1
            elif mode == "no_tickers":
                stats["would_no_tickers"] += 1
            else:
                stats["errors"] += 1
            stats["files_processed"] += 1
            continue

        try:
            opinions, run_meta = extract_opinions_from_result(jp, refresh_cache=refresh_cache)
            if run_meta.get("cache_hit"):
                stats["cache_hits"] += 1
            if run_meta.get("cerebras_called"):
                stats["cerebras_calls"] += 1
            if run_meta.get("mode") == "direct":
                stats["direct_mapped"] += 1

            if opinions:
                result = upsert_opinions(opinions)
                stats["total_opinions"] += result["added"]
                logging.info(
                    f"  [{jp.stem}] +{result['added']} new, "
                    f"{result['skipped']} dup, total={result['total']}"
                )
            stats["files_processed"] += 1
            state_items[video_id] = {
                "status": "done",
                "source_path": str(jp),
                "source_size": fp["source_size"],
                "source_mtime_ns": fp["source_mtime_ns"],
                "updated_at": _utc_now_iso(),
                "mode": run_meta.get("mode", "none"),
                "cache_hit": bool(run_meta.get("cache_hit", False)),
                "model_used": run_meta.get("model_used"),
            }
        except Exception as e:
            logging.error(f"  [{jp.name}] 处理失败: {e}")
            stats["errors"] += 1
            state_items[video_id] = {
                "status": "failed",
                "source_path": str(jp),
                "source_size": fp["source_size"],
                "source_mtime_ns": fp["source_mtime_ns"],
                "updated_at": _utc_now_iso(),
                "error": str(e),
            }
        _save_extract_state(state_items)
        if OPINION_BACKFILL_FILE_SLEEP_SECONDS > 0:
            time.sleep(OPINION_BACKFILL_FILE_SLEEP_SECONDS)

    return stats
