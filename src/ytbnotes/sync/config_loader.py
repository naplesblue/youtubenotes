"""
lib/config/loader.py

统一配置加载器，支持 config.yaml + 环境变量覆盖。

配置优先级（高 → 低）：
  1. 运行时传入的 overrides 字典
  2. 环境变量 (OBSIDIAN_VAULT_DIR / ANALYSIS_OUTPUT_DIR / LOG_LEVEL)
  3. config.yaml / config.yml
  4. 内置默认值 DEFAULT_CONFIG
"""

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# ── .env 加载（模块导入时执行）────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

for _env_file in (_PROJECT_ROOT / ".env", _PROJECT_ROOT / ".env.local"):
    if _env_file.exists():
        load_dotenv(_env_file, override=True)

# ── 默认配置 ─────────────────────────────────────────────────────────────────
DEFAULT_CONFIG: dict = {
    "version": "1.0.0",
    "paths": {
        "root": "${PROJECT_ROOT}",
        "analysis_output": "./analysis_results",
        "vault": "./obsidian_vault",
        "folders": {
            "videos":         "02-视频笔记",
            "transcripts":    "05-完整转录",
            "price_levels":   "03-价格水平",
            "people":         "04-人物",
            "stock_overview": "01-股票概览",
            "index":          "00-MOC-索引",
            "templates":      "templates",
            "cache":          ".cache",
        },
    },
    "processing": {
        "max_concurrent":        3,
        "json_pattern":          "**/*.json",
        "exclude_patterns":      ["**/*_price_levels.json", "**/.cache/**"],
        "ticker_aliases":        {},
        "price_level_strategy":  "merge",
        "atomic_write":          True,
        "keep_backups":          False,
        "backup_retention_days": 7,
    },
    "notes": {
        "schema_version":              "1.0.0",
        "auto_tags":                   ["finance", "youtube-notes"],
        "auto_create_people":          True,
        "auto_create_stock_overview":  True,
        "enable_backlinks":            True,
    },
    "logging": {
        "level": "info",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 优先，不修改原对象。"""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _resolve_env_vars(obj: Any) -> Any:
    """递归解析 ${VAR} 和 ${VAR:-default} 占位符。"""
    if isinstance(obj, str):
        m = re.fullmatch(r"\$\{([^}]+)\}", obj)
        if m:
            inner = m.group(1)
            var_name, default = (inner.split(":-", 1) if ":-" in inner else (inner, obj))
            return os.environ.get(var_name, default)
        return obj
    if isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    return obj


class ConfigLoader:
    def __init__(self, config_path: str | Path | None = None):
        self._config_path = Path(config_path).resolve() if config_path else self._find_config()
        self._project_root = self._config_path.parent.resolve()
        self._config: dict | None = None

    def _find_config(self) -> Path:
        candidates = [
            Path("config.yaml"),
            Path("config.yml"),
            _PROJECT_ROOT / "config.yaml",
            _PROJECT_ROOT / "config.yml",
        ]
        for p in candidates:
            if p.exists():
                return p.resolve()
        return (_PROJECT_ROOT / "config.yaml").resolve()

    def _resolve_paths(self, config: dict) -> dict:
        config = copy.deepcopy(config)
        config.setdefault("paths", {})
        root = config["paths"].get("root", ".")
        if root == "${PROJECT_ROOT}":
            root = str(self._project_root)
        root = Path(root).resolve()
        config["paths"]["root"] = str(root)
        for key in ("analysis_output", "vault"):
            val = config["paths"].get(key)
            if val and not Path(val).is_absolute():
                config["paths"][key] = str((root / val).resolve())
        return config

    def _apply_env_overrides(self, config: dict) -> dict:
        env_map: dict[tuple, str] = {
            ("paths",   "vault"):           "OBSIDIAN_VAULT_DIR",
            ("paths",   "analysis_output"): "ANALYSIS_OUTPUT_DIR",
            ("logging", "level"):           "LOG_LEVEL",
        }
        for (section, key), env_var in env_map.items():
            value = os.environ.get(env_var)
            if value:
                if section == "paths" and not Path(value).is_absolute():
                    value = str(Path(value).resolve())
                config.setdefault(section, {})[key] = value
        return config

    def load(self) -> dict:
        config = copy.deepcopy(DEFAULT_CONFIG)
        if self._config_path.exists():
            with open(self._config_path, "r", encoding="utf-8") as f:
                file_config = yaml.safe_load(f) or {}
            config = _deep_merge(config, file_config)
        config = _resolve_env_vars(config)
        config = self._resolve_paths(config)
        config = self._apply_env_overrides(config)
        self._config = config
        return self._config

    @property
    def config(self) -> dict:
        if self._config is None:
            raise RuntimeError("配置尚未加载，请先调用 load()。")
        return self._config

    def get_path(self, key: str) -> str | None:
        return self.config.get("paths", {}).get(key)

    def get_folder(self, folder_type: str) -> str | None:
        return self.config.get("paths", {}).get("folders", {}).get(folder_type)

    def get_processing(self, key: str) -> Any:
        return self.config.get("processing", {}).get(key)

    def get_notes(self, key: str) -> Any:
        return self.config.get("notes", {}).get(key)

    def save(self) -> None:
        if self._config is None:
            raise RuntimeError("没有可保存的配置，请先调用 load()。")
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            yaml.dump(self._config, f, allow_unicode=True, indent=2, default_flow_style=False)
