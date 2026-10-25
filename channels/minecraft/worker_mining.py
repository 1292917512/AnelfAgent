"""为表面采石露出附近浅埋石层，复用有返程检查的阶梯挖掘。"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

_DIRECTIONS = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
_MAX_STARTER_DEPTH = 6
_SOIL_OBSTRUCTION = re.compile(
    r"MINING_RETURN_BLOCKED: Return passage obstructed by "
    r"(dirt|grass_block|podzol|mycelium|dirt_path) at (-?\d+),(-?\d+),(-?\d+)\."
)
_TREE_OBSTRUCTION = re.compile(r"Unexpected ([a-z0-9_]+_log) at \((-?\d+), (-?\d+), (-?\d+)\)")


async def blocked_tree_order(
    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, int], str] | None:
    """仅为已返程矿道下一列的已确认原木生成整树清理单。"""
    saved = await call("mining_status", {})
    match = _TREE_OBSTRUCTION.search(str(saved.get("reason") or ""))
    entry, direction, steps, depth = (saved.get(k) for k in ("entry", "direction", "steps", "depth"))
    if (saved.get("phase") != "blocked" or saved.get("returned") is not True or saved.get("active")
            or saved.get("item") != "cobblestone" or match is None or not isinstance(entry, dict)
            or not all(k in entry for k in ("x", "y", "z")) or direction not in _DIRECTIONS
            or not isinstance(steps, int) or not isinstance(depth, int) or not 0 <= steps < 48):
        return None
    name, sx, sy, sz = match.groups()
    target = {"x": int(sx), "y": int(sy), "z": int(sz)}
    dx, dz = _DIRECTIONS[direction]
    floor_y = entry["y"] - min(steps + 1, depth)
    if (name.startswith("stripped_") or target["x"] != entry["x"] + dx * (steps + 1)
            or target["z"] != entry["z"] + dz * (steps + 1) or not floor_y <= target["y"] <= floor_y + 2):
        return None
    actual = await call("get_block_at", target)
    if actual.get("name") != name:
        return None
    state = await call("get_state", {})
    position = state.get("position") or {}
    if not all(k in position for k in ("x", "y", "z")):
        return None
    if sum((float(position[k]) - target[k]) ** 2 for k in ("x", "y", "z")) > 64:
        return None
    return ({"block": name, "count": 64, "mode": "tree", "radius": 8, **target}, target, str(saved.get("id")))


async def start_stone_mining(
    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]], order: dict[str, Any],
) -> dict[str, Any]:
    """延长矿道前仅清理可见可挖、属于已登记通道体积的少量泥土堵塞。"""
    for attempt in range(3):
        try:
            return await call("mine_resources", order)
        except Exception as exc:
            match = _SOIL_OBSTRUCTION.search(str(exc))
            if attempt == 2 or not order.get("extend") or match is None:
                raise
            name, sx, sy, sz = match.groups()
            target = {"x": int(sx), "y": int(sy), "z": int(sz)}
            saved = await call("mining_status", {})
            entry, direction, steps, depth = (saved.get(key) for key in ("entry", "direction", "steps", "depth"))
            if (saved.get("active") or saved.get("returned") is not True or not isinstance(entry, dict)
                    or not all(k in entry for k in ("x", "y", "z")) or direction not in _DIRECTIONS
                    or not isinstance(steps, int) or not isinstance(depth, int) or not 0 <= steps <= 48):
                raise
            dx, dz = _DIRECTIONS[direction]
            if not any(target["x"] == entry["x"] + dx * i and target["z"] == entry["z"] + dz * i
                       and entry["y"] - min(i, depth) <= target["y"] <= entry["y"] - min(i, depth) + 2
                       for i in range(steps + 1)):
                raise
            actual = await call("get_block_at", target)
            if actual.get("name") != name or actual.get("canSee") is not True or actual.get("canDig") is not True:
                raise
            receipt = await call("dig", target)
            if receipt.get("serverConfirmed") is not True:
                raise RuntimeError("MINING_REPAIR_UNCONFIRMED: 通道堵塞尚未确认清除") from exc
    raise RuntimeError("MINING_REPAIR_LIMIT: 通道清理次数已用尽")


async def approach_shallow_face(
    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> None:
    """在已确认返程的浅阶梯附近，沿现成通道走回底部采石面。"""
    saved = await call("mining_status", {})
    entry = saved.get("entry")
    direction = _DIRECTIONS.get(str(saved.get("direction")))
    steps = saved.get("steps")
    if (saved.get("active") or saved.get("returned") is not True or saved.get("phase") not in {"partial", "completed"}
            or not isinstance(saved.get("depth"), int) or not 1 <= saved["depth"] <= _MAX_STARTER_DEPTH
            or saved.get("length") != 0 or not direction
            or not isinstance(steps, int) or not 1 <= steps <= saved["depth"]
            or not isinstance(entry, dict) or not all(k in entry for k in ("x", "y", "z"))):
        return
    state = await call("get_state", {})
    position = state.get("position") or {}
    if not all(k in position for k in ("x", "y", "z")):
        return
    if sum((float(position[k]) - float(entry[k])) ** 2 for k in ("x", "y", "z")) > 64:
        return
    await call("goto", {"goalType": "block", "x": int(entry["x"]) + direction[0] * steps,
                        "y": int(entry["y"]) - steps, "z": int(entry["z"]) + direction[1] * steps,
                        "timeout": 30000})


async def shallow_stone_order(
    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    entry: dict[str, int],
    count: int,
    *,
    extend_only: bool = False,
) -> dict[str, Any] | None:
    """开短浅矿道或延长已走完且返程的启动阶梯，不恢复中断任务。"""
    saved = await call("mining_status", {})
    if saved.get("id") or saved.get("active"):
        depth, length = saved.get("depth"), saved.get("length")
        if (saved.get("returned") is True and saved.get("phase") in {"partial", "completed"}
                and saved.get("item") == "cobblestone" and isinstance(depth, int) and 1 <= depth <= _MAX_STARTER_DEPTH
                and isinstance(length, int)
                and 0 <= length < 32 and (saved.get("phase") == "completed" or saved.get("steps") == depth + length)
                and saved.get("direction") in _DIRECTIONS):
            return {"direction": saved["direction"], "depth": depth, "length": min(8, 32 - length),
                    "item": "cobblestone", "count": count, "extend": True, "requireTorches": False}
        return None
    if extend_only:
        return None
    found = await call("find_blocks", {
        "matching": ["stone"], "point": entry, "maxDistance": 8, "count": 8,
    })
    for target in found.get("positions") or []:
        if not isinstance(target, dict) or not all(k in target for k in ("x", "y", "z")):
            continue
        dx, dy, dz = (int(target[k]) - entry[k] for k in ("x", "y", "z"))
        if not -_MAX_STARTER_DEPTH <= dy < 0:
            continue
        depth = max(3, -dy)
        direction = ("east" if dx > 0 else "west") if abs(dx) > abs(dz) else ("south" if dz > 0 else "north")
        return {"direction": direction, "depth": depth, "length": 8, "minY": entry["y"] - depth,
                "item": "cobblestone", "count": count, "extend": False, "requireTorches": False}
    return None
