"""自主规划工具 — 创建、追踪和管理目标计划。

规划是 Agent 的核心认知能力，工具直接持有 MemoryStore 引用，
通过 ``register_planning_tools()`` 在运行时注入依赖后批量注册到 EntityRegistry。

Plan 模式（present_plan）：
- Agent 自发提交计划后立即开始执行，**不**等待用户批准（不走 ApprovalGate）。
- 计划状态机与事件发射统一由 ``agent.planning.tracker`` 实现（本文件只做工具包装）。
- 用户通过浮窗"取消"按钮触发 ``EVENT_PLAN_CANCELLED``（cancel-plan 路由 → tracker.cancel_plan）。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.planning import tracker
from core.log import log
from core.tool_errors import ErrorCause, tool_error
from entities._sdk import activate_group, deferred_tool

_GOAL_SOURCE = "goal"
_GROUP = "planning"

_store: Optional[MemoryStore] = None


def _store_not_ready() -> str:
    """记忆存储未就绪的统一错误。"""
    return tool_error(
        "记忆存储组件未初始化",
        cause=ErrorCause.STATE, retryable=False,
        hint="MemoryStore 不可用，请检查服务启动状态",
    )


def register_planning_tools(store: MemoryStore) -> None:
    """注入 MemoryStore 并批量注册规划工具。"""
    global _store
    _store = store
    tracker.bind_store(store)
    activate_group(_GROUP, "目标规划管理 - 创建执行计划、追踪目标进度")


async def _find_goal(
    goal_id: str,
) -> tuple[Optional[MemoryEntry], Optional[Dict[str, Any]]]:
    """按 goal_id 定位记忆条目与目标数据（委托 tracker 统一实现）。"""
    return await tracker.find_goal_by_id(goal_id)


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
        return _store_not_ready()

    step_list = [s.strip() for s in steps.split("|") if s.strip()] if steps else []
    goal = _make_goal(title, description, step_list, recurring)

    entry = MemoryEntry(
        memory_type=MemoryType.SEMANTIC,
        content=json.dumps(goal, ensure_ascii=False),
        source=_GOAL_SOURCE,
        importance=0.8,
        # goal:{id} 标签：记忆联想网络沿该标签把相关记忆与目标互链
        tags=[f"goal:{goal['goal_id']}"],
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
        return _store_not_ready()

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

    result: Dict[str, Any] = {"goals": goals, "total": len(goals), "filter": status}
    if len(entries) >= 50:
        # 达到查询上限：目标可能未全部列出，提示清理而非静默截断
        result["hint"] = "目标数量已达查询上限，可能有更早的目标未列出，建议删除已完成的目标"
    return json.dumps(result, ensure_ascii=False)


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
        return _store_not_ready()

    target_entry, target_goal = await _find_goal(goal_id)
    if target_entry is None or target_goal is None:
        return tool_error(f"目标 '{goal_id}' 不存在", cause=ErrorCause.NOT_FOUND, retryable=False)

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

    # 发射步骤进度事件（前端 PlanPanel 浮窗据此打勾）；
    # 程序级联动：当前步骤 completed 后自动推进下一步为 in_progress
    try:
        scope = tracker.current_scope()
        if 0 <= step_index < len(target_goal.get("steps", [])):
            await tracker._emit_step(
                scope, goal_id, step_index,
                step_status or target_goal["steps"][step_index].get("status", "pending"),
                note=note,
            )
            if step_status == "completed":
                next_idx = step_index + 1
                steps = target_goal.get("steps", [])
                if next_idx < len(steps) and steps[next_idx].get("status") == "pending":
                    steps[next_idx]["status"] = "in_progress"
                    target_entry.content = json.dumps(target_goal, ensure_ascii=False)
                    await _store.update(target_entry, clear_embedding=False)
                    await tracker._emit_step(scope, goal_id, next_idx, "in_progress", note="自动推进")

        if goal_status:
            await tracker._emit_status(scope, goal_id, target_goal["status"])
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
        return _store_not_ready()

    target_entry, target_goal = await _find_goal(goal_id)
    if target_entry is not None and target_goal is not None and target_entry.id:
        await _store.delete(target_entry.id)
        return json.dumps({
            "success": True,
            "message": f"目标 '{goal_id}' 已删除",
            "deleted_goal": target_goal.get("title", ""),
        }, ensure_ascii=False)

    return tool_error(f"目标 '{goal_id}' 不存在", cause=ErrorCause.NOT_FOUND, retryable=False)


@deferred_tool(group=_GROUP, tags=["planning", "heartbeat"])
async def get_goal(goal_id: str) -> str:
    """获取单个目标的详细信息。

    Args:
        goal_id: 目标 ID
    """
    if _store is None:
        return _store_not_ready()

    target_entry, target_goal = await _find_goal(goal_id)
    if target_entry is not None and target_goal is not None:
        target_goal["memory_id"] = target_entry.id
        # 反查关联记忆（goal:{id} 标签双链），让 AI 看到目标下已沉淀的内容
        related = await _store.search_by_tags([f"goal:{goal_id}"], limit=20)
        related_count = sum(1 for e in related if e.id != target_entry.id)
        return json.dumps({
            "success": True,
            "goal": target_goal,
            "related_memory_count": related_count,
        }, ensure_ascii=False)

    return tool_error(f"目标 '{goal_id}' 不存在", cause=ErrorCause.NOT_FOUND, retryable=False)


# ------------------------------------------------------------------
# 公共查询函数（供 Mind 自主循环调用）
# ------------------------------------------------------------------

async def collect_active_goals(store: MemoryStore) -> list[str]:
    """从 MemoryStore 收集活跃目标摘要。"""
    goals = await collect_active_goal_entries(store, limit=10)
    return [f"{g['goal_id']}: {g['title']} ({g['done']}/{g['total']} 步)" for g in goals]


async def collect_active_goal_entries(
    store: MemoryStore,
    *,
    scope: str = "",
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """收集活跃目标（结构化）：对话内计划（present_plan）按 scope 隔离，长期目标全局可见。"""
    try:
        entries = await store.list_by_source(
            _GOAL_SOURCE, memory_type=MemoryType.SEMANTIC, limit=50,
        )
    except Exception:
        return []
    goals: List[Dict[str, Any]] = []
    for entry in entries:
        try:
            data = json.loads(entry.content)
        except (json.JSONDecodeError, AttributeError):
            continue
        if data.get("status") != "active":
            continue
        metadata = entry.metadata or {}
        if metadata.get("kind") == "present_plan" and metadata.get("scope", "") != scope:
            continue
        steps = data.get("steps", [])
        goals.append({
            "goal_id": data.get("goal_id", ""),
            "title": data.get("title", ""),
            "done": sum(1 for s in steps if s.get("status") == "completed"),
            "total": len(steps),
        })
        if len(goals) >= limit:
            break
    return goals


async def build_goals_injection(
    store: MemoryStore,
    *,
    scope: str = "",
    limit: int = 5,
) -> str:
    """构建活跃目标注入块（每轮调用；内容仅在目标变更时字节变化）。"""
    goals = await collect_active_goal_entries(store, scope=scope, limit=limit)
    if not goals:
        return ""
    lines = [
        f"[系统注入·活跃目标] 你当前有 {len(goals)} 个进行中的目标"
        "（list_goals 查看全部，update_goal 推进，完成后 delete_goal 收敛）："
    ]
    for g in goals:
        progress = f"（{g['done']}/{g['total']} 步）" if g["total"] else ""
        title = g["title"][:60]
        lines.append(f"- [{g['goal_id']}] {title}{progress}")
    from core.sanitizer import sanitize_for_context
    return sanitize_for_context("\n".join(lines))


@deferred_tool(
    group=_GROUP, tags=["planning", "always"],
    description=(
        "把任务的执行计划公告给用户（Plan 模式）。"
        "**默认行为：除最简单的单步问答外，所有任务都应先调用本工具再执行**——"
        "用户能在浮窗中实时看到计划步骤与进度。"
        "适用：多步骤任务、信息搜集分析、目录/文件操作、代码修改、任何需要 2 步以上的工作。"
        "调用后立即返回 plan_id，无需等待批准，直接开始执行；"
        "步骤进度由系统自动追踪，无需手动维护。"
        "注意：调用本工具后必须用工具继续执行（禁止只输出文字），用户取消时会收到中断信号。"
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
        - 持久化与事件发射统一由 ``agent.planning.tracker.submit_plan`` 实现。
        - 同 scope 已有 active plan 时**复用**而非新建（防止 AI 重复规划产生重复卡片）。
    """
    scope = tracker.current_scope()

    # 复用：已有进行中计划时返回现有 plan，提示 AI 继续执行而非重新规划
    existing = await tracker.get_active_plan(scope)
    if existing is not None:
        plan_id = existing.get("goal_id", "")
        steps_list = existing.get("steps", [])
        remaining = [s for s in steps_list if s.get("status") in ("pending", "in_progress")]
        done = len(steps_list) - len(remaining)
        lines = "\n".join(
            f"  - 步骤 {s.get('index', 0) + 1}: {s.get('content', '')} ({s.get('status', 'pending')})"
            for s in remaining
        ) or "  （无剩余步骤）"
        return json.dumps({
            "ok": True,
            "plan_id": plan_id,
            "status": "executing",
            "reused": True,
            "message": (
                f"已有进行中的计划 (plan_id={plan_id})，进度 {done}/{len(steps_list)}，"
                f"请继续执行剩余步骤，不要重新规划：\n{lines}"
            ),
        }, ensure_ascii=False)

    step_objs = tracker.parse_steps(steps)
    plan_id = await tracker.submit_plan(scope, goal, step_objs, files=files, risks=risks)

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
