#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
根据博主名字，调用 YouTube Data API v3 搜索频道，
将结果直接追加到现有 channels.yaml 末尾，避免手工插入。

特性：
1. 优先从 .env 读取 YOUTUBE_DATA_API_KEY
2. 读取现有 channels.yaml
3. 只追加不存在的频道（按 name 或 url 去重）
4. 尽量保留原 YAML 注释和格式（使用 ruamel.yaml）

依赖：
pip install requests rapidfuzz python-dotenv ruamel.yaml

.env 示例：
YOUTUBE_DATA_API_KEY=your_api_key
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz
from ruamel.yaml import YAML

API_BASE = "https://www.googleapis.com/youtube/v3/search"

# 第一版建议追踪名单
CHANNEL_SEEDS: List[Dict[str, str]] = [
    {"name": "Ben Felix", "host": "Ben Felix"},
    {"name": "The Plain Bagel", "host": "Richard Coffin"},
    {"name": "Aswath Damodaran", "host": "Aswath Damodaran"},
    {"name": "FAST Graphs", "host": "Chuck Carnevale"},
    {"name": "Morningstar", "host": "Morningstar"},
    {"name": "Joseph Carlson", "host": "Joseph Carlson"},
    {"name": "Joseph Carlson After Hours", "host": "Joseph Carlson"},
    {"name": "Excess Returns", "host": "Jack Forehand / Justin Carbonneau"},
    {"name": "Patrick Boyle", "host": "Patrick Boyle"},
    {"name": "The Compound", "host": "Josh Brown / Michael Batnick"},
    {"name": "MacroVoices", "host": "Erik Townsend"},
    {"name": "Rational Reminder", "host": "Cameron Passmore / Ben Felix"},
    {"name": "Bloomberg Television", "host": "Bloomberg"},
    {"name": "CNBC Television", "host": "CNBC"},
]


def load_api_key() -> str:
    """
    优先从脚本所在目录的 .env 加载，
    然后读取环境变量 YOUTUBE_DATA_API_KEY。
    """
    script_dir = Path(__file__).resolve().parent
    env_path = script_dir / ".env"

    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    api_key = os.getenv("YOUTUBE_DATA_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "未找到 YOUTUBE_DATA_API_KEY。请在 .env 中设置，例如：\n"
            "YOUTUBE_DATA_API_KEY=your_api_key"
        )
    return api_key


def search_channel(api_key: str, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    params = {
        "part": "snippet",
        "q": query,
        "type": "channel",
        "maxResults": max_results,
        "key": api_key,
    }
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])


def normalize_text(s: str) -> str:
    return " ".join(s.lower().strip().split())


def choose_best_channel(query: str, items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    简单匹配逻辑：
    - 标题相似度优先
    - description 辅助
    """
    if not items:
        return None

    query_norm = normalize_text(query)
    scored: List[tuple[int, Dict[str, Any]]] = []

    for item in items:
        snippet = item.get("snippet", {})
        title = normalize_text(snippet.get("title", ""))
        desc = normalize_text(snippet.get("description", ""))
        channel_id = item.get("id", {}).get("channelId", "")

        score_title = fuzz.ratio(query_norm, title)
        score_partial = fuzz.partial_ratio(query_norm, title)
        score_desc = fuzz.partial_ratio(query_norm, desc)

        score = int(score_title * 0.55 + score_partial * 0.35 + score_desc * 0.10)

        if query_norm in title:
            score += 8
        if channel_id:
            score += 2

        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def channel_feed_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def build_entries(api_key: str, seeds: List[Dict[str, str]]) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []

    for seed in seeds:
        name = seed["name"]
        host = seed["host"]

        try:
            items = search_channel(api_key, name, max_results=5)
            best = choose_best_channel(name, items)

            if not best:
                print(f"[WARN] 未找到频道: {name}", file=sys.stderr)
                continue

            channel_id = best.get("id", {}).get("channelId")
            snippet = best.get("snippet", {})
            found_title = snippet.get("title", "")

            if not channel_id:
                print(f"[WARN] 缺少 channelId: {name}", file=sys.stderr)
                continue

            entry = {
                "name": name,
                "url": channel_feed_url(channel_id),
                "host": host,
            }
            results.append(entry)
            print(f"[OK] {name} -> {found_title} -> {channel_id}", file=sys.stderr)

            time.sleep(0.2)

        except requests.HTTPError as e:
            print(f"[ERROR] HTTP 错误: {name}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] 处理失败: {name}: {e}", file=sys.stderr)

    return results


def load_existing_yaml(yaml_path: Path):
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=2, offset=0)

    if not yaml_path.exists():
        return yaml, []

    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    if data is None:
        data = []
    elif not isinstance(data, list):
        raise ValueError(f"{yaml_path} 不是 YAML 列表，无法追加。")

    return yaml, data


def build_existing_indexes(data: List[Dict[str, Any]]) -> tuple[Set[str], Set[str]]:
    existing_names: Set[str] = set()
    existing_urls: Set[str] = set()

    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()

        if name:
            existing_names.add(name)
        if url:
            existing_urls.add(url)

    return existing_names, existing_urls


def append_new_entries(
    existing_data: List[Dict[str, Any]],
    new_entries: List[Dict[str, str]],
) -> int:
    existing_names, existing_urls = build_existing_indexes(existing_data)
    added_count = 0

    for entry in new_entries:
        name = entry["name"].strip()
        url = entry["url"].strip()

        if name in existing_names:
            print(f"[SKIP] 已存在同名频道: {name}", file=sys.stderr)
            continue

        if url in existing_urls:
            print(f"[SKIP] 已存在同 URL 频道: {url}", file=sys.stderr)
            continue

        existing_data.append(entry)
        existing_names.add(name)
        existing_urls.add(url)
        added_count += 1
        print(f"[ADD] 已追加: {name}", file=sys.stderr)

    return added_count


def save_yaml(yaml, data, yaml_path: Path) -> None:
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)


def main() -> int:
    try:
        api_key = load_api_key()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    script_dir = Path(__file__).resolve().parent
    yaml_path = script_dir / "channels.yaml"

    try:
        yaml, existing_data = load_existing_yaml(yaml_path)
    except Exception as e:
        print(f"[ERROR] 读取 channels.yaml 失败: {e}", file=sys.stderr)
        return 1

    new_entries = build_entries(api_key, CHANNEL_SEEDS)
    added_count = append_new_entries(existing_data, new_entries)

    try:
        save_yaml(yaml, existing_data, yaml_path)
    except Exception as e:
        print(f"[ERROR] 写入 channels.yaml 失败: {e}", file=sys.stderr)
        return 1

    print(f"\n完成。新增 {added_count} 条，文件位置: {yaml_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())