"""规划行为测试的正常调用路径：读取快照后按身份与版本更新。"""
from __future__ import annotations

import json
from typing import Any

from agent.planning import tools


async def update_goal(goal_id: str, **changes: Any) -> str:
    current = json.loads(await tools.get_goal(goal_id)).get("goal", {})
    changes.setdefault("expected_version", current.get("version", 0))
    index = changes.get("step_index", -1)
    steps = current.get("steps", [])
    if 0 <= index < len(steps):
        changes.setdefault("step_id", steps[index]["step_id"])
    return await tools.update_goal(goal_id, **changes)
