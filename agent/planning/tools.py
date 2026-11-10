"""自主规划工具 — 创建、追踪和管理目标计划。

规划是 Agent 的核心认知能力，MemoryStore 引用经 ``planning_store_port``
晚绑定端口分发（工具 import 时注册、拿不到构造参数；由 agent.runtime.wiring
统一施绑，tracker 与工具组共用同一端口）。

两种规划生命周期（以 metadata.kind 判别，详见 tracker 模块说明）：
- Plan 模式（present_plan → PLAN_KIND）：Agent 自发提交计划后立即开始
  执行，**不**等待用户批准（不走 ApprovalGate），进度经 update_goal 依据
  执行结果明确更新；用户通过浮窗"取消"按钮触发 ``EVENT_PLAN_CANCELLED``
  （cancel-plan 路由 → tracker.cancel_plan）。
- 持久目标（create_goal → GOAL_KIND）：跨会话长期目标，AI 经 update_goal
  手动推进步骤；关闭只由完成事实驱动——终态（completed/cancelled）即删、
  全部步骤完成后自动收口，长期停滞由心跳产出事实概况供 AI 决策，系统
  不做基于时间的自动清理。

态势注入与 not_found 自纠上下文统一由 ``agent.planning.situation`` 提供，
本文件全部写路径变更后调 ``situation.invalidate()`` 保持快照新鲜。

Model Experience: update_goal 永驻，计划步骤须依据结果显式更新；调用次数不代表进度。
Token effect: 未召回规划工具的会话增加一个工具 schema，约数百 token。
Cache effect: 常驻 schema 在会话内冻结，不向稳定消息前缀注入实时进度。
Model Experience: 显式查询可查看归档与终态结果；更新必须携带版本，步骤按稳定 ID 或完整名称校验。
Token effect: 归档目标按需增加时间与原因字段，不新增模型调用。
Cache effect: 归档仅进入工具结果，清理留痕不写稳定前缀或新增记忆。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry
from agent.planning import goals as goal_ops
from agent.planning import situation, tracker
from agent.planning.tracker import planning_store_port
from core.entity import EntityRegistry
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from core.tool_registry import deferred_tool

EntityRegistry.register_group_order("planning", 20)

_GROUP = "planning"

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


def _goal_cleanup_conflict() -> str:
    """基于旧目标快照的删除未提交，要求重读再决策。"""
    return tool_error(
        "目标已发生变更或不再存在，本次清理未执行", cause=ErrorCause.STATE, retryable=False,
        hint="先用 get_goal 读取当前目标，再按实际结果更新",
    )


async def _find_goal(
    goal_id: str,
) -> tuple[Optional[MemoryEntry], Optional[Dict[str, Any]]]:
    """按 goal_id 定位记忆条目与目标数据（委托 tracker 统一实现）。"""
    return await tracker.find_goal_by_id(goal_id)


async def _goal_not_found(goal_id: str) -> str:
    """目标不存在的统一错误：附活跃目标简报供 AI 立即自纠（换用存在的
    goal_id），而非对着过期 id 反复重试。"""
    archived, goal = await tracker.find_goal_by_id(goal_id, include_archived=True)
    if archived is not None and goal is not None and archived.archived_at:
        return tool_error(
            f"目标 '{goal_id}' 已归档", cause=ErrorCause.STATE, retryable=False,
            hint=f"先 get_goal 查看，再 restore_memory(memory_id={archived.id}) 恢复后操作",
            memory_id=archived.id, archive_reason=archived.archive_reason,
        )
    store = _bound_store()
    receipt = await goal_ops.terminal_receipt(store, goal_id) if store is not None else None
    if receipt is not None:
        return tool_error(f"目标 '{goal_id}' 已结束", cause=ErrorCause.STATE, retryable=False,
                          hint="可用 get_goal 查看终态结果，不能继续更新已结束目标", receipt=receipt)
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

    try:
        goal = await goal_ops.create_goal(
            store, title, description, [s.strip() for s in steps.split("|") if s.strip()], recurring=recurring,
        )
        return json.dumps({"success": True, "goal": goal}, ensure_ascii=False)
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
    except Exception as exc:
        return error_from_exception(exc, action="创建目标")


@deferred_tool(
    group=_GROUP, tags=["planning", "heartbeat"],
    description="列出目标计划。检查 active 状态的目标，废弃的用 delete_goal 删除。",
)
async def list_goals(status: str = "active", include_archived: bool = False, offset: int = 0) -> str:
    """列出目标计划。

    Args:
        offset: 匹配目标的分页偏移，每页最多 50 条
        status: 筛选状态，active（默认）/ all
        include_archived: 显式包含已归档目标并标注归档时间与原因，默认仅活跃库
    """
    store = _bound_store()
    if store is None:
        return _store_not_ready()

    if offset < 0:
        return tool_error("offset 不能为负数", cause=ErrorCause.PARAM, retryable=False)
    goals: list[dict[str, Any]] = []
    matched = 0
    async for entry, goal in goal_ops.iter_goals(store, status=status, include_archived=include_archived):
        matched += 1
        if matched <= offset:
            continue
        goals.append(goal_ops.goal_view(entry, goal))
        if len(goals) > 50:
            break
    return json.dumps({"goals": goals[:50], "total": min(len(goals), 50), "filter": status,
                       "has_more": len(goals) > 50,
                       "next_offset": offset + 50 if len(goals) > 50 else None}, ensure_ascii=False)


@deferred_tool(
    group=_GROUP, tags=["planning", "heartbeat", "always"],
    description=(
        "更新目标计划：步骤状态、整体状态或文本（标题/描述），空参数不变。"
        "标记 goal_status 为 completed/cancelled 时目标自动清理；"
        "全部步骤标记完成后目标同样自动收口——均无需再调 delete_goal。"
    ),
)
async def update_goal(
    goal_id: str, step_index: int = -1, step_status: str = "", note: str = "",
    goal_status: str = "", title: str = "", description: str = "",
    step_id: str = "", step_name: str = "", expected_version: int = 0, note_mode: str = "replace",
) -> str:
    """依据读取快照更新目标；终态结果保存到审计后清理活动条目。

    Args:
        goal_id: 目标 ID
        step_index: 可选索引（从 0 开始），须同时提供 step_id 或 step_name 校验
        step_status: pending / in_progress / completed / skipped
        note: 步骤备注
        goal_status: active / completed / cancelled，空串不变
        title: 新标题，空串不变
        description: 新描述，空串不变，clear/none 清空
        step_id: get_goal 返回的稳定步骤 ID，优先使用
        step_name: 步骤完整名称，必须唯一；同时传索引时校验二者一致
        expected_version: 必须传入最近 get_goal/create_goal/update_goal 返回的 version
        note_mode: replace 替换备注，append 追加备注
    """
    store = _bound_store()
    if store is None:
        return _store_not_ready()
    target_entry, target_goal = await _find_goal(goal_id)
    if target_entry is None or target_goal is None:
        return await _goal_not_found(goal_id)
    if not (step_status or note or goal_status or title.strip() or description.strip()):
        return tool_error("没有任何字段需要更新", cause=ErrorCause.PARAM, retryable=False,
                          hint="可更新 step_status/note/goal_status/title/description 之一")
    try:
        result = await goal_ops.update_goal(
            store, target_entry, target_goal, expected_version=expected_version,
            step_index=step_index, step_id=step_id, step_name=step_name, step_status=step_status,
            note=note, note_mode=note_mode, status=goal_status or None, title=title.strip() or None,
            description=("" if description.strip().lower() in ("clear", "none") else description.strip())
                        if description.strip() else None,
        )
        return json.dumps({"success": True, "goal": result, "goal_id": goal_id,
                           "memory_id": result["memory_id"], "title": result["title"],
                           **({"message": f"目标 '{goal_id}' 已标记为 {result['status']}，自动收口并自动清理"}
                              if result.get("deleted") else {})}, ensure_ascii=False)
    except goal_ops.GoalConflict as exc:
        return tool_error(str(exc), cause=ErrorCause.STATE, retryable=False, hint="先 get_goal 重读后再决策")
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False, hint=str(exc),
                          steps=[f"{i}: {s.get('content', '')}" for i, s in enumerate(target_goal.get("steps", []))])
    except Exception as exc:
        return error_from_exception(exc, action="更新目标")


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
        if not await tracker.remove_goal(target_entry, goal=target_goal):
            return _goal_cleanup_conflict()
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

    target_entry, target_goal = await tracker.find_goal_by_id(goal_id, include_archived=True)
    if target_entry is not None and target_goal is not None:
        target_goal = goal_ops.goal_view(target_entry, target_goal)
        if target_entry.archived_at:
            return json.dumps({
                "success": True, "goal": target_goal, "archived": True,
                "archived_at": target_entry.archived_at, "archive_reason": target_entry.archive_reason,
                "hint": "目标已归档；先 restore_memory 恢复后才能更新，不会自动恢复",
            }, ensure_ascii=False)
        # 反查关联记忆（goal:{id} 标签双链），让 AI 看到目标下已沉淀的内容
        related = await store.search_by_tags([f"goal:{goal_id}"], limit=20)
        related_count = sum(1 for e in related if e.id != target_entry.id)
        return json.dumps({
            "success": True,
            "goal": target_goal,
            "related_memory_count": related_count,
        }, ensure_ascii=False)

    receipt = await goal_ops.terminal_receipt(store, goal_id)
    if receipt is not None:
        return json.dumps({"success": True, "goal": receipt, "deleted": True}, ensure_ascii=False)
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
        "默认对需要公开跟踪的多步任务先调用本工具；单步问答和已委托的后台任务无需重复计划。"
        "用户能在浮窗中实时看到计划步骤与进度。"
        "适用：多步骤任务、信息搜集分析、目录/文件操作、代码修改、任何需要 2 步以上的工作。"
        "调用后立即返回 plan_id，无需等待批准，直接开始执行；"
        "步骤须依据执行结果用 update_goal 明确更新，系统不会根据工具调用次数判定完成。"
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
            "goal": existing,
            "message": (
                f"已有进行中的计划 (plan_id={plan_id})，进度 {done}/{len(steps_list)}，"
                f"请继续执行剩余步骤，不要重新规划：\n{lines}"
            ),
        }, ensure_ascii=False)

    step_objs = tracker.parse_steps(steps)
    plan_id = await tracker.submit_plan(scope, goal, step_objs, files=files, risks=risks)
    entry, stored_goal = await _find_goal(plan_id)

    return json.dumps({
        "ok": True,
        "plan_id": plan_id,
        "status": "executing",
        "goal": goal_ops.goal_view(entry, stored_goal) if entry is not None and stored_goal is not None else None,
        "message": (
            f"计划已公告 (plan_id={plan_id})，立即开始执行即可。"
            "步骤完成后用 update_goal 标记并记录结果证据；"
            "查询或失败不算完成，未确认完成的计划在会话结束时按取消收束。"
        ),
    }, ensure_ascii=False)
