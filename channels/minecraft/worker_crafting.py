"""等待现有制作执行器确认镐与工作台准备完毕，不另行编排配方。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Literal


async def prepare_pickaxe(
    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    *,
    running: Callable[[], bool],
    poll_seconds: float,
    clock: Callable[[], float],
    item: Literal["wooden_pickaxe", "stone_pickaxe"] = "wooden_pickaxe",
    mode: Literal["ensure", "craft"] = "ensure",
) -> dict[str, Any]:
    """复用 prepare_item 的工作台与配方计划，只接受指定镐任务的清洁终态。"""
    status = await asyncio.wait_for(
        call("prepare_item", {"item": item, "count": 1, "mode": mode}), timeout=15,
    )
    task_id = status.get("id")
    if not task_id:
        raise RuntimeError(f"CRAFT_NOT_STARTED: {status}")
    deadline = clock() + 150
    while True:
        if not running():
            raise asyncio.CancelledError
        if status.get("id") != task_id or status.get("item") != item:
            raise RuntimeError("CRAFT_TASK_CHANGED: 制作状态不属于当前镐任务")
        if not status.get("active") and status.get("phase") not in {"running", "stopping", "paused", "pausing"}:
            if status.get("phase") != "completed" or status.get("inventoryClean") is not True:
                raise RuntimeError(f"CRAFT_BLOCKED: {status.get('reason') or status.get('phase')}")
            return status
        if clock() >= deadline:
            await asyncio.wait_for(call("cancel_task", {}), timeout=3)
            raise TimeoutError("CRAFT_TIMEOUT: 镐制作未确认完成")
        await asyncio.sleep(poll_seconds)
        status = await asyncio.wait_for(call("production_status", {}), timeout=10)
