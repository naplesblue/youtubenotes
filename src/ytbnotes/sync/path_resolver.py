"""
lib/config/path_resolver.py

路径抽象层，完全消除硬编码路径。
对应 JS 版 PathResolver.js。
"""

import re
from datetime import date
from pathlib import Path

DEFAULT_FOLDERS: dict[str, str] = {
    "videos":         "02-视频笔记",
    "transcripts":    "05-完整转录",
    "price_levels":   "03-价格水平",
    "people":         "04-人物",
    "stock_overview": "01-股票概览",
    "index":          "00-MOC-索引",
    "templates":      "templates",
    "cache":          ".cache",
}

# Ticker 白名单：大写字母、数字、点、下划线、连字符，1-20 字符
_TICKER_RE = re.compile(r"^[A-Z0-9._-]{1,20}$")


class PathResolver:
    def __init__(self, config: dict):
        self._config = config
        self._vault_root = Path(config["paths"]["vault"])
        self._analysis_output = Path(config["paths"]["analysis_output"])
        self._folder_map: dict[str, str] = config.get("paths", {}).get("folders", {})

    # ── 核心路径 ──────────────────────────────────────────────────────────────

    def get_vault_root(self) -> Path:
        return self._vault_root

    def get_analysis_output(self) -> Path:
        return self._analysis_output

    def get_project_root(self) -> Path:
        return Path(self._config.get("paths", {}).get("root", "."))

    # ── 子目录路由 ────────────────────────────────────────────────────────────

    def get_folder_name(self, internal_type: str) -> str:
        return self._folder_map.get(internal_type) or DEFAULT_FOLDERS.get(internal_type, internal_type)

    def get_folder(self, internal_type: str) -> Path:
        return self._vault_root / self.get_folder_name(internal_type)

    def get_all_folder_mappings(self) -> dict[str, str]:
        return {**DEFAULT_FOLDERS, **self._folder_map}

    # ── 笔记路径生成 ──────────────────────────────────────────────────────────

    def _sanitize_note_filename(self, name: str) -> str:
        safe = re.sub(r'[\\/:*?"<>|]', "-", (name or "").strip())
        safe = re.sub(r"\s+", " ", safe).strip(" .")
        return safe[:180]

    def _sanitize_folder_segment(self, name: str) -> str:
        safe = self._sanitize_note_filename(name or "未知频道")
        return safe or "未知频道"

    def _format_date_prefix(self, published_date: str | None) -> str:
        raw = str(published_date or "").strip()
        if raw:
            m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw)
            if m:
                return f"{m.group(1)}{m.group(2)}{m.group(3)}"
            m = re.match(r"^(\d{8})$", raw)
            if m:
                return m.group(1)
        return date.today().strftime("%Y%m%d")

    def _build_note_stem(
        self,
        *,
        video_id: str,
        title: str | None,
        published_date: str | None,
    ) -> str:
        base_name = self._sanitize_note_filename(title or "")
        if not base_name:
            base_name = video_id
        prefix = self._format_date_prefix(published_date)
        return f"{prefix} - {base_name}"

    def get_video_note_path(
        self,
        *,
        video_id: str,
        title: str | None = None,
        channel_name: str | None = None,
        published_date: str | None = None,
    ) -> Path:
        channel_segment = self._sanitize_folder_segment(channel_name or "未知频道")
        stem = self._build_note_stem(
            video_id=video_id,
            title=title,
            published_date=published_date,
        )
        result = self.get_folder("videos") / channel_segment / f"{stem}.md"
        self._ensure_in_vault(result)
        return result

    def get_transcript_note_path(
        self,
        *,
        video_id: str,
        title: str | None = None,
        channel_name: str | None = None,
        published_date: str | None = None,
    ) -> Path:
        channel_segment = self._sanitize_folder_segment(channel_name or "未知频道")
        stem = self._build_note_stem(
            video_id=video_id,
            title=f"{title or video_id} [{video_id}]",
            published_date=published_date,
        )
        result = self.get_folder("transcripts") / channel_segment / f"{stem}.md"
        self._ensure_in_vault(result)
        return result

    def get_price_level_path(self, ticker: str) -> Path:
        self._validate_ticker(ticker)
        result = self.get_folder("price_levels") / f"{ticker}_levels.md"
        self._ensure_in_vault(result)
        return result

    def get_price_level_json_path(self, ticker: str) -> Path:
        """点位历史数据的独立 JSON 文件路径（优化：替代从 Markdown 反向解析）。"""
        self._validate_ticker(ticker)
        result = self.get_folder("price_levels") / f"{ticker}_levels.json"
        self._ensure_in_vault(result)
        return result

    def get_person_path(self, person_name: str) -> Path:
        safe = re.sub(r'[\\/:*?"<>|]', "-", person_name)
        return self.get_folder("people") / f"{safe}.md"

    def get_stock_overview_path(self, ticker: str) -> Path:
        self._validate_ticker(ticker)
        result = self.get_folder("stock_overview") / f"{ticker}.md"
        self._ensure_in_vault(result)
        return result

    def get_stock_overview_json_path(self, ticker: str) -> Path:
        """股票概览的持久化 JSON 数据路径（用于时间线增量合并）。"""
        self._validate_ticker(ticker)
        result = self.get_folder("stock_overview") / f"{ticker}_overview.json"
        self._ensure_in_vault(result)
        return result

    def get_moc_path(self) -> Path:
        return self.get_folder("index") / "00-MOC-视频分析索引.md"

    def get_graph_index_path(self) -> Path:
        return self.get_folder("index") / "graph-index.json"

    # ── 辅助 ─────────────────────────────────────────────────────────────────

    def _validate_ticker(self, ticker: str) -> None:
        if not _TICKER_RE.match(ticker):
            raise ValueError(
                f'非法 ticker "{ticker}"：只允许大写字母、数字、点、下划线、连字符（1-20字符）。'
            )

    def _ensure_in_vault(self, target: Path) -> None:
        vault = self._vault_root.resolve()
        resolved = target.resolve()
        if resolved != vault and not str(resolved).startswith(str(vault) + "/"):
            raise ValueError(
                f'路径越界："{target}" 解析为 "{resolved}"，超出 Vault 边界 "{vault}"。'
            )

    def is_in_vault(self, path: Path) -> bool:
        try:
            self._ensure_in_vault(path)
            return True
        except ValueError:
            return False

    def relative(self, abs_path: Path, base: Path | None = None) -> Path:
        return abs_path.relative_to(base or self.get_project_root())

    def get_path_summary(self) -> dict:
        return {
            "vault_root":       str(self._vault_root),
            "analysis_output":  str(self._analysis_output),
            "project_root":     str(self.get_project_root()),
            "folders": {k: str(self.get_folder(k)) for k in DEFAULT_FOLDERS},
        }
