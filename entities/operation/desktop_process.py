"""桌面工作进程的 JSON 行协议与资源收束；不持有业务控制状态。"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.process import managed_process

WORKER = Path(__file__).with_name("desktop_worker.py")


async def run_worker(
    action: str, params: dict[str, Any], *, stop: asyncio.Event,
    update: Callable[[dict[str, Any]], None], takeover: bool = True,
) -> dict[str, Any]:
    """协作取消留一秒释放输入；失去响应时回收整个子进程，结果标为未知。"""
    result: dict[str, Any] = {"status": "unknown", "input_released": False}
    if stop.is_set():
        return {"status": "not_started", "input_released": True}
    async with managed_process(
        sys.executable, "-u", str(WORKER), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=65536,
    ) as proc:
        assert proc.stdin is not None and proc.stdout is not None
        update({"pid": proc.pid})
        if stop.is_set():
            return {"status": "not_started", "input_released": True}
        proc.stdin.write((json.dumps({"action": action, "params": params, "takeover": takeover}) + "\n").encode())
        await proc.stdin.drain()

        async def read() -> None:
            assert proc.stdout is not None
            while line := await proc.stdout.readline():
                data = json.loads(line)
                update(data)
                if data.get("event") == "result":
                    result.update(data)
            await proc.wait()

        reader = asyncio.create_task(read())
        stopper = asyncio.create_task(stop.wait())
        try:
            finished, _ = await asyncio.wait({reader, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if reader not in finished:
                try:
                    proc.stdin.write(b'{"cancel":true}\n')
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                try:
                    await asyncio.wait_for(asyncio.shield(reader), 1)
                except TimeoutError:
                    result.update(status="unknown", input_released=False)
            else:
                await reader
        finally:
            for task in (reader, stopper):
                if not task.done():
                    task.cancel()
            await asyncio.gather(reader, stopper, return_exceptions=True)
    return result
