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
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .models import Opinion, Prediction, Verification, make_opinion_id

# ─── Cerebras 配置 ───
CEREBRAS_API_KEY  = os.getenv("CEREBRAS_API_KEY", "").strip()
CEREBRAS_BASE_URL = os.getenv("OPINION_BASE_URL", "https://api.cerebras.ai/v1").strip()
CEREBRAS_MODEL    = os.getenv("OPINION_MODEL_NAME", "gpt-oss-120b").strip()

# 项目根目录
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
RESULTS_DIR  = _PROJECT_DIR / "data" / "results"


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


def _call_cerebras(prompt: str) -> str | None:
    """调用 Cerebras API。"""
    try:
        from openai import OpenAI
    except ImportError:
        logging.error("openai 包未安装，请执行: pip install openai")
        return None

    client = OpenAI(api_key=CEREBRAS_API_KEY, base_url=CEREBRAS_BASE_URL)

    try:
        response = client.chat.completions.create(
            model=CEREBRAS_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        if usage:
            cost = usage.prompt_tokens * 0.25 / 1e6 + usage.completion_tokens * 0.69 / 1e6
            logging.info(
                f"Cerebras 调用完成: in={usage.prompt_tokens} out={usage.completion_tokens} "
                f"cost=${cost:.4f}"
            )
        return text
    except Exception as e:
        logging.error(f"Cerebras API 调用失败: {e}")
        return None


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


def extract_opinions_from_result(result_json_path: Path) -> list[Opinion]:
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

    if not mentioned:
        logging.info(f"[{video_id}] 无 mentioned_tickers，跳过")
        return []

    if not CEREBRAS_API_KEY:
        logging.error("未设置 CEREBRAS_API_KEY，无法做精标注")
        return []

    prompt = _build_refinement_prompt(mentioned, brief_text, pub_date)
    raw_response = _call_cerebras(prompt)
    if not raw_response:
        return []

    refined = _parse_json_response(raw_response)
    if not refined:
        logging.error(f"[{video_id}] Cerebras 响应 JSON 解析失败")
        return []

    # 将 Cerebras 精标注转换为 Opinion 对象
    opinions: list[Opinion] = []
    for ticker_item in refined:
        ticker = ticker_item.get("ticker", "")
        company = ticker_item.get("company_name", "")
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
    return opinions


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


def backfill_all_opinions(results_dir: Path | None = None) -> dict:
    """
    对所有已有分析结果做一次性回填。
    返回 {"files_processed", "total_opinions", "errors"} 统计。
    """
    from .opinion_store import upsert_opinions

    jsons = discover_result_jsons(results_dir)
    logging.info(f"发现 {len(jsons)} 个分析结果文件")

    stats = {"files_processed": 0, "total_opinions": 0, "errors": 0}

    for jp in jsons:
        try:
            opinions = extract_opinions_from_result(jp)
            if opinions:
                result = upsert_opinions(opinions)
                stats["total_opinions"] += result["added"]
                logging.info(
                    f"  [{jp.stem}] +{result['added']} new, "
                    f"{result['skipped']} dup, total={result['total']}"
                )
            stats["files_processed"] += 1
        except Exception as e:
            logging.error(f"  [{jp.name}] 处理失败: {e}")
            stats["errors"] += 1
        # 避免 API rate limit
        time.sleep(0.5)

    return stats
