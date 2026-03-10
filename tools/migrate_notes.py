#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性迁移脚本：将旧版 Obsidian 视频笔记整理为新结构

目标：
1) 简报按频道分目录，并带日期前缀
2) 完整转录单独落到 transcripts 目录（同样按频道分目录）
3) 简报与完整转录建立双向链接
4) 旧版平铺笔记移入备份目录

默认 dry-run，不写入文件。
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from lib.config.loader import ConfigLoader
from lib.config.path_resolver import PathResolver
from obsidian_sync import ObsidianSync


@dataclass
class LegacyNote:
    path: Path
    video_id: str


def parse_front_matter(content: str) -> dict:
    if not content:
        return {}
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}
    raw = "\n".join(lines[1:end_idx]).strip()
    if not raw:
        return {}
    try:
        data = yaml.safe_load(raw) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def extract_video_id_from_note(note_path: Path) -> Optional[str]:
    try:
        content = note_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    fm = parse_front_matter(content)
    video_id = fm.get("id")
    if video_id:
        return str(video_id).strip()
    source = fm.get("source", {})
    if isinstance(source, dict):
        video_id = source.get("video_id")
        if video_id:
            return str(video_id).strip()
    return None


def discover_legacy_flat_notes(video_root: Path) -> list[LegacyNote]:
    notes: list[LegacyNote] = []
    if not video_root.exists():
        return notes
    for md in sorted(video_root.glob("*.md")):
        vid = extract_video_id_from_note(md)
        if not vid:
            continue
        notes.append(LegacyNote(path=md, video_id=vid))
    return notes


def build_analysis_json_index(analysis_output: Path) -> dict[str, Path]:
    idx: dict[str, Path] = {}
    if not analysis_output.exists():
        return idx
    for p in analysis_output.rglob("*.json"):
        name = p.name
        if name.endswith("_price_levels.json") or name.endswith("_levels.json"):
            continue
        idx.setdefault(p.stem, p)
    return idx


def read_json_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def compute_target_paths(path_resolver: PathResolver, json_path: Path) -> tuple[Path, Path]:
    data = read_json_file(json_path)
    meta = data.get("metadata", {}) if isinstance(data, dict) else {}
    vid = str(meta.get("video_id") or json_path.stem)
    title = meta.get("title")
    channel = meta.get("channel")
    published = meta.get("date")
    video_path = path_resolver.get_video_note_path(
        video_id=vid,
        title=title,
        channel_name=channel,
        published_date=published,
    )
    transcript_path = path_resolver.get_transcript_note_path(
        video_id=vid,
        title=title,
        channel_name=channel,
        published_date=published,
    )
    return video_path, transcript_path


def migrate_notes(
    *,
    config_path: str,
    apply: bool,
) -> int:
    loader = ConfigLoader(config_path)
    cfg = loader.load()
    path_resolver = PathResolver(cfg)

    video_root = path_resolver.get_folder("videos")
    analysis_output = path_resolver.get_analysis_output()
    legacy_notes = discover_legacy_flat_notes(video_root)
    json_index = build_analysis_json_index(analysis_output)

    print(f"Vault: {path_resolver.get_vault_root()}")
    print(f"旧版平铺视频笔记数: {len(legacy_notes)}")
    print(f"可用分析 JSON 数: {len(json_index)}")

    if not legacy_notes:
        print("没有发现需要迁移的旧版平铺视频笔记。")
        return 0

    sync = None
    backup_root = None
    if apply:
        sync = ObsidianSync(config_path=config_path)
        sync.initialize()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root = video_root / "_legacy_flat_backup" / ts
        backup_root.mkdir(parents=True, exist_ok=True)
        print(f"备份目录: {backup_root}")

    migrated = 0
    skipped_missing_json = 0
    skipped_error = 0

    for item in legacy_notes:
        json_path = json_index.get(item.video_id)
        if not json_path:
            print(f"[SKIP] 缺少 JSON: {item.path.name} (video_id={item.video_id})")
            skipped_missing_json += 1
            continue

        target_video, target_transcript = compute_target_paths(path_resolver, json_path)
        print(f"[PLAN] {item.path.name}")
        print(f"       -> 简报: {target_video}")
        print(f"       -> 转录: {target_transcript}")

        if not apply:
            migrated += 1
            continue

        try:
            video_id, data = sync.parser.parse_file(json_path)
            sync._generate_linked_video_and_transcript_notes(video_id, data, json_path)

            backup_path = backup_root / item.path.name
            shutil.move(str(item.path), str(backup_path))
            print(f"[DONE] 已迁移并备份旧笔记 -> {backup_path}")
            migrated += 1
        except Exception as e:
            print(f"[ERR ] 迁移失败 {item.path.name}: {e}")
            skipped_error += 1

    mode = "APPLY" if apply else "DRY-RUN"
    print("\n--- 迁移统计 ---")
    print(f"模式: {mode}")
    print(f"计划/完成迁移: {migrated}")
    print(f"缺少 JSON 跳过: {skipped_missing_json}")
    print(f"错误跳过: {skipped_error}")

    return 0 if skipped_error == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="将旧版 Obsidian 平铺视频笔记迁移到新结构（频道分目录 + 简报/转录分离）。"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径（默认: config.yaml）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行实际迁移（默认仅 dry-run 预览）。",
    )
    args = parser.parse_args()

    return migrate_notes(config_path=args.config, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())

