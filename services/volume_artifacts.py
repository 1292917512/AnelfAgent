"""卷产物的分块 I/O、取消检查与校验；不修改运行中的源数据。"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")
CHUNK_SIZE = 1024 * 1024


class OperationCancelled(Exception):
    """在安全的 I/O 边界收到取消请求。"""


def check_cancel(state: dict[str, Any]) -> None:
    if state.get("cancel_requested"):
        raise OperationCancelled()


async def disk_job(job: Callable[[], T], state: dict[str, Any]) -> T:
    """取消协程时先停止并等待文件线程，避免线程继续写已清理的产物。"""
    task = asyncio.create_task(asyncio.to_thread(job))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        state["cancel_requested"] = True
        try:
            await task
        except OperationCancelled:
            pass
        raise


def digest(path: Path, state: dict[str, Any]) -> str:
    state.update(phase="verifying", done=0, total=path.stat().st_size, unit="bytes")
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK_SIZE):
            check_cancel(state)
            checksum.update(block)
            state["done"] += len(block)
    check_cancel(state)
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())
    return checksum.hexdigest()


def copy_artifact(source: Path, target: Path, state: dict[str, Any]) -> None:
    state.update(phase="copying", done=0, total=source.stat().st_size, unit="bytes")
    with source.open("rb") as src, target.open("xb") as dst:
        while block := src.read(CHUNK_SIZE):
            check_cancel(state)
            dst.write(block)
            state["done"] += len(block)
        dst.flush()
        os.fsync(dst.fileno())
    check_cancel(state)


class CancellableReader:
    """tarfile 每个压缩块都检查取消，单个大文件也能及时停止。"""

    def __init__(self, stream: Any, state: dict[str, Any]) -> None:
        self.stream = stream
        self.state = state

    def read(self, size: int = -1) -> bytes:
        check_cancel(self.state)
        block: bytes = self.stream.read(size)
        self.state["done"] = self.state.get("done", 0) + len(block)
        return block
