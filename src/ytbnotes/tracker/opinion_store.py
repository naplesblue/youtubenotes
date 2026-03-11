"""
Opinion 持久化存储

提供 opinions.json 的原子读写和幂等去重。
"""

import json
import logging
from pathlib import Path

from .models import Opinion

# 默认存储路径
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_OPINIONS_FILE = _PROJECT_DIR / "data" / "opinions" / "opinions.json"


def load_opinions(filepath: Path | None = None) -> list[Opinion]:
    """从 JSON 文件加载所有 opinion 记录。"""
    fp = Path(filepath or DEFAULT_OPINIONS_FILE)
    if not fp.exists():
        return []
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            logging.warning(f"opinions 文件格式异常，预期 list: {fp}")
            return []
        return [Opinion.from_dict(d) for d in raw if isinstance(d, dict)]
    except Exception as e:
        logging.error(f"加载 opinions 失败: {fp} — {e}")
        return []


def save_opinions(opinions: list[Opinion], filepath: Path | None = None) -> None:
    """原子写入 opinions 到 JSON 文件。"""
    fp = Path(filepath or DEFAULT_OPINIONS_FILE)
    fp.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        [o.to_dict() for o in opinions],
        ensure_ascii=False,
        indent=2,
    )
    # 原子写入：先写临时文件再 rename
    import tempfile, os
    tmp_fd, tmp_path = tempfile.mkstemp(dir=fp.parent, prefix=".tmp_opinions_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, fp)
        logging.info(f"opinions 已保存: {fp} ({len(opinions)} 条)")
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def upsert_opinions(
    new_opinions: list[Opinion],
    filepath: Path | None = None,
) -> dict:
    """
    幂等合并 opinions：按 opinion_id 去重。
    返回 {"added": int, "skipped": int, "total": int}。
    """
    existing = load_opinions(filepath)
    existing_ids = {o.opinion_id for o in existing}

    added = 0
    skipped = 0
    for op in new_opinions:
        if op.opinion_id in existing_ids:
            skipped += 1
            continue
        existing.append(op)
        existing_ids.add(op.opinion_id)
        added += 1

    if added > 0:
        save_opinions(existing, filepath)

    return {"added": added, "skipped": skipped, "total": len(existing)}
