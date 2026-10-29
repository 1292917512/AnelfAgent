"""Persist file verification checkpoints without trusting changed source or destination files."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable

from app.migration_windows import change_time


def fingerprint(path: Path) -> str:
    info = path.lstat()
    return json.dumps([info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, change_time(path)])


def digest(path: Path, check: Callable[[], None]) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            check()
            result.update(chunk)
    return result.hexdigest()


class CopyCheckpoint:
    """Batch durable checkpoints; interrupted files are recopied and verified files are reused."""

    def __init__(self, path: Path) -> None:
        if path.is_symlink() or path.is_junction():
            raise RuntimeError("迁移进度文件被链接替换，请保留文件并检查。")
        self.connection = sqlite3.connect(path)
        self.connection.execute("CREATE TABLE IF NOT EXISTS files (name TEXT PRIMARY KEY, source TEXT, destination TEXT, digest TEXT, verified INTEGER)")
        self.connection.commit()
        self.updated = time.monotonic()
        self.pending = 0

    def close(self) -> None:
        try:
            self.connection.commit()
        finally:
            self.connection.close()

    def _flush(self) -> None:
        self.pending += 1
        if self.pending >= 256 or time.monotonic() - self.updated >= 1:
            self.connection.commit()
            self.updated, self.pending = time.monotonic(), 0

    def cached(self, name: str, source: Path, destination: Path) -> tuple[str, str, str, bool] | None:
        row = self.connection.execute("SELECT source, destination, digest, verified FROM files WHERE name = ?", (name,)).fetchone()
        if row:
            try:
                if row[0] == fingerprint(source) and row[1] == fingerprint(destination):
                    reliable = sys.platform != "win32" or all(json.loads(value)[-1] for value in row[:2])
                    return str(row[0]), str(row[1]), str(row[2]), bool(row[3]) and reliable
            except FileNotFoundError:
                return None
        return None

    def copy(self, name: str, source: Path, destination: Path, check: Callable[[], None], advance: Callable[[int], None]) -> None:
        initial = fingerprint(source)
        result = hashlib.sha256()
        # Forget stale success before touching a partial or changed destination.
        self.connection.execute("DELETE FROM files WHERE name = ?", (name,))
        if destination.exists():
            try:
                destination.unlink()
            except PermissionError:
                destination.chmod(destination.stat().st_mode | 0o200)
                destination.unlink()
        with source.open("rb") as reader, destination.open("xb") as writer:
            while chunk := reader.read(1024 * 1024):
                check()
                writer.write(chunk)
                result.update(chunk)
                advance(len(chunk))
        if fingerprint(source) != initial:
            raise RuntimeError(f"文件在迁移期间发生变化：{name}；原项目保持不变，已完成进度保留。")
        shutil.copystat(source, destination)
        self.connection.execute("INSERT INTO files VALUES (?, ?, ?, ?, 0)",
                                (name, initial, fingerprint(destination), result.hexdigest()))
        self._flush()

    def verify(self, name: str, source: Path, destination: Path, check: Callable[[], None]) -> bool:
        cached = self.cached(name, source, destination)
        if cached is None:
            raise RuntimeError(f"文件在迁移期间发生变化：{name}；已完成进度保留，请重试。")
        initial_source, initial_destination, expected, verified = cached
        if verified:
            return False
        if (digest(destination, check) != expected or digest(source, check) != expected
                or fingerprint(source) != initial_source or fingerprint(destination) != initial_destination):
            self.connection.execute("DELETE FROM files WHERE name = ?", (name,))
            raise RuntimeError(f"文件在迁移期间发生变化或校验失败：{name}；原项目保持不变，请重试。")
        self.connection.execute("UPDATE files SET verified = 1 WHERE name = ?", (name,))
        self._flush()
        return True
