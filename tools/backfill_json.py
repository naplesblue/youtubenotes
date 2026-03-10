#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 analysis_results 历史 Markdown 回填结构化 JSON：
- brief_text   <- 【精炼文本】全量
- summary      <- 与 brief_text 保持一致（兼容旧字段）
- raw_transcript <- 【完整转录】时间线全文

默认 dry-run，仅打印统计；加 --apply 才会写入。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


REFINED_HEADING = "# 【精炼文本】"
TRANSCRIPT_HEADING = "# 【完整转录 (带内部时间戳)】"


def parse_front_matter(content: str) -> dict:
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

    result = {}
    for ln in raw.splitlines():
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def extract_section(text: str, start_heading: str, end_candidates: list[str]) -> str:
    start = text.find(start_heading)
    if start < 0:
        return ""
    start = text.find("\n", start)
    if start < 0:
        return ""
    start += 1
    end = len(text)
    for marker in end_candidates:
        idx = text.find(marker, start)
        if idx >= 0:
            end = min(end, idx)
    return text[start:end].strip()


def normalize_transcript(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    ts_re = re.compile(r"^\[\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\]")
    start_idx = None
    for i, ln in enumerate(lines):
        if ts_re.match(ln.strip()):
            start_idx = i
            break
    if start_idx is None:
        return text
    return "\n".join(lines[start_idx:]).strip()


def extract_refined_text(markdown_text: str) -> str:
    return extract_section(
        markdown_text,
        REFINED_HEADING,
        [
            "# 【关键信息摘要（含时间戳）】",
            "# 【原子化点位概览】",
            "# 【原子化点位数据",
        ],
    ).strip()


def extract_transcript(markdown_text: str) -> str:
    # 新格式：<details> + <br> + transcript + </details>
    pattern = r"#\s*【完整转录\s*\(带内部时间戳\)】.*?<details>.*?<br>\s*(.*?)\s*</details>"
    m = re.search(pattern, markdown_text, flags=re.DOTALL)
    if m:
        return normalize_transcript(m.group(1).strip())

    # 旧格式：直接写在 heading 后，直到 “精炼文本” heading
    body = extract_section(
        markdown_text,
        TRANSCRIPT_HEADING,
        [REFINED_HEADING],
    )
    return normalize_transcript(body)


def choose_source_markdown(md_files: list[Path], video_id: str) -> Path | None:
    if not md_files:
        return None

    scored = []
    for p in md_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        fm = parse_front_matter(text)
        score = 0
        if fm.get("video_id") == video_id:
            score += 100
        if REFINED_HEADING in text:
            score += 20
        if TRANSCRIPT_HEADING in text:
            score += 10
        name = p.name.lower()
        if "_enhanced_" in name or "_context" in name:
            score -= 10
        scored.append((score, p))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def iter_target_json_files(root: Path):
    for p in root.rglob("*.json"):
        name = p.name
        if name.endswith("_price_levels.json") or name.endswith("_levels.json"):
            continue
        yield p


def backfill(root: Path, apply: bool) -> int:
    total = 0
    updated = 0
    skip_no_md = 0
    skip_no_refined = 0
    with_transcript = 0

    for json_path in iter_target_json_files(root):
        total += 1
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue

        video_id = str((data.get("metadata") or {}).get("video_id") or json_path.stem)
        md_files = sorted(json_path.parent.glob("*.md"))
        src_md = choose_source_markdown(md_files, video_id)
        if not src_md:
            skip_no_md += 1
            continue

        text = src_md.read_text(encoding="utf-8", errors="ignore")
        refined = extract_refined_text(text)
        transcript = extract_transcript(text)

        if not refined:
            skip_no_refined += 1
            continue

        changed = False
        if data.get("brief_text") != refined:
            data["brief_text"] = refined
            changed = True
        if data.get("summary") != refined:
            data["summary"] = refined
            changed = True
        if transcript:
            with_transcript += 1
            if data.get("raw_transcript") != transcript:
                data["raw_transcript"] = transcript
                changed = True

        if changed:
            updated += 1
            if apply:
                json_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"mode={mode}")
    print(f"total_json={total}")
    print(f"updated={updated}")
    print(f"with_transcript={with_transcript}")
    print(f"skip_no_markdown={skip_no_md}")
    print(f"skip_no_refined={skip_no_refined}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 analysis_results JSON 的 brief_text/raw_transcript 字段。")
    parser.add_argument("--root", default="analysis_results", help="分析结果根目录（默认 analysis_results）")
    parser.add_argument("--apply", action="store_true", help="执行写入；默认 dry-run。")
    args = parser.parse_args()
    return backfill(Path(args.root), args.apply)


if __name__ == "__main__":
    raise SystemExit(main())

