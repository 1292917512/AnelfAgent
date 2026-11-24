"""文件工具 — 原子写入等通用文件操作。"""
from __future__ import annotations

import os
import stat
import tempfile
from _thread import RLock
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import BinaryIO, List, Tuple
from weakref import WeakValueDictionary

from core.log import log

_WRITE_LOCKS: WeakValueDictionary[str, RLock] = WeakValueDictionary()
_WRITE_LOCKS_GUARD = Lock()


def file_write_lock(target: Path) -> RLock:
    """返回同一规范路径共享的进程内写锁。"""
    key = os.path.normcase(str(target.resolve()))
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _WRITE_LOCKS[key] = lock
        return lock


def atomic_write_text(target: Path, content: str) -> None:
    """原子写入文本文件：先写临时文件，再 os.replace 避免并发写导致半截文件。"""
    atomic_write_bytes(target, content.encode("utf-8"))


def atomic_write_bytes(target: Path, content: bytes) -> None:
    """原子写入二进制内容并保留目标权限。"""
    with atomic_binary_writer(target) as file:
        file.write(content)


@contextmanager
def atomic_binary_writer(target: Path) -> Iterator[BinaryIO]:
    """锁定目标，在调用方完成写入后原子发布同目录临时文件。"""
    with file_write_lock(target):
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
        fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp", prefix=".atomic_")
        try:
            with os.fdopen(fd, "wb") as file:
                yield file
                file.flush()
                os.fsync(file.fileno())
            if mode is not None:
                os.chmod(tmp_path, mode)
            os.replace(tmp_path, str(target))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                log("原子写入临时文件清理失败", "DEBUG")
            raise


def walk_files(root: Path, skip_suffixes: Tuple[str, ...] = ()) -> List[Path]:
    """递归列出目录下全部文件（按路径排序，跳过指定后缀如 -wal/-shm 侧文件）。"""
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and not p.name.endswith(skip_suffixes)
    )


def directory_size(root: Path, skip_suffixes: Tuple[str, ...] = ()) -> int:
    """目录占用字节数（与 walk_files 同口径）。"""
    total = 0
    for path in walk_files(root, skip_suffixes):
        try:
            total += path.stat().st_size
        except OSError:
            log("directory_size 异常已忽略", "DEBUG")
    return total
