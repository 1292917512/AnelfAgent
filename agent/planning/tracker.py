"""会话计划的提交、查询、取消与进度事件。

present_plan 提交后首步进入 in_progress，步骤与终态由 planning.goals
在写入时判定。会话范围只影响计划可见性与事件投递。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import GOAL_SOURCE, MemoryEntry, MemoryType
from agent.planning.goals import find_goal, goal_view, iter_goals, normalize_steps, update_goal
from core.conversation_scope import current_scope as current_scope
from core.event_bus import (
    EVENT_PLAN_CANCELLED,
    EVENT_PLAN_DELETED,
    EVENT_PLAN_STATUS_CHANGED,
    EVENT_PLAN_STEP_UPDATED,
    EVENT_PLAN_SUBMITTED,
    event_bus,
)
from core.latebind import LateBinding
from core.log import log

#: 会话执行计划的 metadata.kind
PLAN_KIND = "present_plan"
#: 持久目标的 metadata.kind（create_goal 生命周期：AI 手动推进）
GOAL_KIND = "goal"

#: MemoryStore 端口（planning 工具组与 tracker 共用；工具 import 时注册、
#: 拿不到 MemoryStore 构造参数，由 agent.runtime.wiring 统一施绑）
planning_store_port: LateBinding[MemoryStore] = LateBinding("planning.store")


def get_planning_store() -> Optional[MemoryStore]:
    """取 MemoryStore（未施绑时返回 None，调用方降级为未就绪处理）。"""
    return planning_store_port.get() if planning_store_port.bound else None


# ------------------------------------------------------------------
# scope 工具（delegation / plan / webui 共享）
# ------------------------------------------------------------------

def parse_scope_chat_id(scope: str) -> Tuple[str, str]:
    """从 scope 提取 (base_scope, chat_id)。

    scope 形如 ``user_web_user`` 或 ``user_web_user#abc123``。
    """
    if "#" in scope:
        base, chat_id = scope.split("#", 1)
        return base, chat_id
    return scope, ""


# ------------------------------------------------------------------
# 内部：active plan 查询（三个消费者共享，消除三份重复遍历）
# ------------------------------------------------------------------

async def _find_active_plan(
    scope: str,
) -> Tuple[Optional[MemoryEntry], Optional[Dict[str, Any]]]:
    """查找当前 scope 的 active 会话计划（取最近一条）。

    只认 ``metadata.kind == PLAN_KIND`` 且 scope 严格相等的条目——
    持久目标（GOAL_KIND）与历史无 kind 条目一律排除，持久目标由自己的 goal_id 管理。

    Returns:
        (entry, goal) 或 (None, None)。
    """
    store = get_planning_store()
    if store is None:
        return None, None
    async for entry, goal in iter_goals(store, status="active"):
        if entry.metadata.get("kind") == PLAN_KIND and entry.metadata.get("scope") == scope:
            return entry, goal
    return None, None


async def get_active_plan(scope: str) -> Optional[Dict[str, Any]]:
    """公开查询：当前 scope 的 active plan（goal dict），无则 None。

    present_plan 工具据此复用已有计划（防止 AI 重复规划）。
    """
    entry, goal = await _find_active_plan(scope)
    return goal_view(entry, goal) if entry is not None and goal is not None else None


# ------------------------------------------------------------------
# 内部：事件发射（统一 payload 构造 + scope/chat_id 解析）
# ------------------------------------------------------------------

# 非用户会话 scope：reflect（心跳/子代理）产生的 plan 只持久化、不发射前端事件，
# 避免 scope="reflect" 的幽灵计划出现在 计划卡片
_REFLECT_SCOPE = "reflect"


def _is_user_facing(scope: str) -> bool:
    """scope 是否对应真实用户会话（决定 plan 事件是否发射到前端）。"""
    return scope != _REFLECT_SCOPE and not scope.startswith(_REFLECT_SCOPE + ":")


async def _emit_step(
    scope: str, plan_id: str, step_index: int, status: str, note: str = "",
) -> None:
    if not _is_user_facing(scope):
        return
    _, chat_id = parse_scope_chat_id(scope)
    try:
        await event_bus.emit(EVENT_PLAN_STEP_UPDATED, {
            "scope": scope, "chat_id": chat_id,
            "plan_id": plan_id, "step_index": step_index,
            "step_status": status, "note": note, "ts": time.time(),
        })
    except Exception as exc:
        log(f"plan 步骤事件发射失败: {exc}", "DEBUG", tag="规划")


async def _emit_status(scope: str, plan_id: str, status: str) -> None:
    if not _is_user_facing(scope):
        return
    _, chat_id = parse_scope_chat_id(scope)
    try:
        await event_bus.emit(EVENT_PLAN_STATUS_CHANGED, {
            "scope": scope, "chat_id": chat_id,
            "plan_id": plan_id, "goal_status": status, "ts": time.time(),
        })
    except Exception as exc:
        log(f"plan 状态事件发射失败: {exc}", "DEBUG", tag="规划")


# ------------------------------------------------------------------
# 公共 API
# ------------------------------------------------------------------

def parse_steps(steps_text: str) -> List[Dict[str, Any]]:
    """解析步骤文本为步骤对象列表（兼容 \\n 与 | 分隔），首步 in_progress。"""
    raw = [
        s.strip()
        for s in (steps_text.replace("|", "\n").split("\n") if steps_text else [])
        if s.strip()
    ]
    steps = [
        {"index": i, "content": s, "status": "pending", "note": ""}
        for i, s in enumerate(raw)
    ]
    if steps:
        steps[0]["status"] = "in_progress"
    return steps


async def submit_plan(
    scope: str, goal: str, steps: List[Dict[str, Any]],
    files: str = "", risks: str = "",
) -> str:
    """公告计划：持久化 + 发射 plan_submitted / 首步 in_progress 事件。

    Returns:
        plan_id
    """
    from agent.planning import situation

    plan_id = uuid.uuid4().hex[:8]
    steps = normalize_steps(plan_id, steps)
    _, chat_id = parse_scope_chat_id(scope)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    store = planning_store_port.get()
    goal_doc = {
        "goal_id": plan_id,
        "title": goal,
        "description": "",
        "status": "active",
        "recurring": False,
        "steps": steps,
        "files": files,
        "risks": risks,
        "created_at": now,
        "updated_at": now,
    }
    entry = MemoryEntry(
        memory_type=MemoryType.SEMANTIC,
        content=json.dumps(goal_doc, ensure_ascii=False),
        source=GOAL_SOURCE,
        importance=0.8,
        tags=[f"goal:{plan_id}"],
        metadata={
            "goal_id": plan_id, "status": "active",
            "kind": PLAN_KIND, "scope": scope,
        },
    )
    await store.add(entry, actor="planning")
    situation.invalidate()

    try:
        if _is_user_facing(scope):
            await event_bus.emit(EVENT_PLAN_SUBMITTED, {
                "scope": scope, "chat_id": chat_id, "plan_id": plan_id,
                "goal": goal, "steps": steps, "files": files, "risks": risks,
                "ts": time.time(),
            })
    except Exception as exc:
        log(f"plan_submitted 事件发射失败（不影响执行）: {exc}", "DEBUG", tag="规划")

    if steps:
        await _emit_step(scope, plan_id, 0, "in_progress", note="自动推进")
    return plan_id


async def cancel_plan(scope: str, plan_id: str, reason: str = "用户取消") -> bool:
    """取消计划：标记 cancelled + 发射事件 + interrupt scope。

    Returns:
        是否找到并取消了该 plan。
    """
    store = get_planning_store()
    if store is None:
        return False
    try:
        entry, goal = await _find_active_plan(scope)
        if entry is None or goal is None or goal.get("goal_id") != plan_id:
            return False
        await update_goal(store, entry, goal, expected_version=entry.version, status="cancelled", actor="web")
        _, chat_id = parse_scope_chat_id(scope)
        try:
            await event_bus.emit(EVENT_PLAN_CANCELLED, {
                "scope": scope, "chat_id": chat_id,
                "plan_id": plan_id, "reason": reason,
            })
        except Exception:
            log("cancel_plan 异常已忽略", "DEBUG")
        # 协作式中断当前 think_loop（Agent 下轮检查点停止）
        try:
            from agent.runtime.singleton import get_runtime
            rt = get_runtime()
            mind = getattr(rt, "mind", None) if rt is not None else None
            if mind is not None and hasattr(mind, "interrupt"):
                mind.interrupt(scope, reason=reason)
        except Exception:
            log("cancel_plan 异常已忽略", "DEBUG")
        return True
    except Exception:
        return False


async def find_goal_by_id(
    goal_id: str,
    *, include_archived: bool = False,
) -> Tuple[Optional[MemoryEntry], Optional[Dict[str, Any]]]:
    """按 goal_id 分页定位；只读管理查询可显式包含归档，写路径默认只读活跃库。"""
    store = get_planning_store()
    if store is None:
        return None, None
    return await find_goal(store, goal_id, include_archived=include_archived)


# ------------------------------------------------------------------
# 条目移除（显式删除）
# ------------------------------------------------------------------

async def remove_goal(
    entry: MemoryEntry, *, store: MemoryStore | None = None,
    goal: Dict[str, Any] | None = None, actor: str = "planning",
) -> bool:
    """删除目标条目并同步前端（EVENT_PLAN_DELETED 移除卡片）与态势快照。

    AI/Web 显式操作共用；删除、去向审计原子提交，之后刷新态势与前端卡片。
    store 由服务层构造注入，工具 import 时注册则复用 planning_store_port。

    Returns:
        是否实际删除。
    """
    store = store if store is not None else get_planning_store()
    if store is None or not entry.id or entry.archived_at:
        return False
    if goal is None:
        parsed = json.loads(entry.content)
        goal = parsed if isinstance(parsed, dict) else {}
    goal_id = str(goal.get("goal_id") or entry.metadata.get("goal_id") or "")
    title = str(goal.get("title") or "")
    deleted = await store.delete(
        entry.id, actor=actor, expected_version=entry.version,
        detail=f"goal:{goal_id} → deleted: {title}",
        audit_data={"goal_id": goal_id, "title": title, "status": "deleted", "destination": "deleted",
                    "goal": {**goal, "status": "deleted"}},
    )
    if not deleted:
        return False
    from agent.planning import situation
    situation.invalidate()
    scope = str(entry.metadata.get("scope") or current_scope())
    _, chat_id = parse_scope_chat_id(scope)
    try:
        await event_bus.emit(EVENT_PLAN_DELETED, {
            "scope": scope, "chat_id": chat_id,
            "plan_id": goal_id,
        })
    except Exception as exc:
        log(f"目标删除事件发射失败（不影响结果）: {exc}", "DEBUG", tag="规划")
    return True
