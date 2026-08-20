"""文件工具 — 原子写入等通用文件操作。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import List, Tuple

from core.log import log


def atomic_write_text(target: Path, content: str) -> None:
    """原子写入文本文件：先写临时文件，再 os.replace 避免并发写导致半截文件。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), suffix=".tmp", prefix=".atomic_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(target))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            log("atomic_write_text 异常已忽略", "DEBUG")
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
