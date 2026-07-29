"""文件工具 — 原子写入等通用文件操作。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

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
