"""宿主与独立启动器共享的纯标准库协议，不加载应用依赖。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import IO, Any


class HostLock:
    """跨进程非阻塞文件锁；锁文件保留，所有权随文件句柄释放。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> bool:
        if self._file is not None:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                if self.path.stat().st_size == 0:
                    file.write(b"\0")
                    file.flush()
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            return False
        self._file = file
        return True

    def release(self) -> None:
        file, self._file = self._file, None
        if file is not None:
            file.close()


def write_json(path: Path, value: Any) -> None:
    """同目录原子替换；有界等待短暂读句柄占用，失败仍保留完整旧值。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".host-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        for delay in (0.01, 0.02, 0.04, 0.08, 0.16, None):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if delay is None:
                    raise
                time.sleep(delay)
    finally:
        Path(temporary).unlink(missing_ok=True)
