"""脚本钩子的可取消子进程，限制输出驻留量并回收进程树。"""

from __future__ import annotations

import asyncio
import os

from core.command import CommandResult, decode_command_output
from core.process import managed_process
from core.shell_env import shell_env_defaults


async def _read(stream: asyncio.StreamReader | None) -> bytes:
    if stream is None:
        return b""
    kept = bytearray()
    while chunk := await stream.read(8192):
        if len(kept) < 16384:
            kept.extend(chunk[:16384 - len(kept)])
    return bytes(kept)


async def run_hook_command(command: str, *, timeout: float, env: dict[str, str], stdin: str) -> CommandResult:
    """执行脚本并返回有界输出，超时或取消时终止所创建的进程树。"""
    process_env = {**os.environ, **env}
    for key, value in shell_env_defaults().items():
        process_env.setdefault(key, value)
    async with managed_process(
        command, shell=True, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=process_env,
    ) as proc:
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
            for task in [*tasks, writer]:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, writer, return_exceptions=True)
