"""
obsidian_sync.py

Obsidian Sync Bridge — Python 重写版
协调层：整合 Parser、GraphManager、StorageProvider、NoteRenderer
对应 JS 版 obsidian_sync.js (Phase 2.6)

优化项（相对 JS 版）：
  1. 点位历史数据改用独立 JSON 文件存储，不再从 Markdown 表格反向解析
  2. extract_tickers / extract_people 在 process_json_file 中统一调用一次，结果向下传递
  3. initialize() 移入 try 块，初始化异常可被正确捕获
  4. 所有输出目录在 initialize() 里统一预建，消除每次写入时的重复检查
  5. _apply_overrides 对非法 key 做基本校验
"""

import json
import re
import sys
import yaml
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from ytbnotes.sync.config_loader import ConfigLoader
from ytbnotes.sync.path_resolver import PathResolver
from ytbnotes.sync.graph_manager import GraphManager
from ytbnotes.sync.note_renderer import NoteRenderer
from ytbnotes.sync.parser import Parser
from ytbnotes.sync.storage import StorageProvider

_TICKER_RE = re.compile(r"^[A-Z0-9._-]{1,20}$")
_TICKER_ALIASES = {
    # 历史数据常见写法：公司名误填到 ticker 字段
    "CIRCLE": "CRCL",
}
_TICKER_SPLIT_RE = re.compile(r"[\s(（\[]")
_TICKER_PREFIX_RE = re.compile(r"^[A-Z]+:")
_TICKER_TRAILING_PUNCT_RE = re.compile(r"[,:;，。；：]+$")


