"""安装前检查目标文件系统的用户可用容量，不扫描或清理已有数据。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TypedDict

REQUIREMENTS = json.loads(Path(__file__).with_name("install-requirements.json").read_text("utf-8"))


class Storage(TypedDict):
    path: str
    free: int | None
    required: int
    sufficient: bool
    error: str


def inspect_storage(root: Path) -> Storage:
    """未创建的安装目录使用最近已有父目录所在文件系统，不足或无法读取均不放行。"""
    required = int(REQUIREMENTS["project_free_bytes"])
    result: Storage = {"path": str(root), "free": None, "required": required, "sufficient": False, "error": ""}
    try:
        existing = root.resolve()
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        free = shutil.disk_usage(existing).free
        result["free"] = free
        result["sufficient"] = free >= required
    except OSError as exc:
        result["error"] = str(exc)
    return result


def require_storage(root: Path) -> None:
    """在源码下载、停服或修改环境前拒绝容量不足的安装。"""
    result = inspect_storage(root)
    if not result["sufficient"]:
        available = "unknown" if result["free"] is None else f"{result['free'] / 1024**3:.2f} GiB"
        raise RuntimeError(f"Insufficient disk space: {root}; available {available}, "
                           f"required {result['required'] / 1024**3:g} GiB. {result['error']}")
