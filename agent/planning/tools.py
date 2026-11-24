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
  手动推进步骤；关闭只由完成事实驱动——终态（completed/cancelled）归档、
  全部步骤完成后自动收口，长期停滞由心跳产出事实概况供 AI 决策，系统
  不做基于时间的自动清理。

态势注入与 not_found 自纠上下文统一由 ``agent.planning.situation`` 提供，
本文件全部写路径变更后调 ``situation.invalidate()`` 保持快照新鲜。

Model Experience: update_goal 永驻，计划步骤须依据结果显式更新；调用次数不代表进度。
Token effect: 未召回规划工具的会话增加一个工具 schema，约数百 token。
Cache effect: 常驻 schema 在会话内冻结，不向稳定消息前缀注入实时进度。
Model Experience: 查询返回目标版本；更新和删除携带所见版本，冲突后由模型重读并决定。
Token effect: 删除请求多传一个版本整数，归档查询按需返回时间与原因。
Cache effect: 版本随工具参数和结果传递，目标状态与清理留痕位于稳定前缀之外。
"""

from __future__ import annotations

import json
from typing import Any

from agent.planning import goals as goal_ops
from agent.planning import situation, tracker
from core.entity import EntityRegistry
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from core.tool_registry import deferred_tool

EntityRegistry.register_group_order("planning", 20)

_GROUP = "planning"


def _store_not_ready() -> str:
    """记忆存储未就绪的统一错误。"""
    return tool_error(
        "记忆存储组件未初始化",
        cause=ErrorCause.STATE, retryable=False,
        hint="MemoryStore 不可用，请检查服务启动状态",
    )


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
    store = tracker.get_planning_store()
    receipt = await goal_ops.terminal_receipt(store, goal_id) if store is not None else None
    if receipt is not None:
        return tool_error(f"目标 '{goal_id}' 已结束", cause=ErrorCause.STATE, retryable=False,
                          hint="可用 get_goal 查看终态结果，不能继续更新已结束目标", receipt=receipt)
    briefs = await situation.active_goal_briefs()
    hint = (
        "目标可能已被删除或收敛；请改用活跃目标列表中的 goal_id，或 list_goals 确认全貌"
        if briefs
        else "当前无活跃目标；list_goals(include_archived=True) 可按标题查找历史目标，需要新目标时用 create_goal"
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
        "标记 completed/cancelled 时目标可恢复归档，无需手动删除。"
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
    store = tracker.get_planning_store()
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
    description="列出目标计划。默认展示活跃目标；找已结束目标可传 include_archived=True，再按 goal_id 查看全文。",
)
async def list_goals(status: str = "", include_archived: bool = False, offset: int = 0) -> str:
    """列出目标计划。

    Args:
        offset: 匹配目标的分页偏移，每页最多 50 条
        status: active / completed / cancelled / all。省略时普通查询取 active，包含归档时取 all
        include_archived: 显式包含已归档目标并标注归档时间与原因，默认仅活跃库
    """
    store = tracker.get_planning_store()
    if store is None:
        return _store_not_ready()

    if offset < 0:
        return tool_error("offset 不能为负数", cause=ErrorCause.PARAM, retryable=False)
    goals: list[dict[str, Any]] = []
    status = status or ("all" if include_archived else "active")
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
        "单步更新只影响指定步骤，skipped 仍须显式决定是否收口；"
        "全部步骤 completed 或显式设置目标终态时归档，get_goal 可查看详情。"
    ),
)
async def update_goal(
    goal_id: str, step_index: int = -1, step_status: str = "", note: str = "",
    goal_status: str = "", title: str = "", description: str = "",
    step_id: str = "", step_name: str = "", expected_version: int = 0, note_mode: str = "replace",
) -> str:
    """依据读取快照更新目标；终态结果和触发原因可在归档中查看。

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
    store = tracker.get_planning_store()
    if store is None:
        return _store_not_ready()
    target_entry, target_goal = await tracker.find_goal_by_id(goal_id)
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
                           **({"message": f"目标 '{goal_id}' 已标记为 {result['status']}，已归档（{result['archive_reason']}），可用 get_goal 查看"}
                              if result.get("archived") else {})}, ensure_ascii=False)
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
        "废弃仍在进行的目标时，提交最近读取的 version 作为 expected_version。"
        "已完成/取消的目标无需本工具：update_goal 标记终态时自动归档。"
    ),
)
async def delete_goal(goal_id: str, expected_version: int | None = None) -> str:
    """删除一个目标计划。

    Args:
        goal_id: 目标 ID
        expected_version: 最近读取的目标 version，发生冲突后须重读再决策
    """
    store = tracker.get_planning_store()
    if store is None:
        return _store_not_ready()

    target_entry, target_goal = await tracker.find_goal_by_id(goal_id)
    if target_entry is not None and target_goal is not None and target_entry.id:
        # 删除 + 前端卡片移除（EVENT_PLAN_DELETED）+ 态势同步统一走 tracker
        try:
            deleted = await tracker.remove_goal(target_entry, store=store, goal=target_goal, expected_version=expected_version)
        except goal_ops.GoalConflict as exc:
            return tool_error(str(exc), cause=ErrorCause.STATE, retryable=False, hint="先 get_goal 重读后再决策")
        except Exception as exc:
            return error_from_exception(exc, action="删除目标")
        if not deleted:
            return await _goal_not_found(goal_id)
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
    store = tracker.get_planning_store()
    if store is None:
        return _store_not_ready()

    target_entry, target_goal = await tracker.find_goal_by_id(goal_id, include_archived=True)
    if target_entry is not None and target_goal is not None:
        target_goal = goal_ops.goal_view(target_entry, target_goal)
        if target_entry.archived_at:
            return json.dumps({
                "success": True, "goal": target_goal, "archived": True,
                "archived_at": target_entry.archived_at, "archive_reason": target_entry.archive_reason,
                "hint": "可用 restore_memory(memory_id) 取回原记录；要继续推进，再用当前版本设置 goal_status=active。",
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
    if tracker.get_planning_store() is None:
        return _store_not_ready()
    scope = tracker.current_scope()

    # 复用：已有进行中计划时返回现有 plan，提示 AI 继续执行而非重新规划
    try:
        existing = await tracker.get_active_plan(scope)
    except Exception as exc:
        return error_from_exception(exc, action="查询会话计划")
    if existing is not None:
        plan_id = existing.get("goal_id", "")
        steps_list = existing.get("steps", [])
        remaining = [s for s in steps_list if s.get("status") != "completed"]
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
    if not goal.strip() or not step_objs:
        return tool_error("计划目标和步骤不能为空", cause=ErrorCause.PARAM, retryable=False)
    try:
        plan_id = await tracker.submit_plan(scope, goal.strip(), step_objs, files=files, risks=risks)
        entry, stored_goal = await tracker.find_goal_by_id(plan_id, include_archived=True)
    except Exception as exc:
        return error_from_exception(exc, action="提交会话计划")
    if entry is None or stored_goal is None:
        return await _goal_not_found(plan_id)

    return json.dumps({
        "ok": True,
        "plan_id": plan_id,
        "status": "executing",
        "goal": goal_ops.goal_view(entry, stored_goal),
        "message": (
            f"计划已公告 (plan_id={plan_id})，立即开始执行即可。"
            "步骤完成后用 update_goal 标记并记录结果证据；"
            "会话结束后保留未完成步骤，下次可继续。"
        ),
    }, ensure_ascii=False)