class ObsidianSync:
    def __init__(
        self,
        config_path: str | Path | None = None,
        config_overrides: dict | None = None,
    ):
        self.config_path      = config_path
        self.config_overrides = config_overrides

        # 核心模块（initialize() 后可用）
        self.config_loader: ConfigLoader | None = None
        self.path_resolver: PathResolver | None = None
        self.parser:        Parser | None       = None
        self.graph:         GraphManager | None = None
        self.storage:       StorageProvider | None = None
        self.renderer:      NoteRenderer | None = None

        self.stats = {"processed": 0, "errors": []}
        self.ticker_aliases: dict[str, str] = dict(_TICKER_ALIASES)

        # MOC 仪表盘数据收集
        self._moc_ticker_agg: dict[str, dict] = {}   # ticker -> {count, sentiments, channels}
        self._moc_videos: list[dict] = []            # [{video_id, channel, title, date, tickers}]

        # 频道 host 映射 (用于兼容缺少 host 字段的历史数据)
        self.channel_to_host: dict[str, str] = {}

    # ── 初始化 ────────────────────────────────────────────────────────────────

    def initialize(self) -> None:
        # 1. 加载配置
        self.config_loader = ConfigLoader(self.config_path)
        self.config_loader.load()

        # 2. 应用运行时覆盖
        if self.config_overrides:
            self._apply_overrides(self.config_overrides)

        cfg = self.config_loader.config

        # 3. 路径解析器
        self.path_resolver = PathResolver(cfg)

        # 4. 核心模块
        processing = cfg.get("processing", {})
        self.parser = Parser({
            "pathResolver":    self.path_resolver,
            "jsonPattern":     processing.get("json_pattern", "**/*.json"),
            "excludePatterns": processing.get("exclude_patterns", []),
        })
        self.graph = GraphManager({
            "pathResolver":          self.path_resolver,
            "enableIndexPersistence": True,
        })
        self.storage = StorageProvider({
            "atomicWrite": processing.get("atomic_write", True),
        })
        self.renderer = NoteRenderer({"config": cfg})
        self.ticker_aliases = self._load_ticker_aliases(cfg)

        # 5. 统一预建所有输出目录（避免每次写入时重复检查）
        for folder_type in ("videos", "transcripts", "price_levels", "people", "stock_overview", "index"):
            self.storage.ensure_dir(self.path_resolver.get_folder(folder_type))

        # 6. 加载 channels.yaml 建立向前兼容的 host 映射
        self._load_channels_host_map()

        print("✅ 配置系统初始化完成")
        print(f"   Vault 目录: {self.path_resolver.get_vault_root()}")
        print(f"   分析输出:   {self.path_resolver.get_analysis_output()}")
        print(f"   Ticker 映射: {len(self.ticker_aliases)} 条")

    def _apply_overrides(self, overrides: dict) -> None:
        cfg = self.config_loader.config
        for key, value in overrides.items():
            # 基本校验：跳过空 key 或以点开头/结尾的 key
            if not key or key.startswith(".") or key.endswith("."):
                print(f"  ⚠️  跳过无效的配置覆盖键: '{key}'")
                continue
            if "." in key:
                parts = key.split(".")
                target = cfg
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = value
            else:
                cfg[key] = value

    @staticmethod
    def _load_ticker_aliases(config: dict) -> dict[str, str]:
        merged: dict[str, str] = dict(_TICKER_ALIASES)
        raw_custom = (
            (config or {}).get("processing", {}).get("ticker_aliases", {})
            if isinstance(config, dict)
            else {}
        )
        if not isinstance(raw_custom, dict):
            return merged
        for raw_key, raw_val in raw_custom.items():
            key = str(raw_key or "").strip().upper()
            val = str(raw_val or "").strip().upper()
            if not key or not val:
                continue
            if not _TICKER_RE.match(val):
                print(f"  ⚠️  忽略非法 ticker 映射: {raw_key} -> {raw_val}")
                continue
        return merged

    def _load_channels_host_map(self) -> None:
        """加载 channels.yaml，构建 channel_name -> host 的映射字典，用于兼容历史数据。"""
        channels_file = Path(self.config_loader._project_root) / "channels.yaml"
        if not channels_file.exists():
            return
            
        try:
            with open(channels_file, "r", encoding="utf-8") as f:
                channels_data = yaml.safe_load(f) or []
            
            for ch in channels_data:
                name = ch.get("name")
                host = ch.get("host")
                if name and host:
                    self.channel_to_host[name] = host
        except Exception as e:
            print(f"  ⚠️  加载 channels.yaml 失败: {e}")

    # ── 主流程 ────────────────────────────────────────────────────────────────

    def sync(self) -> None:
        print("\n🚀 Obsidian Sync 启动 (Python 版)")
        print("=" * 50)

        try:
            # initialize() 移入 try 块，初始化异常可被正确捕获
            self.initialize()

            # 1. 构建图谱索引
            self.graph.build_index()

            # 2. 发现 JSON 文件
            json_files = self.parser.discover_json_files()
            print(f"🔍 发现 {len(json_files)} 个分析结果文件")

            # 3. 处理每个 JSON 文件
            for json_path in json_files:
                self._process_json_file(json_path)

            # 4. 持久化图谱（确保 graph-index.json 包含本次新增数据）
            if self.graph.enable_index_persistence:
                self.graph.persist_index()
                print("🧠 图谱索引已持久化")

            # 5. 生成 MOC
            self._generate_moc()

            # 6. 统计输出
            self._print_stats()

        except Exception as exc:
            print(f"❌ 同步失败: {exc}")
            self.stats["errors"].append({"phase": "main", "error": str(exc)})
            raise

    # ── 处理单个 JSON ─────────────────────────────────────────────────────────

    def _process_json_file(self, json_path: Path) -> None:
        try:
            video_id, data = self.parser.parse_file(json_path)
            print(f"\n📝 处理: {video_id}")
            self._sanitize_mentioned_tickers(video_id, data)

            # 优化：统一解析一次，向下传递，避免重复调用
            tickers = self.parser.extract_tickers(data)
            people  = self.parser.extract_people(data)

            # 从 metadata 提取 host 和 channel_name
            meta = data.get("metadata", {}) if isinstance(data, dict) else {}
            host_name = meta.get("host")
            channel_name = meta.get("channel", "未知频道")
            
            # 【兼容历史数据优化】
            # 如果 JSON 数据没有 host 字段（旧数据），自动去 channels.yaml() 映射表里找
            if not host_name and channel_name in self.channel_to_host:
                host_name = self.channel_to_host[channel_name]

            # 兜底回 channel_name
            primary_host = host_name if host_name else channel_name
            
            # 从 mentioned_tickers 提取分析师名，补充到 people 列表 (用于视频内展示)
            mentioned = data.get("mentioned_tickers", []) or []
            analyst_names = set()
            for t in (mentioned if isinstance(mentioned, list) else []):
                if isinstance(t, dict):
                    a = str(t.get("analyst", "")).strip()
                    if a and a.lower() not in ("unknown", "n/a", ""):
                        analyst_names.add(a)
            
            # 核心人物（只为他们生成人物主页笔记）
            core_analysts = [primary_host]

            # 合并：people_mentioned + analyst 去重 (用于视频内的展示)
            all_people = list(dict.fromkeys(people + sorted(analyst_names)))

            self._generate_linked_video_and_transcript_notes(video_id, data, json_path, all_people)
            self._process_price_levels(video_id, data)
            self._process_stock_overview(video_id, data)
            
            # **关键改动**：只对 core_analysts 生成独立的 人物.md
            self._generate_people_notes(video_id, core_analysts, tickers)
            self._update_graph_index(video_id, data, tickers, core_analysts)

            # ── 收集 MOC 仪表盘数据 ──
            meta = data.get("metadata", {}) if isinstance(data, dict) else {}
            mentioned = data.get("mentioned_tickers", []) or []
            for t in (mentioned if isinstance(mentioned, list) else []):
                tk = str(t.get("ticker", "") if isinstance(t, dict) else "").strip()
                if not tk:
                    continue
                agg = self._moc_ticker_agg.setdefault(tk, {
                    "count": 0, "sentiments": [], "channels": set(),
                })
                agg["count"] += 1
                agg["sentiments"].append(
                    t.get("sentiment", "neutral") if isinstance(t, dict) else "neutral"
                )
                ch = meta.get("channel", "")
                if ch:
                    agg["channels"].add(ch)
            self._moc_videos.append({
                "video_id": video_id,
                "channel":  meta.get("channel", ""),
                "title":    meta.get("title", video_id),
                "date":     meta.get("date", ""),
                "tickers":  [
                    str(t.get("ticker", "")) for t in (mentioned if isinstance(mentioned, list) else [])
                    if isinstance(t, dict) and t.get("ticker")
                ],
                # 用于 MOC 链接：相对 Vault 的路径（不含 .md）
                "note_link": self._video_note_wikilink_target(
                    video_id=video_id,
                    title=meta.get("title"),
                    channel_name=meta.get("channel"),
                    published_date=meta.get("date"),
                ),
            })

            self.stats["processed"] += 1

        except Exception as exc:
            print(f"  ❌ 处理失败 {json_path}: {exc}")
            self.stats["errors"].append({"file": str(json_path), "error": str(exc)})

    # ── 视频笔记 ──────────────────────────────────────────────────────────────

    def _video_note_wikilink_target(
        self,
        *,
        video_id: str,
        title: str | None,
        channel_name: str | None,
        published_date: str | None,
    ) -> str:
        """构造视频笔记的 wikilink 目标路径（Vault 相对路径，不含 .md）。"""
        try:
            note_path = self.path_resolver.get_video_note_path(
                video_id=video_id,
                title=title,
                channel_name=channel_name,
                published_date=published_date,
            )
            rel = note_path.resolve().relative_to(
                self.path_resolver.get_vault_root().resolve()
            )
            target = rel.as_posix()
            if target.endswith(".md"):
                target = target[:-3]
            return target
        except Exception:
            return video_id  # 降级：裸 video_id


    @staticmethod
    def _note_matches_id(content: str, note_id: str) -> bool:
        pattern = rf'^id:\s*["\']?{re.escape(note_id)}["\']?\s*$'
        return bool(re.search(pattern, content or "", flags=re.MULTILINE))

    def _to_wikilink(self, abs_path: Path, label: str | None = None) -> str:
        rel = abs_path.resolve().relative_to(self.path_resolver.get_vault_root().resolve())
        target = rel.as_posix()
        if target.endswith(".md"):
            target = target[:-3]
        if label:
            return f"[[{target}|{label}]]"
        return f"[[{target}]]"

    def _resolve_note_path(
        self,
        *,
        path_builder: Callable[..., Path],
        expected_note_id: str,
        video_id: str,
        title: str | None,
        channel_name: str | None,
        published_date: str | None,
    ) -> Path:
        note_path = path_builder(
            video_id=video_id,
            title=title,
            channel_name=channel_name,
            published_date=published_date,
        )
        if self.storage.exists(note_path):
            existing = self.storage.read_file(note_path)
            if not self._note_matches_id(existing or "", expected_note_id):
                fallback_title = f"{title or video_id} [{video_id}]"
                note_path = path_builder(
                    video_id=video_id,
                    title=fallback_title,
                    channel_name=channel_name,
                    published_date=published_date,
                )
        return note_path

    @staticmethod
    def _extract_transcript_from_analysis_markdown(markdown_text: str) -> str:
        if not markdown_text:
            return ""
        # 兼容 audio_analyzer.py 产物：完整转录位于 <details> ... <br> 与 </details> 之间。
        pattern = r"#\s*【完整转录\s*\(带内部时间戳\)】.*?<details>.*?<br>\s*(.*?)\s*</details>"
        m = re.search(pattern, markdown_text, flags=re.DOTALL)
        if m:
            return m.group(1).strip()
        return ""

    @staticmethod
    def _normalize_transcript_text(text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        lines = raw.splitlines()
        ts_re = re.compile(r"^\[\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\]")
        start_idx = None
        for idx, line in enumerate(lines):
            if ts_re.match(line.strip()):
                start_idx = idx
                break
        if start_idx is None:
            return raw
        cleaned = "\n".join(lines[start_idx:]).strip()
        return cleaned

    def _load_transcript_fallback_from_markdown(
        self,
        *,
        json_path: Path,
        video_id: str,
    ) -> str:
        parent = json_path.parent
        candidates = sorted(parent.glob("*.md"))
        if not candidates:
            return ""
        expected_marker = f"video_id: {video_id}"
        for md_path in candidates:
            try:
                content = md_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if expected_marker not in content:
                continue
            extracted = self._extract_transcript_from_analysis_markdown(content)
            if extracted:
                return extracted
        return ""

    def _generate_linked_video_and_transcript_notes(
        self,
        video_id: str,
        data: dict,
        json_path: Path,
        all_people: list[str] | None = None,
    ) -> None:
        metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
        title = metadata.get("title")
        channel_name = metadata.get("channel")
        published_date = metadata.get("date")

        video_note_path = self._resolve_note_path(
            path_builder=self.path_resolver.get_video_note_path,
            expected_note_id=video_id,
            video_id=video_id,
            title=title,
            channel_name=channel_name,
            published_date=published_date,
        )
        transcript_note_path = self._resolve_note_path(
            path_builder=self.path_resolver.get_transcript_note_path,
            expected_note_id=f"{video_id}-transcript",
            video_id=video_id,
            title=title,
            channel_name=channel_name,
            published_date=published_date,
        )

        brief_text = data.get("brief_text", "") or data.get("summary", "")
        transcript_text = (
            data.get("raw_transcript", "")
            or data.get("full_transcript", "")
            or data.get("transcript", "")
        )
        if not transcript_text:
            transcript_text = self._load_transcript_fallback_from_markdown(
                json_path=json_path,
                video_id=video_id,
            )
        transcript_text = self._normalize_transcript_text(transcript_text)

        # people_mentioned：优先用合并后的 all_people（包含分析师）
        people_for_note = all_people if all_people is not None else data.get("people_mentioned", [])

        video_note_content = self.renderer.render_video_note(
            video_id=video_id,
            metadata=metadata,
            summary=brief_text,
            key_points=data.get("key_points", []),
            mentioned_tickers=data.get("mentioned_tickers", []),
            people_mentioned=people_for_note,
            transcript_note_link=self._to_wikilink(transcript_note_path, "完整转录"),
            note_title=video_note_path.stem,
        )
        self.storage.write_file_safely(video_note_path, video_note_content, silent=True)

        transcript_note_content = self.renderer.render_transcript_note(
            video_id=video_id,
            metadata=metadata,
            transcript_text=transcript_text,
            brief_note_link=self._to_wikilink(video_note_path, "视频简报"),
            note_title=transcript_note_path.stem,
        )
        self.storage.write_file_safely(transcript_note_path, transcript_note_content, silent=True)

    # ── 价格水平（优化：历史数据用 JSON 存储，不再解析 Markdown）────────────────

    def _process_price_levels(self, video_id: str, data: dict) -> None:
        price_levels_data = self.parser.extract_price_levels(data)
        for ticker_data in price_levels_data:
            try:
                self._update_price_level_note(video_id, ticker_data)
            except ValueError as e:
                # 历史脏数据可能包含非法 ticker（如公司名误入 ticker 字段）；
                # 跳过该条，避免单个脏点位阻塞整条视频同步。
                print(f"  ⚠️  跳过非法点位 ticker: {e}")

    @staticmethod
    def _ticker_candidates(raw_ticker: str) -> list[str]:
        raw = str(raw_ticker or "").strip()
        if not raw:
            return []

        candidates: list[str] = []

        def _add(value: str) -> None:
            v = str(value or "").strip().upper()
            if v and v not in candidates:
                candidates.append(v)

        _add(raw)
        cleaned = raw.strip().lstrip("$")
        _add(cleaned)
        _add(_TICKER_TRAILING_PUNCT_RE.sub("", cleaned))

        first_token = _TICKER_SPLIT_RE.split(cleaned, 1)[0]
        _add(first_token)

        no_prefix = _TICKER_PREFIX_RE.sub("", cleaned.upper())
        _add(no_prefix)
        _add(no_prefix.replace("/", "."))
        _add(no_prefix.replace("/", "-"))
        _add(no_prefix.replace(" ", ""))

        return candidates

    def _normalize_ticker_symbol(self, raw_ticker: str, company_name: str = "") -> str:
        candidates = self._ticker_candidates(raw_ticker)
        for c in candidates:
            mapped = self.ticker_aliases.get(c)
            if mapped:
                return mapped

        for c in candidates:
            if self._is_valid_ticker_symbol(c):
                return c

        company_key = str(company_name or "").strip().upper()
        if company_key:
            mapped = self.ticker_aliases.get(company_key)
            if mapped:
                return mapped
        return ""

    @staticmethod
    def _is_valid_ticker_symbol(ticker: str) -> bool:
        return bool(_TICKER_RE.match(str(ticker or "").strip()))

    def _sanitize_mentioned_tickers(self, video_id: str, data: dict) -> None:
        if not isinstance(data, dict):
            return

        mentioned = data.get("mentioned_tickers")
        if mentioned is None:
            data["mentioned_tickers"] = []
            return
        if not isinstance(mentioned, list):
            data["mentioned_tickers"] = []
            print("  ⚠️  mentioned_tickers 非列表，已重置为空")
            return

        cleaned: list[dict] = []
        remapped = 0
        dropped = 0
        for item in mentioned:
            if isinstance(item, dict):
                entry = dict(item)
            elif isinstance(item, str):
                entry = {"ticker": item}
            else:
                dropped += 1
                continue

            raw_ticker = str(entry.get("ticker") or "").strip()
            company_name = str(entry.get("company_name") or "").strip()
            ticker = self._normalize_ticker_symbol(raw_ticker, company_name)
            if not ticker:
                dropped += 1
                continue
            if not self._is_valid_ticker_symbol(ticker):
                dropped += 1
                continue

            if ticker != raw_ticker.upper():
                remapped += 1
            entry["ticker"] = ticker

            levels = entry.get("price_levels")
            if isinstance(levels, list):
                normalized_levels: list[dict] = []
                for lv in levels:
                    if not isinstance(lv, dict):
                        continue
                    normalized_levels.append({
                        "level": lv.get("level"),
                        "type": lv.get("type", "observation"),
                        "context": lv.get("context", ""),
                    })
                entry["price_levels"] = normalized_levels
            cleaned.append(entry)

        data["mentioned_tickers"] = cleaned
        if remapped:
            print(f"  🧹 ticker 清洗映射: {remapped} 条")
        if dropped:
            print(f"  ⚠️  丢弃无法识别 ticker: {dropped} 条 ({video_id})")

    @staticmethod
    def _split_text_segments(text: str) -> list[str]:
        raw = str(text or "").strip()
        if not raw:
            return []
        parts = re.split(r"\n+|(?<=[。！？.!?])\s+", raw)
        return [p.strip() for p in parts if p and p.strip()]

    def _extract_ticker_focus_excerpt(
        self,
        *,
        ticker: str,
        company_name: str,
        brief_text: str,
        key_points: list[str],
    ) -> str:
        keywords = [str(ticker or "").strip(), str(company_name or "").strip()]
        keywords = [k for k in keywords if k]
        keywords_lower = [k.lower() for k in keywords]

        matched_segments = []
        for seg in self._split_text_segments(brief_text):
            seg_lower = seg.lower()
            if any(k in seg_lower for k in keywords_lower):
                matched_segments.append(seg)
            if len(matched_segments) >= 3:
                break
        if matched_segments:
            return " ".join(matched_segments).strip()

        matched_points = []
        for kp in (key_points or []):
            s = str(kp or "").strip()
            if not s:
                continue
            s_lower = s.lower()
            if any(k in s_lower for k in keywords_lower):
                matched_points.append(s)
            if len(matched_points) >= 2:
                break
        if matched_points:
            return " ".join(matched_points).strip()

        fallback_text = str(brief_text or "").strip()
        if fallback_text:
            return fallback_text[:400].strip()
        if key_points:
            return " ".join([str(x).strip() for x in key_points[:2] if str(x).strip()]).strip()
        return ""

    def _extract_ticker_transcript_snippet(
        self,
        *,
        ticker: str,
        company_name: str,
        transcript_text: str,
    ) -> str:
        raw = str(transcript_text or "").strip()
        if not raw:
            return ""
        keywords = [str(ticker or "").strip(), str(company_name or "").strip()]
        keywords = [k for k in keywords if k]
        keywords_lower = [k.lower() for k in keywords]
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        for idx, ln in enumerate(lines):
            ll = ln.lower()
            if any(k in ll for k in keywords_lower):
                snippet = [ln]
                if idx + 1 < len(lines):
                    snippet.append(lines[idx + 1])
                return "\n".join(snippet).strip()
        return ""

    @staticmethod
    def _sort_timeline_entries_desc(entries: list[dict]) -> list[dict]:
        def _k(item: dict):
            return (
                str(item.get("date") or ""),
                str(item.get("video_id") or ""),
            )
        return sorted(entries, key=_k, reverse=True)

    def _update_stock_overview_note(self, ticker: str, company_name: str, new_entry: dict) -> None:
        overview_json_path = self.path_resolver.get_stock_overview_json_path(ticker)
        existing_data = self.storage.read_json(overview_json_path) or {}
        entries = []
        if isinstance(existing_data, dict):
            raw_entries = existing_data.get("entries") or []
            if isinstance(raw_entries, list):
                entries = [e for e in raw_entries if isinstance(e, dict)]

        by_video = {str(e.get("video_id")): e for e in entries if e.get("video_id")}
        by_video[str(new_entry.get("video_id"))] = new_entry
        merged_entries = self._sort_timeline_entries_desc(list(by_video.values()))

        canonical_company = company_name or ""
        if not canonical_company and isinstance(existing_data, dict):
            canonical_company = str(existing_data.get("company_name") or "")
        if not canonical_company:
            canonical_company = ticker

        overview_data = {
            "ticker": ticker,
            "company_name": canonical_company,
            "entries": merged_entries,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.storage.write_json(overview_json_path, overview_data, silent=True)

        overview_md_path = self.path_resolver.get_stock_overview_path(ticker)
        content = self.renderer.render_stock_overview_note(
            ticker=ticker,
            company_name=canonical_company,
            entries=merged_entries,
        )
        self.storage.write_file_safely(overview_md_path, content, silent=True)
        print(f"  📊 {ticker} 概览: {len(merged_entries)} 条时间线记录")

    def _process_stock_overview(self, video_id: str, data: dict) -> None:
        metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
        mentioned = data.get("mentioned_tickers", []) if isinstance(data, dict) else []
        if not isinstance(mentioned, list) or not mentioned:
            return

        brief_text = str(data.get("brief_text") or data.get("summary") or "")
        key_points = data.get("key_points") or []
        if not isinstance(key_points, list):
            key_points = []
        transcript_text = str(
            data.get("raw_transcript")
            or data.get("full_transcript")
            or data.get("transcript")
            or ""
        )

        for t in mentioned:
            if not isinstance(t, dict):
                continue
            raw_ticker = str(t.get("ticker") or "").strip()
            company_name = str(t.get("company_name") or "").strip()
            ticker = self._normalize_ticker_symbol(raw_ticker, company_name)
            if not ticker:
                continue
            if not self._is_valid_ticker_symbol(ticker):
                print(f"  ⚠️  跳过股票概览非法 ticker: {ticker}")
                continue

            focus_excerpt = self._extract_ticker_focus_excerpt(
                ticker=ticker,
                company_name=company_name,
                brief_text=brief_text,
                key_points=key_points,
            )
            transcript_snippet = self._extract_ticker_transcript_snippet(
                ticker=ticker,
                company_name=company_name,
                transcript_text=transcript_text,
            )
            if not focus_excerpt and transcript_snippet:
                focus_excerpt = transcript_snippet

            entry = {
                "video_id": video_id,
                "date": metadata.get("date"),
                "channel": metadata.get("channel"),
                "analyst": t.get("analyst", "unknown"),
                "sentiment": t.get("sentiment", "neutral"),
                "focus_excerpt": focus_excerpt,
                "key_points": [str(x).strip() for x in key_points if str(x).strip()][:8],
                "price_levels": t.get("price_levels") if isinstance(t.get("price_levels"), list) else [],
                "source_url": metadata.get("youtube_url"),
                "transcript_snippet": transcript_snippet,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._update_stock_overview_note(ticker=ticker, company_name=company_name, new_entry=entry)

    @staticmethod
    def _to_float(value) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _normalize_level_for_key(cls, value) -> str:
        num = cls._to_float(value)
        if num is not None:
            return str(num)
        return str(value).strip()

    @classmethod
    def _level_sort_key(cls, level_obj: dict) -> tuple[int, float | str]:
        num = cls._to_float(level_obj.get("level"))
        if num is not None:
            return (0, num)
        return (1, str(level_obj.get("level", "")))

    def _update_price_level_note(self, video_id: str, ticker_data: dict) -> None:
        ticker       = ticker_data["ticker"]
        company_name = ticker_data.get("company_name", "")
        analyst      = ticker_data.get("analyst", "unknown")
        new_levels   = ticker_data.get("levels", [])

        # ── 读取历史数据（从独立 JSON，不再解析 Markdown 表格）────────────────
        json_path = self.path_resolver.get_price_level_json_path(ticker)
        existing: list[dict] = self.storage.read_json(json_path) or []
        if existing:
            print(f"  📖 恢复 {len(existing)} 条历史点位")

        # ── 合并：Bucket Key = level_type_analyst ────────────────────────────
        today     = date.today().isoformat()
        iso_now   = datetime.now(timezone.utc).isoformat()
        formatted: list[dict] = []
        for lv in new_levels:
            level_value = lv.get("level")
            if level_value in (None, ""):
                continue
            formatted.append({
                "level":        level_value,
                "type":         lv.get("type", "observation"),
                "context":      lv.get("context", ""),
                "source_video": video_id,
                "date_added":   today,
                "timestamp_iso": iso_now,
                "analyst":      analyst,
            })

        buckets: dict[str, dict] = {
            (
                f"{self._normalize_level_for_key(lv.get('level'))}_"
                f"{lv.get('type', 'observation')}_{lv.get('analyst', 'unknown')}"
            ): lv
            for lv in existing
        }
        for lv in formatted:
            key = (
                f"{self._normalize_level_for_key(lv.get('level'))}_"
                f"{lv.get('type', 'observation')}_{lv.get('analyst', 'unknown')}"
            )
            buckets[key] = lv

        merged = sorted(buckets.values(), key=self._level_sort_key)
        source_videos = list(dict.fromkeys(lv.get("source_video") for lv in merged if lv.get("source_video")))

        # ── 持久化历史 JSON ───────────────────────────────────────────────────
        self.storage.write_json(json_path, merged, silent=True)

        # ── 渲染 Markdown（展示层，渲染格式变更不影响历史数据）────────────────
        md_path = self.path_resolver.get_price_level_path(ticker)
        content = self.renderer.render_price_level_note(
            ticker=ticker,
            company_name=company_name,
            levels=merged,
            source_videos=source_videos,
        )
        self.storage.write_file_safely(md_path, content, silent=True)

        print(f"  💰 {ticker}: +{len(formatted)} = {len(merged)} 条")

    # ── 人物笔记 ──────────────────────────────────────────────────────────────

    def _generate_people_notes(
        self, video_id: str, people: list[str], tickers: list[str]
    ) -> None:
        for person in people:
            person_path = self.path_resolver.get_person_path(person)

            # TODO: 人物笔记目前仅在首次创建，不追踪后续出现记录。
            #       如需追踪视频出现历史，需改为类似 _update_price_level_note 的合并逻辑。
            if self.storage.exists(person_path):
                continue

            content = self.renderer.render_person_note(
                person=person,
                tickers_mentioned=tickers,
                videos_appeared=[video_id],
            )
            self.storage.write_file_safely(person_path, content, silent=True)
            print(f"  👤 创建人物: {person}")

    # ── 图谱索引 ──────────────────────────────────────────────────────────────

    def _update_graph_index(
        self, video_id: str, data: dict, tickers: list[str], people: list[str]
    ) -> None:
        metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
        mentioned_tickers = data.get("mentioned_tickers", []) if isinstance(data, dict) else []
        if not mentioned_tickers and tickers:
            mentioned_tickers = [{"ticker": t} for t in tickers]

        self.graph.upsert_video(
            video_id,
            mentioned_tickers=mentioned_tickers,
            people_mentioned=people,
            timestamp=metadata.get("date"),
        )

    # ── MOC ───────────────────────────────────────────────────────────────────

    def _generate_moc(self) -> None:
        today        = date.today().isoformat()
        storage_stats = self.storage.get_stats()

        # ── 聚合 ticker 统计 ──
        ticker_stats = []
        for tk, agg in self._moc_ticker_agg.items():
            sentiments = agg["sentiments"]
            bullish = sum(1 for s in sentiments if s in ("bullish", "very_bullish"))
            bearish = sum(1 for s in sentiments if s in ("bearish", "very_bearish"))
            if bullish > bearish:
                dominant = "bullish"
            elif bearish > bullish:
                dominant = "bearish"
            else:
                dominant = "neutral"
            ticker_stats.append({
                "ticker": tk,
                "count": agg["count"],
                "sentiment": dominant,
                "channels": sorted(agg["channels"]),
            })
        ticker_stats.sort(key=lambda x: x["count"], reverse=True)

        # ── 最新视频排序 ──
        recent_videos = sorted(
            self._moc_videos,
            key=lambda v: v.get("date", ""),
            reverse=True,
        )

        # ── 频道列表 ──
        channel_names = sorted({
            v.get("channel", "") for v in self._moc_videos if v.get("channel")
        })

        content = self.renderer.render_moc(
            stats={
                "processed": self.stats["processed"],
                "created":   storage_stats["filesWritten"],
                "updated":   storage_stats["filesUpdated"],
            },
            timestamp=today,
            ticker_stats=ticker_stats,
            recent_videos=recent_videos,
            channel_names=channel_names,
        )
        moc_path = self.path_resolver.get_moc_path()
        self.storage.write_file_safely(moc_path, content, silent=True)
        print("\n📋 已生成 MOC 仪表盘索引")

    # ── 统计输出 ──────────────────────────────────────────────────────────────

    def _print_stats(self) -> None:
        storage_stats = self.storage.get_stats()
        graph_stats   = self.graph.get_stats()

        print("\n" + "=" * 50)
        print("📊 同步完成统计")
        print("=" * 50)
        print(f"✅ 处理视频: {self.stats['processed']}")
        print(f"✨ 新建笔记: {storage_stats['filesWritten']}")
        print(f"🔄 更新笔记: {storage_stats['filesUpdated']}")
        print(f"📊 图谱索引:")
        print(f"   - 股票:     {graph_stats['tickers']}")
        print(f"   - 视频:     {graph_stats['videos']}")
        print(f"   - 人物:     {graph_stats['people']}")
        print(f"   - 反向链接: {graph_stats['backlinks']}")

        if self.stats["errors"]:
            print(f"\n❌ 错误: {len(self.stats['errors'])}")
            for e in self.stats["errors"]:
                print(f"   - {e.get('file') or e.get('phase')}: {e['error']}")

        print("=" * 50)


# ── CLI 入口 ─────────────────────────────────────────────────────────────────

def main() -> None:
    sync = ObsidianSync()
    try:
        sync.sync()
        if sync.stats["errors"]:
            print(f"\n❌ 同步完成但有 {len(sync.stats['errors'])} 个错误")
            sys.exit(1)
    except Exception as exc:
        print(f"\n❌ 同步失败: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
