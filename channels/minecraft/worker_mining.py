"""石头阶段的走廊订单策略：延续已返程矿道，或在表面无石料时盲开浅走廊。

矿道与表面采集共用同一套安全与挖掘原语（自然方块名单、挖掘回执、回程
铁律）；本模块只表达走廊几何的偏好——既定几何优先延续，新开时按轮换
方向盲挖（石头总在几格之下，不依赖先看见石头才敢下挖）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

_DIRECTIONS = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
_HEADINGS = ("east", "south", "west", "north")
_MAX_STARTER_DEPTH = 8
_CORRIDOR_ITEM = "cobblestone"


async def stone_corridor_order(
    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    entry: dict[str, int],
    count: int,
    heading: int = 0,
    *,
    extend_only: bool = False,
) -> dict[str, Any] | None:
    """已返程且走完的矿道优先延续；extend_only 时不新开。

    延续条件与执行器的 extend 契约一致：completed，或已走完全部格且
    partial。新开为盲走廊：固定 8 深、轮换方向——不依赖先看见石头。
    """
    saved = await call("mining_status", {})
    depth, length = saved.get("depth"), saved.get("length")
    steps = saved.get("steps")
    phase = str(saved.get("phase") or "")
    walked_out = isinstance(steps, int) and isinstance(depth, int) and isinstance(length, int)         and steps == depth + length
    continuable = isinstance(steps, int) and isinstance(depth, int) and isinstance(length, int)         and 0 <= steps < depth + length
    # 已返程的终态走廊一律延续：未走完的从 steps 续挖（半途阶梯是留存
    # 进度，作废只会让 bot 站在自己挖出的廊口上开不了新口）；已走完的
    # partial/completed 延长水平段。
    if (saved.get("id") and not saved.get("active") and saved.get("returned") is True
            and phase in {"partial", "completed", "blocked"} and saved.get("item") == _CORRIDOR_ITEM
            and isinstance(depth, int) and 1 <= depth <= 16
            and isinstance(length, int) and 0 <= length < 32
            and (continuable or (walked_out and phase != "blocked"))
            and saved.get("direction") in _DIRECTIONS):
        return {"direction": saved["direction"], "depth": depth, "length": min(8, 32 - length),
                "item": _CORRIDOR_ITEM, "count": count, "extend": True, "requireTorches": False}
    if extend_only:
        return None
    return {"direction": _HEADINGS[heading % len(_HEADINGS)], "depth": _MAX_STARTER_DEPTH,
            "length": 8, "minY": entry["y"] - _MAX_STARTER_DEPTH, "item": _CORRIDOR_ITEM,
            "count": count, "extend": False, "requireTorches": False}
