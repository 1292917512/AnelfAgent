"""脚本钩子的可取消子进程，限制输出驻留量并回收进程树。"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys

from core.command import CommandResult, decode_command_output
from core.shell_env import shell_env_defaults


async def _read(stream: asyncio.StreamReader | None) -> bytes:
    if stream is None:
        return b""
    kept = bytearray()
    while chunk := await stream.read(8192):
        if len(kept) < 16384:
            kept.extend(chunk[:16384 - len(kept)])
    return bytes(kept)


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                await asyncio.wait_for(killer.wait(), timeout=5)
            except TimeoutError:
                killer.kill()
                await killer.wait()
            if proc.returncode is None:
                proc.kill()
        else:
            killpg = os.killpg
            killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        if proc.returncode is None:
            proc.kill()
    await proc.wait()


async def run_hook_command(command: str, *, timeout: float, env: dict[str, str], stdin: str) -> CommandResult:
    """执行脚本并返回有界输出，超时或取消时终止所创建的进程树。"""
    process_env = {**os.environ, **env}
    for key, value in shell_env_defaults().items():
        process_env.setdefault(key, value)
    proc = await asyncio.create_subprocess_shell(
        command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=process_env,
        start_new_session=os.name != "nt",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )

    async def feed() -> None:
        if proc.stdin is not None:
            try:
                proc.stdin.write(stdin.encode("utf-8"))
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()

    tasks = [asyncio.create_task(_read(proc.stdout)), asyncio.create_task(_read(proc.stderr))]
    writer = asyncio.create_task(feed())
    try:
        async with asyncio.timeout(timeout):
            await proc.wait()
            stdout, stderr = await asyncio.gather(*tasks)
            await writer
        return CommandResult(proc.returncode == 0, decode_command_output(stdout),
                             decode_command_output(stderr), proc.returncode)
    except TimeoutError:
        return CommandResult(False, "", f"脚本超时（{timeout:g}s）")
    finally:
        await _terminate(proc)
        for task in [*tasks, writer]:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, writer, return_exceptions=True)
