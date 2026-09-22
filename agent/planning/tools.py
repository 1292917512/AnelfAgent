"""自主规划工具 — 创建、追踪和管理目标计划。

规划是 Agent 的核心认知能力，MemoryStore 引用经 ``planning_store_port``
晚绑定端口分发（工具 import 时注册、拿不到构造参数；由 agent.runtime.wiring
统一施绑，tracker 与工具组共用同一端口）。

两种规划生命周期（以 metadata.kind 判别，详见 tracker 模块说明）：
- Plan 模式（present_plan → PLAN_KIND）：Agent 自发提交计划后立即开始
  执行，**不**等待用户批准（不走 ApprovalGate），进度由 tracker 程序级
  推断；用户通过浮窗"取消"按钮触发 ``EVENT_PLAN_CANCELLED``
  （cancel-plan 路由 → tracker.cancel_plan）。
- 持久目标（create_goal → GOAL_KIND）：跨会话长期目标，AI 经 update_goal
  手动推进步骤；关闭只由完成事实驱动——终态（completed/cancelled）即删、
  全部步骤完成后自动收口，长期停滞由心跳产出事实概况供 AI 决策，系统
  不做基于时间的自动清理。

态势注入与 not_found 自纠上下文统一由 ``agent.planning.situation`` 提供，
本文件全部写路径变更后调 ``situation.invalidate()`` 保持快照新鲜。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import GOAL_SOURCE, MemoryEntry, MemoryType
from agent.planning import situation, tracker
from agent.planning.tracker import GOAL_KIND, planning_store_port
from core.entity import EntityRegistry
from core.log import log
from core.tool_errors import ErrorCause, tool_error
from entities._sdk import deferred_tool

EntityRegistry.register_group_order("planning", 20)

_GROUP = "planning"

# update_goal 允许的步骤终态（与 tracker 状态机一致）
_STEP_STATUSES = frozenset({"pending", "in_progress", "completed", "skipped"})
# update_goal 允许的目标整体状态
_GOAL_STATUSES = frozenset({"active", "completed", "cancelled"})
# 可清空字段的占位值（description 传 clear/none 置空）
_CLEAR_TOKENS = frozenset({"clear", "none"})


def _bound_store() -> Optional[MemoryStore]:
    """取 MemoryStore（端口未施绑时返回 None，工具降级为未就绪错误）。"""
    return planning_store_port.get() if planning_store_port.bound else None


def _store_not_ready() -> str:
    """记忆存储未就绪的统一错误。"""
    return tool_error(
        "记忆存储组件未初始化",
        cause=ErrorCause.STATE, retryable=False,
        hint="MemoryStore 不可用，请检查服务启动状态",
    )


async def _find_goal(
    goal_id: str,
) -> tuple[Optional[MemoryEntry], Optional[Dict[str, Any]]]:
    """按 goal_id 定位记忆条目与目标数据（委托 tracker 统一实现）。"""
    return await tracker.find_goal_by_id(goal_id)


async def _goal_not_found(goal_id: str) -> str:
    """目标不存在的统一错误：附活跃目标简报供 AI 立即自纠（换用存在的
    goal_id），而非对着过期 id 反复重试。"""
    briefs = await situation.active_goal_briefs()
    hint = (
        "目标可能已被删除或收敛；请改用活跃目标列表中的 goal_id，或 list_goals 确认全貌"
        if briefs
        else "当前无活跃目标（可能已删除/完成）；如需新目标用 create_goal 创建"
    )
    return tool_error(
        f"目标 '{goal_id}' 不存在",
        cause=ErrorCause.NOT_FOUND, retryable=False,
        hint=hint, active_goals=briefs or None,
    )


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
        "创建一个新的目标计划（可为长期目标，存续由你管理，系统不按时间自动清理）。"
        "创建后记住 goal_id，推进进度用 update_goal；"
        "标记 completed/cancelled 时目标会自动清理，无需手动删除。"
    ),
)
async def create_goal(title: str, description: str = "", steps: str = "", recurring: bool = False) -> str:
    """创建一个新的目标计划。

    Args:
        title: 目标标题
        description: 目标详细描述
        steps: 执行步骤，用 | 分隔（如 "搜索资料|分析数据|总结报告"）
        recurring: 是否为循环计划，完成后自动重置步骤为 pending 并恢复 active
    """
    store = _bound_store()
    if store is None:
        return _store_not_ready()

    step_list = [s.strip() for s in steps.split("|") if s.strip()] if steps else []
    goal = _make_goal(title, description, step_list, recurring)

    entry = MemoryEntry(
        memory_type=MemoryType.SEMANTIC,
        content=json.dumps(goal, ensure_ascii=False),
        source=GOAL_SOURCE,
        importance=0.8,
        # goal:{id} 标签：记忆联想网络沿该标签把相关记忆与目标互链
        tags=[f"goal:{goal['goal_id']}"],
        # kind 声明生命周期归属：持久目标只由 AI 手动推进，plan 状态机
        # （自动推进/会话收敛）不触碰；无 scope——目标是跨会话的全局对象
        metadata={"goal_id": goal["goal_id"], "status": "active", "kind": GOAL_KIND},
    )
    entry_id = await store.add(entry, actor="planning")
    situation.invalidate()
    goal["memory_id"] = entry_id
    return json.dumps({"success": True, "goal": goal}, ensure_ascii=False)


@deferred_tool(
    group=_GROUP, tags=["planning", "heartbeat"],
    description="列出目标计划。检查 active 状态的目标，废弃的用 delete_goal 删除。",
)
async def list_goals(status: str = "active") -> str:
    """列出目标计划。

    Args:
        status: 筛选状态，active（默认）/ all
    """
    store = _bound_store()
    if store is None:
        return _store_not_ready()

    entries = await store.list_recent(limit=50, memory_type=MemoryType.SEMANTIC, source=GOAL_SOURCE)

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
        "更新目标计划：步骤状态、整体状态或文本（标题/描述），空参数不变。"
        "标记 goal_status 为 completed/cancelled 时目标自动清理；"
        "全部步骤标记完成后目标同样自动收口——均无需再调 delete_goal。"
    ),
)
async def update_goal(
    goal_id: str,
    step_index: int = -1,
    step_status: str = "",
    note: str = "",
    goal_status: str = "",
    title: str = "",
    description: str = "",
) -> str:
    """更新目标计划的步骤状态、整体状态或文本。

    Args:
        goal_id: 目标 ID
        step_index: 要更新的步骤索引（-1 表示不更新步骤）
        step_status: 步骤状态（pending / in_progress / completed / skipped）
        note: 步骤备注
        goal_status: 整体目标状态（active / completed / cancelled），留空不更新；
            终态写入即自动删除该目标
        title: 新标题（空串不变）
        description: 新描述（空串不变；clear/none 置空）
    """
    store = _bound_store()
    if store is None:
        return _store_not_ready()

    target_entry, target_goal = await _find_goal(goal_id)
    if target_entry is None or target_goal is None:
        return await _goal_not_found(goal_id)

    if not (step_status or note or goal_status or title.strip() or description.strip()):
        return tool_error(
            "没有任何字段需要更新",
            cause=ErrorCause.PARAM, retryable=False,
            hint="可更新 step_status/note/goal_status/title/description 之一",
        )

    steps: List[Dict[str, Any]] = target_goal.get("steps", [])
    # 参数诚实校验：越界索引/非法状态此前被静默忽略（返回 success 但未生效），
    # AI 误以为已标记——错误 + 步骤概览让它一次修正
    if (step_status or note) and not 0 <= step_index < len(steps):
        if not steps:
            return tool_error(
                f"目标 '{goal_id}' 没有步骤，无法更新步骤状态",
                cause=ErrorCause.PARAM, retryable=False,
                hint="仅当创建目标时提供了 steps 才有步骤；如需记录进度可先补建带步骤的目标",
            )
        return tool_error(
            f"步骤索引 {step_index} 超出范围（有效 0~{len(steps) - 1}）",
            cause=ErrorCause.PARAM, retryable=False,
            hint="步骤索引从 0 起；目标步骤如下",
            steps=[f"{i}: {s.get('content', '')}" for i, s in enumerate(steps)],
        )
    if step_status and step_status not in _STEP_STATUSES:
        return tool_error(
            f"非法步骤状态 '{step_status}'",
            cause=ErrorCause.PARAM, retryable=False,
            hint=f"有效值: {' / '.join(sorted(_STEP_STATUSES))}",
        )
    if goal_status and goal_status not in _GOAL_STATUSES:
        return tool_error(
            f"非法目标状态 '{goal_status}'",
            cause=ErrorCause.PARAM, retryable=False,
            hint="有效值: active / completed / cancelled",
        )

    if title.strip():
        target_goal["title"] = title.strip()
    if description.strip():
        target_goal["description"] = (
            "" if description.strip().lower() in _CLEAR_TOKENS else description.strip()
        )

    if 0 <= step_index < len(steps):
        if step_status:
            steps[step_index]["status"] = step_status
        if note:
            steps[step_index]["note"] = note

    if goal_status == "completed" and target_goal.get("recurring"):
        # 循环目标完成即重置：步骤归零、恢复 active，跨周期存续
        for s in target_goal.get("steps", []):
            s["status"] = "pending"
            s["note"] = ""
        target_goal["status"] = "active"
    elif goal_status in ("completed", "cancelled"):
        # 终态即清：不保留终态条目（删除 + 前端卡片移除 + 态势同步）
        await tracker.remove_goal(target_entry)
        return json.dumps({
            "success": True,
            "message": f"目标 '{goal_id}' 已标记为 {goal_status} 并自动清理",
            "goal_id": goal_id,
            "title": target_goal.get("title", ""),
        }, ensure_ascii=False)
    elif goal_status:
        target_goal["status"] = goal_status

    # 全步骤完成自动收口：本轮推进步骤后计划内步骤全部 done（completed/
    # skipped）且无显式整体状态指令时关闭目标——完成事实驱动的唯一自动
    # 关闭路径，不因停滞时间等其他原因关闭
    if (
        step_status
        and not goal_status
        and steps
        and not target_goal.get("recurring")
        and target_goal.get("status") == "active"
        and all(s.get("status") in ("completed", "skipped") for s in steps)
    ):
        try:
            if 0 <= step_index < len(steps):
                await tracker._emit_step(
                    tracker.current_scope(), goal_id, step_index,
                    steps[step_index].get("status", "completed"), note=note,
                )
        except Exception as exc:
            log(f"update_goal 事件发射失败（不影响结果）: {exc}", "DEBUG", tag="规划")
        await tracker.remove_goal(target_entry)
        return json.dumps({
            "success": True,
            "message": f"目标 '{goal_id}' 全部步骤已完成，自动收口清理",
            "goal_id": goal_id,
            "title": target_goal.get("title", ""),
        }, ensure_ascii=False)

    target_goal["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # 原地更新（保留 id 与时间戳），内容变更后清空旧向量待后台 worker 重建
    target_entry.content = json.dumps(target_goal, ensure_ascii=False)
    target_entry.importance = 0.8 if target_goal["status"] == "active" else 0.3
    # 合并更新 metadata：保留 kind/scope 等既有键（present_plan 的 scope 隔离依赖它们），
    # 整体覆盖会导致计划跨 scope 泄漏
    target_entry.metadata = {
        **(target_entry.metadata or {}),
        "goal_id": goal_id,
        "status": target_goal["status"],
    }
    await store.update(target_entry, clear_embedding=True)
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
                    await store.update(target_entry, clear_embedding=False)
                    await tracker._emit_step(scope, goal_id, next_idx, "in_progress", note="自动推进")

        if goal_status:
            await tracker._emit_status(scope, goal_id, target_goal["status"])
    except Exception as exc:
        log(f"update_goal 事件发射失败（不影响结果）: {exc}", "DEBUG", tag="规划")
    situation.invalidate()

    result: Dict[str, Any] = {"success": True, "goal": target_goal}
    return json.dumps(result, ensure_ascii=False)


@deferred_tool(
    group=_GROUP, tags=["planning", "heartbeat"],
    description=(
        "删除一个目标计划（用于废弃仍在进行的目标）。"
        "已完成/取消的目标无需本工具：update_goal 标记终态时自动清理。"
    ),
)
async def delete_goal(goal_id: str) -> str:
    """删除一个目标计划。

    Args:
        goal_id: 目标 ID
    """
    store = _bound_store()
    if store is None:
        return _store_not_ready()

    target_entry, target_goal = await _find_goal(goal_id)
    if target_entry is not None and target_goal is not None and target_entry.id:
        # 删除 + 前端卡片移除（EVENT_PLAN_DELETED）+ 态势同步统一走 tracker
        await tracker.remove_goal(target_entry)
        return json.dumps({
            "success": True,
            "message": f"目标 '{goal_id}' 已删除",
            "deleted_goal": target_goal.get("title", ""),
        }, ensure_ascii=False)

    return await _goal_not_found(goal_id)


@deferred_tool(group=_GROUP, tags=["planning", "heartbeat"])
async def get_goal(goal_id: str) -> str:
    """获取单个目标的详细信息。

    Args:
        goal_id: 目标 ID
    """
    store = _bound_store()
    if store is None:
        return _store_not_ready()

    target_entry, target_goal = await _find_goal(goal_id)
    if target_entry is not None and target_goal is not None:
        target_goal["memory_id"] = target_entry.id
        # 反查关联记忆（goal:{id} 标签双链），让 AI 看到目标下已沉淀的内容
        related = await store.search_by_tags([f"goal:{goal_id}"], limit=20)
        related_count = sum(1 for e in related if e.id != target_entry.id)
        return json.dumps({
            "success": True,
            "goal": target_goal,
            "related_memory_count": related_count,
        }, ensure_ascii=False)

    return await _goal_not_found(goal_id)


# ------------------------------------------------------------------
# 公共查询函数
# ------------------------------------------------------------------
# 活跃目标的态势消费（上下文注入 / 错误简报 / 自主循环收集）统一由
# agent.planning.situation 提供（版本化快照，单一数据源）。


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
