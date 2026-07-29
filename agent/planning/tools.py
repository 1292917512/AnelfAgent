"""自主规划工具 — 创建、追踪和管理目标计划。

规划是 Agent 的核心认知能力，工具直接持有 MemoryStore 引用，
通过 ``register_planning_tools()`` 在运行时注入依赖后批量注册到 EntityRegistry。

Plan 模式（present_plan）：
- Agent 自发提交计划后立即开始执行，**不**等待用户批准（不走 ApprovalGate）。
- 工具调用时同步发射 ``EVENT_PLAN_SUBMITTED`` / ``EVENT_PLAN_STEP_UPDATED`` 事件，
  前端据此渲染 PlanPanel 浮窗与 PlanCard 消息卡。
- 用户通过浮窗"取消"按钮触发 ``EVENT_PLAN_CANCELLED``，由 webui adapter 路由
  到对应 scope 的 interrupt，协作式停止后续工具链。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from entities._sdk import deferred_tool, activate_group

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from core.event_bus import (
    event_bus,
    EVENT_PLAN_SUBMITTED,
    EVENT_PLAN_STEP_UPDATED,
    EVENT_PLAN_STATUS_CHANGED,
)
from core.log import log

_store: Optional[MemoryStore] = None
_GOAL_SOURCE = "goal"
_GROUP = "planning"


def _current_scope() -> str:
    """读取当前对话 scope（用于 plan 事件按 scope 路由到对应 chat_id）。"""
    try:
        from agent.mind.tool_activation import ToolActivationManager
        return ToolActivationManager.current_scope()
    except Exception:
        return "_global"


def _parse_scope_chat_id(scope: str) -> tuple[str, str]:
    """从 scope 提取 (user_scope, chat_id)。scope 形如 'user_web_user' 或 'user_web_user#abc123'。"""
    if "#" in scope:
        base, chat_id = scope.split("#", 1)
        return base, chat_id
    return scope, ""


def _make_goal(
    title: str,
    description: str = "",
    steps: Optional[List[str]] = None,
    recurring: bool = False,
) -> Dict[str, Any]:
    """构造目标数据结构。"""
    return {
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


def register_planning_tools(store: MemoryStore) -> None:
    """注入 MemoryStore 并批量注册规划工具。"""
    global _store
    _store = store
    activate_group(_GROUP, "目标规划管理 - 创建执行计划、追踪目标进度")


async def _find_goal(
    store: MemoryStore, goal_id: str,
) -> tuple[Optional[MemoryEntry], Optional[Dict[str, Any]]]:
    """按 source='goal' 分页查全，定位 goal_id 对应的记忆条目与目标数据。

    目标数量可能超过单页，逐页扫描直到命中或遍历完毕。
    """
    page_size = 100
    offset = 0
    while True:
        entries = await store.list_by_source(
            _GOAL_SOURCE, memory_type=MemoryType.SEMANTIC,
            limit=page_size, offset=offset,
        )
        if not entries:
            break
        for entry in entries:
            try:
                goal = json.loads(entry.content)
            except (json.JSONDecodeError, AttributeError):
                continue
            if goal.get("goal_id") == goal_id:
                return entry, goal
        if len(entries) < page_size:
            break
        offset += page_size
    return None, None


# ------------------------------------------------------------------
# 工具实现
# ------------------------------------------------------------------

@deferred_tool(
    group=_GROUP, tags=["planning", "heartbeat"],
    description=(
        "创建一个新的目标计划。"
        "创建后记住 goal_id，完成后需调用 update_goal 将状态改为 completed，"
        "或调用 delete_goal 删除已完成的目标。"
    ),
)
async def create_goal(title: str, description: str = "", steps: str = "", recurring: bool = False) -> str:
    """创建一个新的目标计划。

    Args:
        title: 目标标题
        description: 目标详细描述
        steps: 执行步骤，用 | 分隔（如 "搜索资料|分析数据|总结报告"）
        recurring: 是否为循环计划，完成后自动重置步骤为 pending 并恢复 active

    注意：非循环目标完成后需调用 delete_goal(goal_id) 删除。
    """
    if _store is None:
        return json.dumps({"error": "MemoryStore 不可用"}, ensure_ascii=False)

    step_list = [s.strip() for s in steps.split("|") if s.strip()] if steps else []
    goal = _make_goal(title, description, step_list, recurring)

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
    group=_GROUP, tags=["planning", "heartbeat"],
    description=(
        "列出目标计划。检查 active 状态的目标，"
        "已完成的用 update_goal 标记为 completed 或用 delete_goal 删除。"
    ),
)
async def list_goals(status: str = "active") -> str:
    """列出目标计划。

    Args:
        status: 筛选状态，active（默认）/ completed / all
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
    group=_GROUP, tags=["planning", "heartbeat"],
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
    """
    if _store is None:
        return json.dumps({"error": "MemoryStore 不可用"}, ensure_ascii=False)

    target_entry, target_goal = await _find_goal(_store, goal_id)
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

    # 原地更新（保留 id 与时间戳），内容变更后清空旧向量待后台 worker 重建
    target_entry.content = json.dumps(target_goal, ensure_ascii=False)
    target_entry.importance = 0.8 if target_goal["status"] == "active" else 0.3
    target_entry.metadata = {"goal_id": goal_id, "status": target_goal["status"]}
    await _store.update(target_entry, clear_embedding=True)
    target_goal["memory_id"] = target_entry.id

    # 发射步骤进度事件（前端 PlanPanel 浮窗据此打勾）
    try:
        scope = _current_scope()
        _user_scope, chat_id = _parse_scope_chat_id(scope)
        if 0 <= step_index < len(target_goal.get("steps", [])):
            await event_bus.emit(EVENT_PLAN_STEP_UPDATED, {
                "scope": scope,
                "chat_id": chat_id,
                "plan_id": goal_id,
                "step_index": step_index,
                "step_status": step_status or target_goal["steps"][step_index].get("status", "pending"),
                "note": note,
                "ts": time.time(),
            })

            # 程序级自动推进：当前步骤 completed 后，自动把下一步 pending → in_progress
            if step_status == "completed":
                next_idx = step_index + 1
                steps = target_goal.get("steps", [])
                if next_idx < len(steps) and steps[next_idx].get("status") == "pending":
                    steps[next_idx]["status"] = "in_progress"
                    # 同步更新 MemoryStore（让 list_goals 也能看到）
                    target_entry.content = json.dumps(target_goal, ensure_ascii=False)
                    await _store.update(target_entry, clear_embedding=False)
                    await event_bus.emit(EVENT_PLAN_STEP_UPDATED, {
                        "scope": scope,
                        "chat_id": chat_id,
                        "plan_id": goal_id,
                        "step_index": next_idx,
                        "step_status": "in_progress",
                        "note": "自动推进",
                        "ts": time.time(),
                    })

        if goal_status:
            await event_bus.emit(EVENT_PLAN_STATUS_CHANGED, {
                "scope": scope,
                "chat_id": chat_id,
                "plan_id": goal_id,
                "goal_status": target_goal["status"],
                "ts": time.time(),
            })
    except Exception as exc:
        log(f"update_goal 事件发射失败（不影响结果）: {exc}", "DEBUG", tag="规划")

    result: Dict[str, Any] = {"success": True, "goal": target_goal}
    if target_goal["status"] in ("completed", "cancelled"):
        result["hint"] = f"目标已标记为 {target_goal['status']}，建议立即调用 delete_goal('{goal_id}') 删除"
    return json.dumps(result, ensure_ascii=False)


@deferred_tool(
    group=_GROUP, tags=["planning", "heartbeat"],
    description=(
        "删除一个目标计划。"
        "完成目标后应立即调用此工具删除，避免已完成目标干扰记忆召回。"
    ),
)
async def delete_goal(goal_id: str) -> str:
    """删除一个目标计划。

    Args:
        goal_id: 目标 ID
    """
    if _store is None:
        return json.dumps({"error": "MemoryStore 不可用"}, ensure_ascii=False)

    target_entry, target_goal = await _find_goal(_store, goal_id)
    if target_entry is not None and target_goal is not None and target_entry.id:
        await _store.delete(target_entry.id)
        return json.dumps({
            "success": True,
            "message": f"目标 '{goal_id}' 已删除",
            "deleted_goal": target_goal.get("title", ""),
        }, ensure_ascii=False)

    return json.dumps({"error": f"目标 '{goal_id}' 不存在"}, ensure_ascii=False)


@deferred_tool(group=_GROUP, tags=["planning", "heartbeat"])
async def get_goal(goal_id: str) -> str:
    """获取单个目标的详细信息。

    Args:
        goal_id: 目标 ID
    """
    if _store is None:
        return json.dumps({"error": "MemoryStore 不可用"}, ensure_ascii=False)

    target_entry, target_goal = await _find_goal(_store, goal_id)
    if target_entry is not None and target_goal is not None:
        target_goal["memory_id"] = target_entry.id
        return json.dumps({"success": True, "goal": target_goal}, ensure_ascii=False)

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
                summary = f"{data.get('goal_id', '?')}: {title} ({done}/{len(steps)} 步)"
                goals.append(summary)
        return goals
    except Exception:
        return []


@deferred_tool(
    group=_GROUP, tags=["planning", "always"],
    description=(
        "在执行复杂任务前，把计划公告给用户（自发 Plan 模式）。"
        "适用场景：多步骤任务、涉及文件修改、不可逆操作、需求有歧义。"
        "调用后立即返回 plan_id，**无需等待用户批准**，按计划步骤继续执行。"
        "执行中通过 update_goal 更新步骤状态，前端浮窗会实时展示进度。"
        "如果用户取消计划，你会在后续轮次收到中断信号，需立即停止后续步骤。"
    ),
)
async def present_plan(goal: str, steps: str, files: str = "", risks: str = "") -> str:
    """把执行计划公告给用户，并立即开始执行。

    Args:
        goal: 计划目标（一句话）
        steps: 执行步骤（每步一行，含顺序）
        files: 涉及的文件/资源（可选，逗号或换行分隔）
        risks: 风险与注意事项（可选）

    Returns:
        JSON：``{"ok": True, "plan_id": ..., "status": "executing", "message": ...}``

    Notes:
        - 同时向 MemoryStore 持久化目标（复用 create_goal 路径），便于 list_goals 追踪。
        - 同步发射 ``EVENT_PLAN_SUBMITTED`` 事件（scope/plan_id/goal/steps/files/risks），
          前端 PlanPanel 浮窗与 PlanCard 卡片据此渲染。
        - 前端事件失败不影响工具返回（仅记日志）。
    """
    scope = _current_scope()
    _user_scope, chat_id = _parse_scope_chat_id(scope)
    plan_id = uuid.uuid4().hex[:8]

    # 步骤解析：兼容 \n 或 | 分隔
    raw_steps = [
        s.strip()
        for s in (steps.replace("|", "\n").split("\n") if steps else [])
        if s.strip()
    ]
    step_objs = [
        {"index": i, "content": s, "status": "pending", "note": ""}
        for i, s in enumerate(raw_steps)
    ]
    # 程序级自动推进：第 1 步立即标记为 in_progress（前端立即看到"正在执行"）
    if step_objs:
        step_objs[0]["status"] = "in_progress"

    # 持久化到 MemoryStore（与 create_goal 同结构），便于 Agent 后续 update_goal
    if _store is not None:
        try:
            goal_doc = _make_goal(goal, description="", steps=raw_steps, recurring=False)
            # 覆盖 goal_id 为 plan_id，保持前后端 ID 一致
            goal_doc["goal_id"] = plan_id
            goal_doc["steps"] = step_objs  # 用已标记 in_progress 的步骤覆盖默认全 pending
            goal_doc["files"] = files
            goal_doc["risks"] = risks
            entry = MemoryEntry(
                memory_type=MemoryType.SEMANTIC,
                content=json.dumps(goal_doc, ensure_ascii=False),
                source=_GOAL_SOURCE,
                importance=0.8,
                metadata={
                    "goal_id": plan_id,
                    "status": "active",
                    "kind": "present_plan",
                    "scope": scope,
                },
            )
            await _store.add(entry)
        except Exception as exc:
            log(f"present_plan 持久化失败（不影响执行）: {exc}", "WARNING", tag="规划")

    # 发射 plan_submitted 事件（前端 PlanPanel / PlanCard 据此渲染）
    try:
        await event_bus.emit(EVENT_PLAN_SUBMITTED, {
            "scope": scope,
            "chat_id": chat_id,
            "plan_id": plan_id,
            "goal": goal,
            "steps": step_objs,
            "files": files,
            "risks": risks,
            "ts": time.time(),
        })
    except Exception as exc:
        log(f"present_plan 事件发射失败（不影响执行）: {exc}", "DEBUG", tag="规划")

    # 发射 step 0 in_progress 事件（与 MemoryStore 状态一致）
    if step_objs:
        try:
            await event_bus.emit(EVENT_PLAN_STEP_UPDATED, {
                "scope": scope,
                "chat_id": chat_id,
                "plan_id": plan_id,
                "step_index": 0,
                "step_status": "in_progress",
                "note": "自动推进",
                "ts": time.time(),
            })
        except Exception as exc:
            log(f"present_plan 自动推进步骤失败（不影响执行）: {exc}", "DEBUG", tag="规划")

    return json.dumps({
        "ok": True,
        "plan_id": plan_id,
        "status": "executing",
        "message": (
            f"计划已公告 (plan_id={plan_id})，立即开始执行即可。"
            "步骤进度由系统自动追踪，无需手动维护；"
            "如某步完成质量较好，可选调用 update_goal 精确标记。"
        ),
    }, ensure_ascii=False)
