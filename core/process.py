"""异步子进程资源边界：调用方决定预算与输出策略，退出前回收拥有的进程树。"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TypeVar

T = TypeVar("T")


async def terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """回收由独立进程组创建的子进程，等待退出后再释放流和产物。"""
    try:
        if proc.returncode is not None:
            pass
        elif sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                await asyncio.wait_for(killer.wait(), 5)
            except TimeoutError:
                killer.kill()
                await killer.wait()
            if proc.returncode is None:
                proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        if proc.returncode is None:
            proc.kill()
    async def discard(stream: asyncio.StreamReader | None) -> None:
        if stream is not None:
            while await stream.read(65536):
                pass

    await asyncio.gather(proc.wait(), discard(proc.stdout), discard(proc.stderr))


async def _finish_owned(task: asyncio.Task[T]) -> T:
    """重复取消仍先收束已拥有的资源，随后向调用方传播取消。"""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


@asynccontextmanager
async def managed_process(*command: str, shell: bool = False, **kwargs: Any) -> AsyncIterator[asyncio.subprocess.Process]:
    """独立进程组与隐藏窗口；创建、运行或读取被取消时均完成资源回收。"""
    kwargs["start_new_session"] = os.name != "nt"
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if shell:
        if len(command) != 1:
            raise ValueError("Shell 执行需要一条完整命令")
        creation = asyncio.create_task(asyncio.create_subprocess_shell(command[0], **kwargs))
    else:
        creation = asyncio.create_task(asyncio.create_subprocess_exec(*command, **kwargs))
    try:
        proc = await asyncio.shield(creation)
    except asyncio.CancelledError:
        async def reclaim_creation() -> None:
            child = await creation
            await terminate_process_tree(child)

        await _finish_owned(asyncio.create_task(reclaim_creation()))
        raise
    try:
        yield proc
    finally:
        await _finish_owned(asyncio.create_task(terminate_process_tree(proc)))
