"""
lib/core/storage_provider.py

原子化文件存储与 IO 操作。
对应 JS 版 StorageProvider.js。
"""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StorageStats:
    files_written: int = 0
    files_updated: int = 0
    backups_created: int = 0
    errors: list = field(default_factory=list)


class StorageProvider:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.path_resolver = cfg.get("pathResolver")
        self.atomic_write: bool = cfg.get("atomicWrite", True)
        self.enable_backup: bool = cfg.get("enableBackup", True)
        self.stats = StorageStats()

    # ── 写入 ─────────────────────────────────────────────────────────────────

    def write_file_safely(
        self,
        file_path: str | Path,
        content: str,
        *,
        silent: bool = False,
    ) -> bool:
        """
        原子写入文件。
        返回 True 表示新建，False 表示更新已有文件。
        """
        file_path = Path(file_path)
        exists = file_path.exists()

        file_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.atomic_write:
            file_path.write_text(content, encoding="utf-8")
            self._record_write(file_path, exists, silent)
            return not exists

        # 原子写入：临时文件 → os.replace
        backup_path: Path | None = None
        temp_fd, temp_path_str = tempfile.mkstemp(
            dir=file_path.parent, prefix=".tmp_", suffix=".tmp"
        )
        temp_path = Path(temp_path_str)

        try:
            # 备份现有文件
            if exists and self.enable_backup:
                import shutil
                backup_path = file_path.with_suffix(f".backup{file_path.suffix}")
                shutil.copy2(file_path, backup_path)
                self.stats.backups_created += 1

            # 写入临时文件
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                f.write(content)
            temp_fd = None  # 已关闭

            # 原子替换
            os.replace(temp_path, file_path)

            # 成功后删除备份
            if backup_path and backup_path.exists():
                backup_path.unlink(missing_ok=True)

            self._record_write(file_path, exists, silent)
            return not exists

        except Exception as exc:
            # 恢复备份
            if backup_path and backup_path.exists():
                try:
                    import shutil
                    shutil.move(str(backup_path), str(file_path))
                    print(f"  ⚠️  已恢复备份: {file_path.name}")
                except Exception as restore_exc:
                    print(f"  ❌ 恢复备份失败: {restore_exc}")

            # 清理临时文件
            if temp_fd is not None:
                try:
                    os.close(temp_fd)
                except OSError:
                    pass
            temp_path.unlink(missing_ok=True)

            self.stats.errors.append({"file": str(file_path), "error": str(exc)})
            raise

    def _record_write(self, file_path: Path, existed: bool, silent: bool) -> None:
        if existed:
            self.stats.files_updated += 1
            if not silent:
                print(f"  🔄 更新: {file_path.name}")
        else:
            self.stats.files_written += 1
            if not silent:
                print(f"  ✨ 创建: {file_path.name}")

    # ── 读取 ─────────────────────────────────────────────────────────────────

    def read_file(self, file_path: str | Path) -> str | None:
        file_path = Path(file_path)
        try:
            if file_path.exists():
                return file_path.read_text(encoding="utf-8")
            return None
        except Exception as exc:
            print(f"  ⚠️  读取失败 {file_path}: {exc}")
            return None

    def read_json(self, file_path: str | Path) -> dict | list | None:
        import json
        content = self.read_file(file_path)
        if content is None:
            return None
        try:
            return json.loads(content)
        except Exception as exc:
            print(f"  ⚠️  JSON 解析失败 {file_path}: {exc}")
            return None

    def write_json(self, file_path: str | Path, data: dict | list, *, silent: bool = False) -> bool:
        import json
        content = json.dumps(data, indent=2, ensure_ascii=False)
        return self.write_file_safely(file_path, content, silent=silent)

    # ── 目录与存在性 ──────────────────────────────────────────────────────────

    def ensure_dir(self, dir_path: str | Path) -> None:
        Path(dir_path).mkdir(parents=True, exist_ok=True)

    def exists(self, file_path: str | Path) -> bool:
        return Path(file_path).exists()

    # ── 统计 ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "filesWritten":   self.stats.files_written,
            "filesUpdated":   self.stats.files_updated,
            "backupsCreated": self.stats.backups_created,
            "errors":         list(self.stats.errors),
        }

    def reset_stats(self) -> None:
        self.stats = StorageStats()
