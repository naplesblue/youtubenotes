#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cerebras GPT-OSS-120B 精标注 POC

用途：读取现有 mentioned_tickers 数据 + brief_text 摘要，
     调用 Cerebras API（OpenAI 兼容）做结构化观点精标注。
     测试 GPT-OSS-120B 在指令遵循 + JSON schema 输出上的质量。

用法：
  export CEREBRAS_API_KEY=your_key_here
  python tools/cerebras_poc.py data/results/老李玩钱/20260227/e6pJrGpMNfU.json
"""

import os
import sys
import json
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── 配置 ───
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "").strip()
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
CEREBRAS_MODEL = "gpt-oss-120b"

OPINION_SCHEMA_EXAMPLE = """\
{
  "ticker": "NVDA",
  "company_name": "英伟达",
  "analyst": "老李",
  "sentiment": "bullish",
  "opinions": [
    {
      "prediction_type": "entry_zone",
      "direction": "long",
      "price": 176.0,
      "target_price": 200.0,
      "stop_loss": 164.0,
      "confidence": "medium",
      "horizon": "medium_term",
      "context": "建议开仓位，关键支撑。目标价200，止损164"
    }
  ]
}
"""


def build_prompt(mentioned_tickers: list, brief_text: str, video_date: str) -> str:
    tickers_json = json.dumps(mentioned_tickers, ensure_ascii=False, indent=2)

    # Truncate brief_text to ~2000 chars to stay within reasonable input size
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

2. **direction**: 观点方向
   - `long`: 看多 / 建议买入
   - `short`: 看空 / 建议卖出
   - `hold`: 观望 / 持有不动

3. **target_price**: 目标价（如有，否则 null）
4. **stop_loss**: 止损价（如有，否则 null）

5. **confidence**: 语气强度
   - `high`: "一定会"、"强烈看好"、"必须关注"
   - `medium`: "可以考虑"、"个人建议"、"觉得不错"
   - `low`: "不确定"、"如果发生"、"观察中"

6. **horizon**: 时间窗口
   - `short_term`: < 1 个月（"短线"、"本周"、"几天"）
   - `medium_term`: 1–6 个月（"中期"、"今年"、默认值）
   - `long_term`: > 6 个月（"长期"、"未来几年"）

7. **context**: 用一句话总结这条观点的核心含义

## 输出格式

输出一个 JSON 数组，每个元素对应一个 ticker。格式示例：
```json
[
{OPINION_SCHEMA_EXAMPLE}
]
```

## 重要规则
- **仅提取可验证的投资观点**，排除"当前股价"、"前期高点"等纯陈述性描述（标记为 `reference_only` 并单独列出即可）
- 如果某个 price_level 的 context 不足以判断具体字段，请根据 sentiment + 上下文合理推断
- confidence 和 horizon 如果从文本无法确定，分别默认为 `medium` 和 `medium_term`
- 必须输出合法 JSON，不要添加注释或 markdown 标记

请直接输出 JSON 数组，不要添加任何解释文字。"""


def call_cerebras(prompt: str) -> str:
    """调用 Cerebras API（OpenAI 兼容）"""
    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: openai 包未安装，请执行: pip install openai")
        sys.exit(1)

    client = OpenAI(
        api_key=CEREBRAS_API_KEY,
        base_url=CEREBRAS_BASE_URL,
    )

    print(f"📡 正在调用 Cerebras {CEREBRAS_MODEL}...")
    start = time.time()

    response = client.chat.completions.create(
        model=CEREBRAS_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=4096,
    )

    elapsed = time.time() - start
    result = response.choices[0].message.content or ""
    usage = response.usage

    print(f"✅ 响应完成 ({elapsed:.1f}s)")
    if usage:
        print(f"   tokens: input={usage.prompt_tokens}, output={usage.completion_tokens}")
        cost = usage.prompt_tokens * 0.25 / 1_000_000 + usage.completion_tokens * 0.69 / 1_000_000
        print(f"   预估费用: ${cost:.4f}")

    return result


def extract_json_from_response(text: str) -> list | None:
    """从响应中提取 JSON 数组"""
    text = text.strip()
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试去掉 markdown 代码块
    if "```" in text:
        import re
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    # 尝试找 [ ... ]
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass
    return None


def main():
    if not CEREBRAS_API_KEY:
        print("ERROR: 请设置环境变量 CEREBRAS_API_KEY")
        sys.exit(1)

    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <分析结果 JSON 文件路径>")
        print(f"示例: {sys.argv[0]} data/results/老李玩钱/20260227/e6pJrGpMNfU.json")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"ERROR: 文件不存在: {json_path}")
        sys.exit(1)

    # 加载数据
    data = json.loads(json_path.read_text(encoding="utf-8"))
    mentioned_tickers = data.get("mentioned_tickers", [])
    brief_text = data.get("brief_text", "")
    metadata = data.get("metadata", {})
    video_date = metadata.get("date", "unknown")

    if not mentioned_tickers:
        print("WARNING: mentioned_tickers 为空，无法进行精标注")
        sys.exit(0)

    print(f"📋 输入: {json_path.name}")
    print(f"   频道: {metadata.get('channel', '?')}")
    print(f"   日期: {video_date}")
    print(f"   Tickers: {len(mentioned_tickers)} 个")
    total_levels = sum(len(t.get("price_levels", [])) for t in mentioned_tickers)
    print(f"   Price levels: {total_levels} 条")
    print()

    # 构建 prompt 并调用
    prompt = build_prompt(mentioned_tickers, brief_text, video_date)
    response_text = call_cerebras(prompt)

    # 解析结果
    opinions = extract_json_from_response(response_text)
    if opinions is None:
        print("\n❌ 无法解析 JSON，原始响应：")
        print(response_text[:2000])
        sys.exit(1)

    # 输出格式化结果
    print(f"\n{'='*60}")
    print(f"📊 精标注结果: {len(opinions)} 个 ticker")
    print(f"{'='*60}\n")

    total_opinions = 0
    for item in opinions:
        ticker = item.get("ticker", "?")
        analyst = item.get("analyst", "?")
        sentiment = item.get("sentiment", "?")
        ops = item.get("opinions", [])
        total_opinions += len(ops)

        emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(sentiment, "❓")
        print(f"{emoji} {ticker} ({item.get('company_name','')}) — {sentiment} by {analyst}")

        for op in ops:
            ptype = op.get("prediction_type", "?")
            direction = op.get("direction", "?")
            price = op.get("price")
            target = op.get("target_price")
            stop = op.get("stop_loss")
            conf = op.get("confidence", "?")
            horizon = op.get("horizon", "?")
            ctx = op.get("context", "")

            parts = [f"  ├─ {ptype}/{direction}"]
            if price: parts.append(f"price=${price}")
            if target: parts.append(f"target=${target}")
            if stop: parts.append(f"stop=${stop}")
            parts.append(f"[{conf}/{horizon}]")
            print(" ".join(parts))
            if ctx:
                print(f"  │  └─ {ctx}")

        print()

    print(f"总计: {total_opinions} 条可验证观点")

    # 保存原始 JSON 结果
    output_path = json_path.parent / f"{json_path.stem}_cerebras_opinions.json"
    output_path.write_text(
        json.dumps(opinions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n💾 结果已保存: {output_path}")


if __name__ == "__main__":
    main()
