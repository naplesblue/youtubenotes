"""
lib/core/parser.py

解析 Python 分析层（audio_analyzer.py）生成的 JSON 数据。
对应 JS 版 Parser.js。
"""

import json
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from ytbnotes.sync.path_resolver import PathResolver


class SchemaError(Exception):
    pass


class Parser:
    def __init__(self, options: dict | None = None):
        opts = options or {}
        self.path_resolver: PathResolver = opts["pathResolver"]
        self.json_pattern: str = opts.get("jsonPattern", "**/*.json")
        self.exclude_patterns: list[str] = opts.get(
            "excludePatterns", ["**/*_price_levels.json", "**/*_levels.json", "**/*_opinions.json"]
        )

    # ── 文件发现 ──────────────────────────────────────────────────────────────

    def discover_json_files(self) -> list[Path]:
        """
        发现分析输出目录下所有待处理的 JSON 文件。
        排除点位数据文件（*_price_levels.json / *_levels.json）和缓存目录。
        """
        analysis_dir = self.path_resolver.get_analysis_output()
        if not analysis_dir.exists():
            return []

        all_files = list(analysis_dir.glob(self.json_pattern))

        def is_excluded(p: Path) -> bool:
            name = p.name
            rel_str = p.relative_to(analysis_dir).as_posix()

            for pattern in self.exclude_patterns:
                if fnmatch(rel_str, pattern):
                    return True

            # 排除点位数据文件和观点数据文件
            if name.endswith("_price_levels.json") or name.endswith("_levels.json") or name.endswith("_opinions.json"):
                return True
            # 排除缓存目录
            if ".cache" in p.parts:
                return True
            return False

        return [f for f in all_files if not is_excluded(f)]

    # ── 解析单文件 ────────────────────────────────────────────────────────────

    def parse_file(self, json_path: Path | str) -> tuple[str, dict]:
        """
        解析单个 JSON 文件，返回 (video_id, data)。
        video_id 取文件名（不含扩展名）。
        """
        json_path = Path(json_path)
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        video_id = json_path.stem
        self.validate_schema(data, video_id)
        return video_id, data

    def parse_files(self, file_paths: list[Path]) -> list[tuple[str, dict]]:
        results = []
        for fp in file_paths:
            try:
                results.append(self.parse_file(fp))
            except Exception as exc:
                print(f"  ❌ 解析失败 {fp}: {exc}")
        return results

    # ── Schema 验证 ───────────────────────────────────────────────────────────

    def validate_schema(self, data: dict, video_id: str) -> None:
        errors: list[str] = []

        if not data.get("metadata"):
            errors.append("缺少 metadata 字段")
        elif not data["metadata"].get("title"):
            errors.append("metadata.title 为空")

        for idx, ticker in enumerate(data.get("mentioned_tickers") or []):
            if isinstance(ticker, str):
                if not ticker.strip():
                    errors.append(f"mentioned_tickers[{idx}] ticker 为空字符串")
                continue
            if not isinstance(ticker, dict):
                errors.append(f"mentioned_tickers[{idx}] 不是对象或字符串")
                continue
            if not ticker.get("ticker"):
                errors.append(f"mentioned_tickers[{idx}] 缺少 ticker 字段")
            if "price_levels" in ticker and not isinstance(ticker["price_levels"], list):
                errors.append(f"mentioned_tickers[{idx}].price_levels 不是列表")

        if errors:
            raise SchemaError(f"Schema 验证失败 ({video_id}): {'; '.join(errors)}")

    # ── 数据提取（优化：统一在协调层调用一次，结果向下传递）────────────────────

    def extract_tickers(self, data: dict) -> list[str]:
        result: list[str] = []
        for t in (data.get("mentioned_tickers") or []):
            if isinstance(t, dict) and t.get("ticker"):
                result.append(t["ticker"])
            elif isinstance(t, str) and t.strip():
                result.append(t.strip())
        return result

    def extract_people(self, data: dict) -> list[str]:
        return [p for p in (data.get("people_mentioned") or []) if p]

    def extract_price_levels(self, data: dict) -> list[dict]:
        """
        提取各股票的价格水平列表。
        返回：[{ticker, company_name, analyst, sentiment, levels: [{level, type, context}]}]
        """
        result = []
        for t in (data.get("mentioned_tickers") or []):
            if not isinstance(t, dict):
                continue
            levels = t.get("price_levels") or []
            if not levels:
                continue
            result.append({
                "ticker":       t["ticker"],
                "company_name": t.get("company_name", ""),
                "analyst":      t.get("analyst", "unknown"),
                "sentiment":    t.get("sentiment", "neutral"),
                "levels": [
                    {
                        "level":   lv.get("level"),
                        "type":    lv.get("type", "observation"),
                        "context": lv.get("context", ""),
                    }
                    for lv in levels
                ],
            })
        return result
