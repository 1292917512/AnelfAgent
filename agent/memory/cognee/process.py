"""有界、可取消的 Cognee 子进程 RPC；原生崩溃不进入宿主地址空间。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from typing import Any

from core.path import project_root

from .types import CogneePipelineError
from .wire import encode


class WorkerCrashed(RuntimeError):
    """原生工作进程异常退出；恢复候选索引后才允许后续调用。"""


class CogneeProcess:
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._sequence = 0
        self._write_lock = asyncio.Lock()
        self.written_bytes = 0

    def sample_writes(self) -> int:
        import psutil

        if self.process and self.process.returncode is None:
            try:
                self.written_bytes = max(self.written_bytes, psutil.Process(self.process.pid).io_counters().write_bytes)
            except psutil.NoSuchProcess:
                pass
        return self.written_bytes

    async def start(self, config: dict[str, Any], chat: dict[str, Any], embedding: dict[str, Any]) -> Any:
        options: dict[str, Any] = {}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-c",
            "import os,sys; channel=os.fdopen(os.dup(1),'w',encoding='utf-8'); "
            "os.dup2(2,1); sys.stdout=sys.stderr; "
            "from agent.memory.cognee.worker import main; main(channel)",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=64 * 1024 * 1024, cwd=project_root(), **options,
        )
        self._reader = asyncio.create_task(self._read(), name="memory.cognee.rpc")
        return await self.call("initialize", config=config, chat=chat, embedding=embedding, timeout=120)

    async def _read(self) -> None:
        process = self.process
        assert process is not None and process.stdout is not None
        try:
            while line := await process.stdout.readline():
                response = json.loads(line)
                self.written_bytes = max(self.written_bytes, response.get("written_bytes", 0))
                future = self._pending.get(response["id"])
                if future is None or future.done():
                    continue
                if "error" in response:
                    future.set_exception(CogneePipelineError("worker", response.get("error_class", ""), response["error"]))
                else:
                    future.set_result(response.get("result"))
            code = await process.wait()
            error: Exception = WorkerCrashed(f"Cognee 工作进程退出 (exit_code={code})；未提交批次将恢复后重试")
        except Exception as exc:
            error = WorkerCrashed(f"Cognee 工作进程协议中断 ({type(exc).__name__})")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)

    async def call(self, method: str, *args: Any, timeout: float = 30, **kwargs: Any) -> Any:
        process = self.process
        if process is None or process.returncode is not None or process.stdin is None:
            raise WorkerCrashed("Cognee 工作进程未运行")
        self._sequence += 1
        request_id = self._sequence
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            message = json.dumps({"id": request_id, "method": method, "args": encode(args), "kwargs": encode(kwargs)})
            async with self._write_lock:
                process.stdin.write((message + "\n").encode())
                await process.stdin.drain()
            async with asyncio.timeout(timeout):
                return await future
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def stop(self, *, clean: bool = False) -> None:
        """发布要求原生关闭成功；取消时允许直接终止可丢弃工作区。"""
        process = self.process
        if process is None:
            return
        try:
            if clean:
                await self.call("close", timeout=30)
                async with asyncio.timeout(10):
                    code = await process.wait()
                if code != 0:
                    raise WorkerCrashed(f"Cognee 关闭失败 (exit_code={code})，候选索引未提交")
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
            if process.stdin:
                process.stdin.close()
                try:
                    await process.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            if self._reader:
                await self._reader
            self.process = None
