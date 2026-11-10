"""目标的统一查询、步骤契约与写入操作，供工具、Web 和计划追踪共用。

Model Experience: 目标返回版本及稳定步骤 ID；写入校验读取版本，终态结果可按 ID 查询。
Token effect: 每步增加一个短 ID，目标增加版本；不新增模型调用。
Cache effect: 只影响工具回执及动态规划态势，不修改稳定前缀。
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import GOAL_SOURCE, MemoryEntry, MemoryType

STEP_STATUSES = frozenset({"pending", "in_progress", "completed", "skipped"})
GOAL_STATUSES = frozenset({"active", "completed", "cancelled"})


class GoalConflict(ValueError):
    """目标读取版本已过期，调用方必须重读后决策。"""


def normalize_steps(goal_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """兼容历史 step 字段，稳定派生旧步骤 ID；不回写只读查询。"""
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        raise ValueError("steps 必须是步骤对象数组")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, step in enumerate(steps):
        content = str(step.get("content") or step.get("step") or "").strip()
        step_id = str(step.get("step_id") or uuid.uuid5(
            uuid.NAMESPACE_URL, f"goal:{goal_id}:step:{index}:{content}",
        ).hex[:12])
        if step_id in seen:
            raise ValueError("步骤 ID 重复")
        seen.add(step_id)
        status = step.get("status", "pending")
        if not isinstance(status, str) or status not in STEP_STATUSES:
            raise ValueError(f"非法步骤状态 '{status}'；有效值: {' / '.join(sorted(STEP_STATUSES))}")
        result.append({"step_id": step_id, "index": index, "content": content,
                       "status": status, "note": str(step.get("note") or "")})
    return result


def goal_view(entry: MemoryEntry, goal: dict[str, Any]) -> dict[str, Any]:
    """统一只读回执；版本、归档状态不写入目标正文。"""
    return {**goal, "memory_id": entry.id, "version": entry.version, "created_ts": entry.timestamp,
            "archived": bool(entry.archived_at),
            **({"archived_at": entry.archived_at, "archive_reason": entry.archive_reason}
               if entry.archived_at else {})}


async def iter_goals(
    store: MemoryStore, *, status: str = "all", include_archived: bool = False,
) -> AsyncIterator[tuple[MemoryEntry, dict[str, Any]]]:
    """完整分页后按领域状态筛选，调用方只能在筛选后限制展示数量。"""
    offset = 0
    while True:
        entries = await store.list_by_source(
            GOAL_SOURCE, memory_type=MemoryType.SEMANTIC, limit=100, offset=offset,
            include_archived=include_archived,
        )
        for entry in entries:
            try:
                goal = json.loads(entry.content)
                if not isinstance(goal, dict) or not goal.get("goal_id"):
                    continue
                if status != "all" and goal.get("status") != status:
                    continue
                goal["steps"] = normalize_steps(str(goal["goal_id"]), goal.get("steps", []))
            except (ValueError, TypeError, AttributeError):
                continue
            yield entry, goal
        if len(entries) < 100:
            return
        offset += len(entries)


async def find_goal(
    store: MemoryStore, goal_id: str, *, include_archived: bool = False,
) -> tuple[MemoryEntry | None, dict[str, Any] | None]:
    async for entry, goal in iter_goals(store, include_archived=include_archived):
        if goal["goal_id"] == goal_id:
            return entry, goal
    return None, None


async def terminal_receipt(store: MemoryStore, goal_id: str) -> dict[str, Any] | None:
    """复用既有审计保留窗口；历史简版审计也能说明目标去向。"""
    rows = await store.list_audit(goal_id=goal_id, limit=1)
    if not rows:
        return None
    row = rows[0]
    data = row["data"]
    return {**data.get("goal", {"goal_id": goal_id, "title": data.get("title", ""),
                                "status": data.get("status", "deleted"), "steps": []}),
            "memory_id": row["memory_id"], "deleted": True, "closed_at": row["timestamp"],
            "actor": row["actor"], "destination": data.get("destination", "deleted")}


async def create_goal(
    store: MemoryStore, title: str, description: str = "", steps: list[str] | None = None,
    *, recurring: bool = False, due_time: str | None = None, actor: str = "planning",
) -> dict[str, Any]:
    from agent.planning import situation
    from agent.planning.tracker import GOAL_KIND

    if not title.strip():
        raise ValueError("目标标题不能为空")
    goal_id = uuid.uuid4().hex[:16]
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    goal = {"goal_id": goal_id, "title": title.strip(), "description": description,
            "status": "active", "recurring": recurring, "created_at": now, "updated_at": now,
            "steps": normalize_steps(goal_id, [{"content": s} for s in (steps or []) if s.strip()])}
    if due_time:
        goal["due_time"] = due_time
    entry = MemoryEntry(
        memory_type=MemoryType.SEMANTIC, content=json.dumps(goal, ensure_ascii=False),
        source=GOAL_SOURCE, importance=0.8, tags=[f"goal:{goal_id}"],
        metadata={"goal_id": goal_id, "status": "active", "kind": GOAL_KIND},
    )
    entry.id = await store.add(entry, actor=actor)
    situation.invalidate()
    return goal_view(entry, goal)


def select_step(steps: list[dict[str, Any]], *, step_id: str, step_name: str, step_index: int) -> int:
    """确切定位步骤；索引必须附身份校验，名称重名时不得猜测。"""
    if not steps:
        raise ValueError("目标没有步骤，无法更新步骤状态")
    if step_index < -1 or step_index >= len(steps):
        raise ValueError(f"步骤索引 {step_index} 超出范围（有效 0~{len(steps) - 1}）")
    if not (step_id or step_name):
        raise ValueError("更新步骤须提供 step_id 或完整 step_name；不能仅凭数字索引写入")
    matches = [i for i, s in enumerate(steps)
               if (not step_id or s["step_id"] == step_id)
               and (not step_name or s["content"] == step_name)]
    if len(matches) != 1 or (step_index >= 0 and matches[0] != step_index):
        raise ValueError("步骤标识/名称/索引不一致或名称不唯一，请按 get_goal 返回的 step_id 更新")
    return matches[0]


async def update_goal(
    store: MemoryStore, entry: MemoryEntry, goal: dict[str, Any], *, expected_version: int,
    title: str | None = None, description: str | None = None, status: str | None = None,
    steps: list[dict[str, Any]] | None = None, due_time: str | None = None,
    recurring: bool | None = None, step_id: str = "", step_name: str = "", step_index: int = -1,
    step_status: str = "", note: str = "", note_mode: str = "replace", actor: str = "planning",
) -> dict[str, Any]:
    """校验完整变更后一次提交；版本冲突不写入、不发布成功事件。"""
    from agent.planning import situation, tracker

    if expected_version != entry.version:
        raise GoalConflict("目标版本已变化或未提供 expected_version，请 get_goal 重读后更新")
    if status is not None and status not in GOAL_STATUSES:
        raise ValueError("非法目标状态；有效值: active / completed / cancelled")
    if step_status and step_status not in STEP_STATUSES:
        raise ValueError(f"非法步骤状态；有效值: {' / '.join(sorted(STEP_STATUSES))}")
    if note_mode not in ("replace", "append"):
        raise ValueError("note_mode 只允许 replace / append")
    goal = {**goal, "steps": normalize_steps(goal["goal_id"], goal.get("steps", []))}
    if steps is not None:
        goal["steps"] = normalize_steps(goal["goal_id"], steps)
        if any(not s["content"] for s in goal["steps"]):
            raise ValueError("步骤正文不能为空，请保留 content 和 step_id")
    changed_index = -1
    if step_status or note:
        changed_index = select_step(goal["steps"], step_id=step_id, step_name=step_name, step_index=step_index)
        step = goal["steps"][changed_index]
        if step_status:
            step["status"] = step_status
        if note:
            step["note"] = "\n".join(filter(None, [step["note"], note])) if note_mode == "append" else note
    for key, value in (("title", title), ("description", description), ("recurring", recurring), ("due_time", due_time)):
        if value is not None:
            goal[key] = value
    if due_time == "":
        goal.pop("due_time", None)
    if not str(goal["title"]).strip():
        raise ValueError("目标标题不能为空")
    if status is not None:
        goal["status"] = status
    auto_close = (status is None and (steps is not None or bool(step_status)) and goal["steps"]
                  and not goal.get("recurring") and goal["status"] == "active"
                  and all(s["status"] in ("completed", "skipped") for s in goal["steps"]))
    if auto_close:
        goal["status"] = "completed"
    if goal["status"] == "completed" and goal.get("recurring"):
        goal["status"] = "active"
        for step in goal["steps"]:
            step.update(status="pending", note="")
    goal["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if goal["status"] in ("completed", "cancelled"):
        if not await tracker.remove_goal(entry, store=store, goal=goal, status=goal["status"], actor=actor):
            raise GoalConflict("目标已发生变更，本次清理未执行")
        return {**goal_view(entry, goal), "deleted": True}
    advance = (changed_index >= 0 and step_status == "completed" and status != "completed"
               and changed_index + 1 < len(goal["steps"]) and goal["steps"][changed_index + 1]["status"] == "pending")
    if advance:
        goal["steps"][changed_index + 1]["status"] = "in_progress"
    entry.content = json.dumps(goal, ensure_ascii=False)
    entry.metadata = {**entry.metadata, "status": goal["status"], "goal_id": goal["goal_id"]}
    entry.importance = 0.8
    if not await store.update(entry, actor=actor, expected_version=expected_version):
        raise GoalConflict("目标已发生变更，本次更新未写入")
    situation.invalidate()
    scope = str(entry.metadata.get("scope") or tracker.current_scope())
    if changed_index >= 0:
        step = goal["steps"][changed_index]
        await tracker._emit_step(scope, goal["goal_id"], changed_index, step["status"], note=step["note"])
        if advance:
            await tracker._emit_step(scope, goal["goal_id"], changed_index + 1, "in_progress", note="自动推进")
    if status is not None:
        await tracker._emit_status(scope, goal["goal_id"], goal["status"])
    return goal_view(entry, goal)
