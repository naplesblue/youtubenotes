import os
import time
import logging

from .config import TEXT_MODEL_NAME, QWEN_BASE_URL

def process_text_tasks(raw_transcript_with_timestamps, api_key=None):
    """
    基于带时间戳的原始转录，调用 Qwen-Plus 完成：
      1. 财经简报精炼
      2. 关键信息摘要（含时间戳）
      3. 原子化点位数据 JSON
      4. 提及股票数据 JSON

    api_key 参数保留以兼容主流程调用，实际从环境变量 DASHSCOPE_API_KEY 读取。
    """
    logging.info(f"正在使用 {TEXT_MODEL_NAME} 进行文本摘要...")

    if not raw_transcript_with_timestamps:
        logging.error("输入转录为空，无法进行文本处理。")
        return None

    dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
    if not dashscope_api_key:
        logging.error(
            "环境变量 DASHSCOPE_API_KEY 未设置。"
            "请在 .env 文件中配置阿里云 DashScope API Key。"
        )
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logging.error(
            "openai 包未安装。请执行: pip install openai --break-system-packages"
        )
        return None

    client = OpenAI(
        api_key=dashscope_api_key,
        base_url=QWEN_BASE_URL,
    )

    prompt = f"""**输入:**
您将收到一份带有时间戳的原始音频转录。接下来，您将扮演一名资深财经分析师，需要根据用户提供的财经音频转录文本，完成分析

**任务:**
请*仅*基于提供的【带时间戳的原始转录】，完成以下三项任务：
1.  文本整理与精炼 (生成财经简报的主要内容):
    * 使用简体中文生成转录的精炼版本。
    * 将口语化的表达（例如：嗯、啊、填充词、重复、不完整的句子）转换为流畅、简洁、专业的书面语言。
    * 在此精炼文本中，请按照财经简报的格式和风格，组织和呈现以下内容：
        * 标题：根据音频内容提炼一个概括性的标题，包含主题和日期（例如：美股市场情绪改善与个股分析 (YYYY年MM月DD日)）。
        * 主要主题和重要观点：总结本次音频分享的核心内容、主要议题和关键结论。
        * 市场整体情绪与走势：描述当前的股市情绪和整体走势，分析影响市场的宏观因素（例如：贸易谈判、经济数据、政策等），提及重要的市场指数及其变化（例如：VIX指数、大盘点位），并对当前行情是"反弹"还是"反转"进行分析和判断，说明判断依据。
        * 经济数据分析：列出音频中提及的所有重要经济数据（例如：耐用品订单、失业救济申请、成屋销售等），详细说明各项数据的具体数值、与预期值的比较以及对市场的潜在影响，并提供对数据的解读和分析。
        * 重要个股分析：逐一分析音频中提及的每一支具体股票（例如：GOOG, NOW, NVDA, AMD, AMZN, AAPL, META, MSFT等），提取并总结财报表现、主要业务部门表现、公司财务状况、估值情况和发言人的观点、技术面分析（关键支撑/压力位、趋势判断、止跌或突破信号等），以及发言人的持仓情况和操作计划（加仓、减仓、滚动操作等）及其理由。
        * 投资策略与反思：总结音频中分享的投资理念、策略和对当前市场环境的反思。
        * 总结：用简洁的语言概括要点。
    * 在此精炼文本部分不应包含任何时间戳。
    * 在保留原始含义的同时，提高可读性和正式性。
    * 请确保精炼后的文本内容可以直接作为财经简报使用。
2.  关键信息提取与摘要 (含时间戳):
    * 分析【带时间戳的原始转录】，识别并提取与以下内容相关的特定数字或关键信息：
        * 股市指数水平（例如，"标普"，"SPX"，"纳斯达克"）及其点位值。
        * 具体股票、债券、货币或商品的价格、目标价格或重要价格水平。
        * 重要的经济数据发布及其数值。
        * 公司财报的关键指标。
        * 其他明确提到的、具有显著意义的量化指标或关键判断。
    * **排除**不相关的数字（日期、普通时间、关税百分比、持续时间等）。
    * 对于每个提取的关键信息，从原始转录中找到最近的前一个时间戳 `[HH:MM:SS]`，严格按原格式复制。
    * 将所有提取的带时间戳的关键信息整理成清晰列表。如果未找到符合条件的信息，请明确说明。
3.  结构化股票数据提取 (JSON 格式):
    * 提取音频中所有明确提及的股票代码（如 NVDA, AMD, AAPL, GOOG 等）。
    * 对于每只股票，提取分析师对其的情绪（bullish/bearish/neutral）、公司名称、关键价格水平。
    * 如果有提及人物姓名（分析师、CEO、嘉宾等），也请提取。

**输出格式:**
请严格按照以下四个部分组织您的回答：
【精炼文本】
（在此处放置精炼后的、不含时间戳的、已组织成财经简报格式的文本）
【关键信息摘要（含时间戳）】
（每条以 '- [HH:MM:SS]' 开头）
【原子化点位数据 (JSON)】
```json
[
  {{"ticker": "NVDA", "price": 850.00, "type": "support", "context": "关键支撑位", "timestamp": "[00:15:30]"}},
  {{"ticker": "AMD", "price": 150.00, "type": "resistance", "context": "阻力位", "timestamp": "[00:20:45]"}}
]
```
【提及股票数据 (JSON)】
```json
[
  {{
    "ticker": "NVDA",
    "company_name": "英伟达",
    "sentiment": "bullish",
    "analyst": "Rhino",
    "price_levels": [
      {{"level": 850.00, "type": "support", "context": "关键支撑"}},
      {{"level": 950.00, "type": "target", "context": "目标价"}}
    ]
  }}
]
```

**【带时间戳的原始转录】:**
---
{raw_transcript_with_timestamps}
---
请确保精确遵循所有指令，特别是在摘要部分的输出格式和时间戳处理上。
"""

    logging.info(f"正在调用 {TEXT_MODEL_NAME} API...")
    MAX_RETRIES = 3
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=TEXT_MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=8192,
                timeout=600,
            )
            result_text = response.choices[0].message.content
            if result_text and result_text.strip():
                logging.info(f"成功从 {TEXT_MODEL_NAME} 收到处理后的文本。")
                logging.debug(f"输出前 500 字:\n{result_text[:500]}")
                return result_text
            else:
                logging.error(f"{TEXT_MODEL_NAME} 返回空内容（尝试 {attempt+1}/{MAX_RETRIES}）。")

        except Exception as e:
            err_str = str(e)
            logging.error(f"{TEXT_MODEL_NAME} 调用失败（尝试 {attempt+1}/{MAX_RETRIES}）: {err_str}")
            if any(kw in err_str.lower() for kw in ("rate", "429", "503", "timeout")):
                wait = (attempt + 1) * 15
                logging.info(f"等待 {wait} 秒后重试...")
                time.sleep(wait)
            else:
                logging.error("非临时性错误，停止重试。")
                break

    logging.error(f"已达最大重试次数，{TEXT_MODEL_NAME} 文本处理失败。")
    return None
