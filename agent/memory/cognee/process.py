"""有界、可取消的 Cognee 子进程 RPC；原生崩溃不进入宿主地址空间。"""

from __future__ import annotations

import asyncio
import codecs
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

from core.log import log
from core.path import project_root

from .diagnostics import WorkerDiagnostics, credential_values
from .query_scope import current_query
from .types import CogneePipelineError
from .wire import encode

PROTOCOL_VERSION = 1


class WorkerCrashed(RuntimeError):
    """原生工作进程异常退出；恢复候选索引后才允许后续调用。"""


class CogneeProcess:
    def __init__(self, *, diagnostic_path: Path | None = None, commit: str = "") -> None:
        self.process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._sequence = 0
        self._write_lock = asyncio.Lock()
        self.written_bytes = 0
        self._diagnostic_path = diagnostic_path
        self._commit = commit
        self._diagnostics: WorkerDiagnostics | None = None
        self._stderr_reader: asyncio.Task[None] | None = None
        self._owns_group = False
        self._stop_lock = asyncio.Lock()

    def sample_writes(self) -> int:
        if self.process and self.process.returncode is None:
            try:
                self.written_bytes = max(self.written_bytes, psutil.Process(self.process.pid).io_counters().write_bytes)
            except (AttributeError, psutil.Error):
                pass
        return self.written_bytes

    async def start(self, config: dict[str, Any], chat: dict[str, Any], embedding: dict[str, Any]) -> Any:
        if self._diagnostic_path is not None:
            self._diagnostics = WorkerDiagnostics(self._diagnostic_path,
                credential_values(config) | credential_values(chat) | credential_values(embedding))
        options: dict[str, Any] = {}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-c",
            "import os,sys; channel=os.fdopen(os.dup(1),'w',encoding='utf-8'); "
            "os.dup2(2,1); sys.stdout=sys.stderr; "
            "from agent.memory.cognee.worker import main; main(channel)",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024, cwd=project_root(), **options,
        )
        self._owns_group = sys.platform != "win32"
        self._stderr_reader = asyncio.create_task(self._read_stderr(), name="memory.cognee.diagnostics")
        if self._diagnostics:
            self._diagnostics.write(f"worker pid={self.process.pid} commit={self._commit}")
        self._reader = asyncio.create_task(self._read(), name="memory.cognee.rpc")
        return await self.call("initialize", config=config, chat=chat, embedding=embedding, timeout=120)

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending, oversized = "", False
        while chunk := await self.process.stderr.read(4096):
            pending += decoder.decode(chunk)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                if self._diagnostics:
                    self._diagnostics.write("[oversized diagnostic omitted]" if oversized or len(line) > 16384 else line)
                oversized = False
            if len(pending) > 16384:
                pending, oversized = "", True
        if self._diagnostics and pending:
            self._diagnostics.write("[oversized diagnostic omitted]" if oversized else pending)

    async def _read(self) -> None:
        process = self.process
        assert process is not None and process.stdout is not None
        try:
            while line := await process.stdout.readline():
                response = json.loads(line)
                if response.get("protocol") != PROTOCOL_VERSION:
                    raise RuntimeError("Cognee RPC 协议版本不匹配")
                self.written_bytes = max(self.written_bytes, response.get("written_bytes", 0))
                future = self._pending.get(response["id"])
                if future is None or future.done():
                    continue
                if "error" in response:
                    future.set_exception(CogneePipelineError("worker", response.get("error_class", ""), response["error"]))
                else:
                    future.set_result(response.get("result"))
            code = await self._wait_exit(process, timeout=10)
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
        started = time.monotonic()
        try:
            scope = current_query.get() if method == "recall" else None
            if scope is not None:
                scope.releases[id(self)] = lambda: self.call("release_query", scope.identity, timeout=2)
            message = json.dumps({"protocol": PROTOCOL_VERSION, "id": request_id, "method": method,
                                  "query_scope": scope.identity if scope else "",
                                  "args": encode(args), "kwargs": encode(kwargs)})
            async with asyncio.timeout(timeout):
                async with self._write_lock:
                    process.stdin.write((message + "\n").encode())
                    await process.stdin.drain()
                return await future
        finally:
            if self._diagnostics and method != "pipeline_progress":
                self._diagnostics.write(f"rpc id={request_id} method={method} elapsed={time.monotonic() - started:.2f}s")
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def stop(self, *, clean: bool = False) -> None:
        """回收专用进程树；取消不能遗失句柄，退出与管道回收分别有界。"""
        async with self._stop_lock:
            await self._stop_owned(clean=clean)

    def _children(self, pid: int) -> list[psutil.Process]:
        try:
            return psutil.Process(pid).children(recursive=True)
        except psutil.NoSuchProcess:
            return []

    @staticmethod
    async def _wait_exit(process: asyncio.subprocess.Process, *, timeout: float) -> int:
        """只等 OS 退出通知；Process.wait 还依赖所有继承的管道关闭。"""
        async with asyncio.timeout(timeout):
            while process.returncode is None:
                await asyncio.sleep(0.02)
        return process.returncode

    async def _stop_owned(self, *, clean: bool) -> None:
        process = self.process
        if process is None:
            return
        # 正常关闭前记住辅助进程，避免父进程退出后 Windows 无法再追溯孤儿。
        children = self._children(process.pid)
        try:
            if clean:
                await self.call("close", timeout=30)
                code = await self._wait_exit(process, timeout=10)
                if code != 0:
                    raise WorkerCrashed(f"Cognee 关闭失败 (exit_code={code})，候选索引未提交")
        finally:
            cleanup = asyncio.create_task(self._reap(process, children), name="memory.cognee.reap")
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
                cleanup.result()
                raise

    async def _reap(self, process: asyncio.subprocess.Process, children: list[psutil.Process]) -> None:
        self.sample_writes()
        children = list({child.pid: child for child in [*children, *self._children(process.pid)]}.values())
        log(f"Cognee 回收工作进程 PID={process.pid}，辅助进程 {len(children)} 个", "DEBUG", tag="关停")
        if self._owns_group and sys.platform != "win32":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for child in reversed(children):
            try:
                child.kill()  # psutil 验证创建时间，不向复用 PID 发信号。
            except psutil.NoSuchProcess:
                pass
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await self._wait_exit(process, timeout=5)
            if children:
                _, alive = await asyncio.to_thread(psutil.wait_procs, children, timeout=5)
                if alive:
                    raise WorkerCrashed(f"Cognee 辅助进程未退出: {[child.pid for child in alive]}；保留所有权")
            async with asyncio.timeout(5):
                if process.stdin:
                    process.stdin.close()
                    try:
                        await process.stdin.wait_closed()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                readers = [task for task in (self._reader, self._stderr_reader) if task]
                results = await asyncio.gather(*readers, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        log(f"Cognee 管道回收: {type(result).__name__}", "DEBUG", tag="关停")
                await process.wait()
        except TimeoutError as exc:
            raise WorkerCrashed(f"Cognee 进程或管道回收超时: PID={process.pid}, exit_code={process.returncode}；"
                                "禁止发布或释放索引所有权") from exc
        if self._diagnostics:
            self._diagnostics.write(f"worker exit_code={process.returncode}")
            self._diagnostics.close()
        self.process = None
