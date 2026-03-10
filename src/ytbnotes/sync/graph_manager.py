"""
lib/core/graph_manager.py

反向链接与关系追踪引擎。
对应 JS 版 GraphManager.js。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ytbnotes.sync.path_resolver import PathResolver


class RelationType:
    MENTIONS_TICKER = "mentions_ticker"
    CONTAINS_LEVELS = "contains_levels"
    ANALYZED_BY     = "analyzed_by"
    RELATED_VIDEO   = "related_video"
    CO_MENTIONED    = "co_mentioned"
    APPEARS_IN      = "appears_in"


class GraphManager:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.path_resolver: PathResolver = cfg.get("pathResolver")
        if not self.path_resolver:
            raise ValueError("GraphManager 需要 pathResolver 实例。")
        self.enable_index_persistence: bool = cfg.get("enableIndexPersistence", True)

        # 内存索引
        # ticker  → [video_ids]
        self._tickers:   dict[str, list[str]] = {}
        # video_id → {tickers, people, timestamp}
        self._videos:    dict[str, dict]      = {}
        # person  → [video_ids]
        self._people:    dict[str, list[str]] = {}
        # target_id → [{source_id, relation_type, created_at}]
        self._backlinks: dict[str, list[dict]] = {}

    # ── 索引构建 ──────────────────────────────────────────────────────────────

    def build_index(self) -> None:
        """扫描现有 Vault，从 front matter 重建图谱关系。"""
        print("🔨 构建图谱索引...")
        vault_path = self.path_resolver.get_vault_root()
        if not vault_path.exists():
            print("  ℹ️  Vault 目录不存在，跳过索引构建")
            return

        processed = 0
        for md_file in vault_path.glob("**/*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                front_matter = self._parse_front_matter(content)
                note_id   = front_matter.get("id") or md_file.stem
                note_type = front_matter.get("type")

                if note_type == "video-note":
                    self._index_video_note(note_id, front_matter)
                elif note_type == "price-level":
                    self._index_price_level_note(note_id, front_matter)
                elif note_type == "person":
                    self._index_person_note(note_id, front_matter)

                processed += 1
            except Exception as exc:
                print(f"  ⚠️  索引失败 {md_file}: {exc}")

        print(f"  ✅ 索引构建完成: {processed} 个笔记")

        if self.enable_index_persistence:
            self.persist_index()

    def _parse_front_matter(self, content: str) -> dict:
        """提取 Markdown 文件的 YAML front matter。"""
        if not content.startswith("---"):
            return {}
        end = content.find("\n---", 3)
        if end == -1:
            return {}
        try:
            return yaml.safe_load(content[3:end]) or {}
        except yaml.YAMLError:
            return {}

    def _index_video_note(self, note_id: str, fm: dict) -> None:
        self._videos[note_id] = {
            "tickers":   fm.get("mentioned_tickers", []),
            "people":    fm.get("people_mentioned", []),
            "timestamp": (fm.get("source") or {}).get("published") or fm.get("modified"),
        }
        for ticker_obj in fm.get("mentioned_tickers") or []:
            ticker = ticker_obj.get("ticker") if isinstance(ticker_obj, dict) else ticker_obj
            if ticker:
                self._tickers.setdefault(ticker, [])
                if note_id not in self._tickers[ticker]:
                    self._tickers[ticker].append(note_id)
                self.add_edge(note_id, f"price-level-{ticker}", RelationType.CONTAINS_LEVELS)
                self.add_edge(note_id, ticker, RelationType.MENTIONS_TICKER)

        for person in fm.get("people_mentioned") or []:
            self._people.setdefault(person, [])
            if note_id not in self._people[person]:
                self._people[person].append(note_id)
            slug = person.lower().replace(" ", "-")
            self.add_edge(note_id, f"person-{slug}", RelationType.APPEARS_IN)

    def _index_price_level_note(self, note_id: str, fm: dict) -> None:
        ticker = fm.get("ticker")
        if ticker:
            self._tickers.setdefault(ticker, [])
            for video_id in fm.get("source_videos") or []:
                self.add_edge(note_id, video_id, RelationType.RELATED_VIDEO)

    def _index_person_note(self, note_id: str, fm: dict) -> None:
        person = fm.get("name")
        if person:
            self._people.setdefault(person, fm.get("videos_appeared") or [])

    def upsert_video(
        self,
        video_id: str,
        *,
        mentioned_tickers: list | None = None,
        people_mentioned: list[str] | None = None,
        timestamp: str | None = None,
    ) -> None:
        """
        将单条视频分析结果写入图索引内存结构（用于本次运行增量更新）。
        mentioned_tickers 支持:
          - ["NVDA", "AAPL"]
          - [{"ticker": "NVDA", ...}, ...]
        """
        ticker_entries = list(mentioned_tickers or [])
        people = [p for p in (people_mentioned or []) if p]

        self._videos[video_id] = {
            "tickers": ticker_entries,
            "people": people,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        }

        for t in ticker_entries:
            ticker = t.get("ticker") if isinstance(t, dict) else t
            if not ticker:
                continue
            self._tickers.setdefault(ticker, [])
            if video_id not in self._tickers[ticker]:
                self._tickers[ticker].append(video_id)
            self.add_edge(video_id, f"price-level-{ticker}", RelationType.CONTAINS_LEVELS)
            self.add_edge(video_id, ticker, RelationType.MENTIONS_TICKER)

        for person in people:
            self._people.setdefault(person, [])
            if video_id not in self._people[person]:
                self._people[person].append(video_id)
            slug = person.lower().replace(" ", "-")
            self.add_edge(video_id, f"person-{slug}", RelationType.APPEARS_IN)

    # ── 边操作 ────────────────────────────────────────────────────────────────

    def add_edge(self, source_id: str, target_id: str, relation_type: str) -> None:
        backlinks = self._backlinks.setdefault(target_id, [])
        already = any(
            b["source_id"] == source_id and b["relation_type"] == relation_type
            for b in backlinks
        )
        if not already:
            backlinks.append({
                "source_id":     source_id,
                "relation_type": relation_type,
                "created_at":    datetime.now(timezone.utc).isoformat(),
            })

    # ── 查询 ─────────────────────────────────────────────────────────────────

    def get_backlinks(self, note_id: str) -> list[dict]:
        return self._backlinks.get(note_id, [])

    def get_videos_by_ticker(self, ticker: str) -> list[str]:
        return self._tickers.get(ticker, [])

    def get_videos_by_person(self, person: str) -> list[str]:
        return self._people.get(person, [])

    def get_video_info(self, video_id: str) -> dict | None:
        return self._videos.get(video_id)

    # ── 股票概览数据生成 ──────────────────────────────────────────────────────

    def generate_stock_overview_data(self, ticker: str) -> dict:
        videos     = self.get_videos_by_ticker(ticker)
        video_infos = [self.get_video_info(v) for v in videos if self.get_video_info(v)]

        analysts: set[str] = set()
        sentiments: list[str] = []
        for vi in video_infos:
            for t in (vi.get("tickers") or []):
                t_obj = t if isinstance(t, dict) else {}
                if t_obj.get("ticker") == ticker:
                    if t_obj.get("analyst"):
                        analysts.add(t_obj["analyst"])
                    if t_obj.get("sentiment"):
                        sentiments.append(t_obj["sentiment"])

        sentiment_map = {
            "bullish": 0.75, "very_bullish": 0.9,
            "neutral": 0.5,
            "bearish": 0.25, "very_bearish": 0.1,
        }
        avg_sentiment = (
            sum(sentiment_map.get(s, 0.5) for s in sentiments) / len(sentiments)
            if sentiments else 0.5
        )

        latest = (
            sorted(video_infos, key=lambda v: v.get("timestamp") or "", reverse=True)[0]
            if video_infos else None
        )

        return {
            "ticker":          ticker,
            "video_count":     len(videos),
            "latest_analysis": latest,
            "avg_sentiment":   avg_sentiment,
            "top_analysts":    list(analysts),
            "source_videos":   videos,
        }

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def persist_index(self) -> None:
        index_path = self.path_resolver.get_graph_index_path()
        index_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "tickers":    self._tickers,
            "people":     self._people,
            "videos":     self._videos,
            "backlinks":  self._backlinks,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        index_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def load_index(self) -> bool:
        index_path = self.path_resolver.get_graph_index_path()
        if not index_path.exists():
            return False
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            self._tickers   = data.get("tickers", {})
            self._people    = data.get("people", {})
            self._videos    = data.get("videos", {})
            self._backlinks = data.get("backlinks", {})
            return True
        except Exception as exc:
            print(f"  ⚠️  索引加载失败: {exc}")
            return False

    def clear_index(self) -> None:
        self._tickers.clear()
        self._videos.clear()
        self._people.clear()
        self._backlinks.clear()

    # ── 统计 ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "tickers":   len(self._tickers),
            "videos":    len(self._videos),
            "people":    len(self._people),
            "backlinks": sum(len(v) for v in self._backlinks.values()),
        }
