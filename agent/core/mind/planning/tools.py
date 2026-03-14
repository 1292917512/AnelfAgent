"""自主规划工具 — 创建、追踪和管理目标计划。

规划是 Agent 的核心认知能力，工具直接持有 MemoryStore 引用，
通过 ``register_planning_tools()`` 在运行时注入依赖后批量注册到 EntityRegistry。
"""

from __future__ import annotations

import datetime
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from entities._sdk import deferred_tool, activate_group

from ..memory.memory_store import MemoryStore
from ..memory.memory_types import MemoryEntry, MemoryType

_store: Optional[MemoryStore] = None
_GOAL_SOURCE = "goal"
_GROUP = "planning"


def _make_goal(
    title: str,
    description: str = "",
    steps: Optional[List[str]] = None,
    due_time: str = "",
    recurring: bool = False,
) -> Dict[str, Any]:
    """构造目标数据结构。"""
    goal: Dict[str, Any] = {
        "goal_id": uuid.uuid4().hex[:8],
        "title": title,
        "description": description,
        "status": "active",
        "recurring": recurring,
        "steps": [
            {"index": i, "content": s, "status": "pending", "note": ""}
            for i, s in enumerate(steps or [])
        ],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if due_time:
        goal["due_time"] = due_time
    return goal


def register_planning_tools(store: MemoryStore) -> None:
    """注入 MemoryStore 并批量注册规划工具。"""
    global _store
    _store = store
    activate_group(_GROUP, "目标规划管理 - 创建执行计划、追踪目标进度、管理自主任务")


# ------------------------------------------------------------------
# 工具实现
# ------------------------------------------------------------------

@deferred_tool(
    group=_GROUP, tags=["planning", "reflect"],
    description=(
        "创建一个新的目标计划。"
        "创建后记住 goal_id，完成后需调用 update_goal 将状态改为 completed，"
        "或调用 delete_goal 删除已完成的目标。"
    ),
)
async def create_goal(title: str, description: str = "", steps: str = "", due_time: str = "", recurring: bool = False) -> str:
    """创建一个新的目标计划。

    Args:
        title: 目标标题
        description: 目标详细描述
        steps: 执行步骤，用 | 分隔（如 "搜索资料|分析数据|总结报告"）
        due_time: 到期时间（如 "2025-03-10 18:00"），留空表示无期限
        recurring: 是否为循环计划，完成后自动重置步骤为 pending 并恢复 active

    注意：非循环目标完成后需调用 delete_goal(goal_id) 删除。
    """
    if _store is None:
        return json.dumps({"error": "MemoryStore 不可用"}, ensure_ascii=False)

    step_list = [s.strip() for s in steps.split("|") if s.strip()] if steps else []
    goal = _make_goal(title, description, step_list, due_time, recurring)

    entry = MemoryEntry(
        memory_type=MemoryType.SEMANTIC,
        content=json.dumps(goal, ensure_ascii=False),
        source=_GOAL_SOURCE,
        importance=0.8,
        metadata={"goal_id": goal["goal_id"], "status": "active"},
    )
    entry_id = await _store.add(entry)
    goal["memory_id"] = entry_id
    return json.dumps({"success": True, "goal": goal}, ensure_ascii=False)


@deferred_tool(
    group=_GROUP, tags=["planning", "reflect"],
    description=(
        "列出目标计划。定期检查 active 状态的目标，"
        "已完成的用 update_goal 标记为 completed 或用 delete_goal 删除。"
    ),
)
async def list_goals(status: str = "active") -> str:
    """列出目标计划。

    Args:
        status: 筛选状态，active（默认）/ completed / all

    提示：定期检查活跃目标，完成的及时标记或删除，避免目标堆积。
    """
    if _store is None:
        return json.dumps({"error": "MemoryStore 不可用"}, ensure_ascii=False)

    entries = await _store.list_recent(limit=50, memory_type=MemoryType.SEMANTIC, source=_GOAL_SOURCE)

    goals: List[Dict[str, Any]] = []
    for entry in entries:
        try:
            goal = json.loads(entry.content)
            goal["memory_id"] = entry.id
            if status == "all" or goal.get("status") == status:
                goals.append(goal)
        except (json.JSONDecodeError, AttributeError):
            continue

    return json.dumps({"goals": goals, "total": len(goals), "filter": status}, ensure_ascii=False)


@deferred_tool(
    group=_GROUP, tags=["planning", "reflect"],
    description=(
        "更新目标计划的步骤状态或整体状态。"
        "用于更新进行中的步骤进度。"
        "完成目标后建议直接用 delete_goal 删除，避免目标堆积。"
    ),
)
async def update_goal(
    goal_id: str,
    step_index: int = -1,
    step_status: str = "",
    note: str = "",
    goal_status: str = "",
) -> str:
    """更新目标计划的步骤状态或整体状态。

    Args:
        goal_id: 目标 ID
        step_index: 要更新的步骤索引（-1 表示不更新步骤）
        step_status: 步骤状态（pending / in_progress / completed / skipped）
        note: 步骤备注
        goal_status: 整体目标状态（active / completed / cancelled），留空不更新

    提示：完成目标后建议直接调用 delete_goal(goal_id) 删除，避免已完成目标干扰召回。
    """
    if _store is None:
        return json.dumps({"error": "MemoryStore 不可用"}, ensure_ascii=False)

    entries = await _store.list_recent(limit=50, memory_type=MemoryType.SEMANTIC, source=_GOAL_SOURCE)

    target_entry = None
    target_goal = None
    for entry in entries:
        try:
            goal = json.loads(entry.content)
            if goal.get("goal_id") == goal_id:
                target_entry = entry
                target_goal = goal
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    if target_entry is None or target_goal is None:
        return json.dumps({"error": f"目标 '{goal_id}' 不存在"}, ensure_ascii=False)

    if 0 <= step_index < len(target_goal.get("steps", [])):
        if step_status:
            target_goal["steps"][step_index]["status"] = step_status
        if note:
            target_goal["steps"][step_index]["note"] = note

    if goal_status:
        if goal_status == "completed" and target_goal.get("recurring"):
            for s in target_goal.get("steps", []):
                s["status"] = "pending"
                s["note"] = ""
            target_goal["status"] = "active"
            goal_status = "active"
        else:
            target_goal["status"] = goal_status

    target_goal["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    if target_entry.id:
        await _store.delete(target_entry.id)

    new_entry = MemoryEntry(
        memory_type=MemoryType.SEMANTIC,
        content=json.dumps(target_goal, ensure_ascii=False),
        source=_GOAL_SOURCE,
        importance=0.8 if target_goal["status"] == "active" else 0.3,
        metadata={"goal_id": goal_id, "status": target_goal["status"]},
    )
    new_id = await _store.add(new_entry)
    target_goal["memory_id"] = new_id

    result: Dict[str, Any] = {"success": True, "goal": target_goal}
    if target_goal["status"] in ("completed", "cancelled"):
        result["hint"] = f"目标已标记为 {target_goal['status']}，建议立即调用 delete_goal('{goal_id}') 删除"
    return json.dumps(result, ensure_ascii=False)


@deferred_tool(
    group=_GROUP, tags=["planning", "reflect"],
    description=(
        "删除一个目标计划。"
        "完成目标后应立即调用此工具删除，避免已完成目标干扰记忆召回。"
    ),
)
async def delete_goal(goal_id: str) -> str:
    """删除一个目标计划。

    Args:
        goal_id: 目标 ID

    重要：完成目标后必须删除！已完成目标如不删除会持续出现在记忆召回中。
    """
    if _store is None:
        return json.dumps({"error": "MemoryStore 不可用"}, ensure_ascii=False)

    entries = await _store.list_recent(limit=50, memory_type=MemoryType.SEMANTIC, source=_GOAL_SOURCE)

    for entry in entries:
        try:
            goal = json.loads(entry.content)
            if goal.get("goal_id") == goal_id and entry.id:
                await _store.delete(entry.id)
                return json.dumps({
                    "success": True,
                    "message": f"目标 '{goal_id}' 已删除",
                    "deleted_goal": goal.get("title", ""),
                }, ensure_ascii=False)
        except (json.JSONDecodeError, AttributeError):
            continue

    return json.dumps({"error": f"目标 '{goal_id}' 不存在"}, ensure_ascii=False)


@deferred_tool(group=_GROUP, tags=["planning", "reflect"])
async def get_goal(goal_id: str) -> str:
    """获取单个目标的详细信息。

    Args:
        goal_id: 目标 ID
    """
    if _store is None:
        return json.dumps({"error": "MemoryStore 不可用"}, ensure_ascii=False)

    entries = await _store.list_recent(limit=50, memory_type=MemoryType.SEMANTIC, source=_GOAL_SOURCE)

    for entry in entries:
        try:
            goal = json.loads(entry.content)
            if goal.get("goal_id") == goal_id:
                goal["memory_id"] = entry.id
                return json.dumps({"success": True, "goal": goal}, ensure_ascii=False)
        except (json.JSONDecodeError, AttributeError):
            continue

    return json.dumps({"error": f"目标 '{goal_id}' 不存在"}, ensure_ascii=False)


# ------------------------------------------------------------------
# 公共查询函数（供 Mind 自主循环调用）
# ------------------------------------------------------------------

async def collect_active_goals(store: MemoryStore) -> list[str]:
    """从 MemoryStore 收集活跃目标摘要。"""
    try:
        entries = await store.list_recent(
            limit=10, memory_type=MemoryType.SEMANTIC, source=_GOAL_SOURCE,
        )
        goals: list[str] = []
        for entry in entries:
            data = json.loads(entry.content)
            if data.get("status") == "active":
                title = data.get("title", "")
                steps = data.get("steps", [])
                done = sum(1 for s in steps if s.get("status") == "completed")
                due = data.get("due_time", "")
                summary = f"{data.get('goal_id', '?')}: {title} ({done}/{len(steps)} 步)"
                if due:
                    summary += f" [到期: {due}]"
                goals.append(summary)
        return goals
    except Exception:
        return []


async def check_due_goals(store: MemoryStore) -> list[str]:
    """检查已到期的活跃目标，返回到期目标的 goal_id 列表。"""
    try:
        now = datetime.datetime.now()
        entries = await store.list_recent(
            limit=10, memory_type=MemoryType.SEMANTIC, source=_GOAL_SOURCE,
        )
        due_ids: list[str] = []
        for entry in entries:
            data = json.loads(entry.content)
            if data.get("status") != "active":
                continue
            due_time = data.get("due_time", "")
            if not due_time:
                continue
            try:
                dt = datetime.datetime.strptime(due_time, "%Y-%m-%d %H:%M")
            except ValueError:
                try:
                    dt = datetime.datetime.strptime(due_time, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
            if now >= dt:
                due_ids.append(data.get("goal_id", ""))
        return [gid for gid in due_ids if gid]
    except Exception:
        return []
