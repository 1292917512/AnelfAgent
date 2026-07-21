"""delegate_task 工具 — 主 Agent 将子任务委托给隔离的子代理执行。

通过 `register_delegation_tools()` 在 Mind 初始化时注入 DelegationManager 后注册。
"""
from __future__ import annotations

import json
from typing import Optional

from core.log import log
from entities._sdk import activate_group, deferred_tool

from agent.delegation.delegation_manager import DelegationManager
from agent.delegation.sub_agent import current_depth, max_spawn_depth

_manager: Optional[DelegationManager] = None


def register_delegation_tools(manager: DelegationManager) -> None:
    """注入委托管理器并注册 delegate_task 工具。"""
    global _manager
    _manager = manager
    count = activate_group("delegation", "子代理 - 复杂任务拆分委托与并行执行")
    log(f"🤖 子代理工具已注册 ({count} 个)", tag="委托")


def _delegation_enabled() -> bool:
    from core.config import get_config_bool
    return get_config_bool("delegation_enabled", True)


@deferred_tool(
    name="delegate_task",
    group="delegation", tags=["always"], source="mind.delegation",
    description="将子任务委托给隔离的子代理执行。适合可独立完成的子任务（调研、分析、批量处理）。"
    "支持 tasks 数组并行委托多个子任务。子代理无法发送消息，只返回文字总结。",
)
async def delegate_task(
        goal: str = "",
        context: str = "",
        tasks: str = "",
        role: str = "leaf",
        background: bool = False,
        max_iterations: int = 0,
) -> str:
    """委托子任务给子代理执行。

    Args:
        goal: 子任务目标（单个任务时必填）
        context: 背景上下文（子代理只能看到 goal+context，看不到主对话）
        tasks: 并行任务数组的 JSON 字符串，如 [{"goal":"...","context":"..."}, ...]（提供时忽略 goal）
        role: 子代理角色：leaf（默认，不可再委托）/ orchestrator（可再委托，有深度限制）
        background: 是否后台执行（立即返回 delegation_id；完成时系统自动通知并触发新一轮回复，
            期间可用 check_background_tasks 查询进度）
        max_iterations: 子代理迭代预算（轮次），默认 15
    """
    if not _delegation_enabled():
        return json.dumps({"error": "子代理委托已禁用"}, ensure_ascii=False)
    if _manager is None:
        return json.dumps({"error": "委托管理器未初始化"}, ensure_ascii=False)

    # 深度硬限制：超过 max_depth 禁止再委托
    depth = current_depth()
    max_depth = max_spawn_depth()
    if depth >= max_depth:
        return json.dumps({
            "error": f"委托深度已达上限（{depth}/{max_depth}），请直接完成任务而非继续委托。",
        }, ensure_ascii=False)

    # 并行模式：tasks 数组
    if tasks.strip():
        try:
            task_list = json.loads(tasks)
            if not isinstance(task_list, list) or not task_list:
                raise ValueError("tasks 必须是非空数组")
            for t in task_list:
                if not isinstance(t, dict) or not t.get("goal"):
                    raise ValueError("每个任务必须包含 goal 字段")
        except (json.JSONDecodeError, ValueError) as exc:
            return json.dumps({"error": f"tasks 参数解析失败: {exc}"}, ensure_ascii=False)

        log(f"并行委托 {len(task_list)} 个子任务 (role={role})", tag="委托")
        try:
            results = await _manager.delegate_batch(
                task_list, role=role, max_iterations=max_iterations,
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return _manager.aggregate_results(results)

    # 单任务模式
    if not goal.strip():
        return json.dumps({"error": "必须提供 goal 或 tasks 参数"}, ensure_ascii=False)

    if background:
        from agent.mind.tool_activation import ToolActivationManager
        delegation_id = _manager.delegate_background(
            goal, context, role=role, max_iterations=max_iterations,
            scope=ToolActivationManager.current_scope(),
        )
        return json.dumps({
            "ok": True,
            "mode": "background",
            "delegation_id": delegation_id,
            "message": "子代理已在后台执行，完成后系统会自动通知你（可用 check_background_tasks 查询进度）。",
        }, ensure_ascii=False)

    result = await _manager.delegate(
        goal, context, role=role, max_iterations=max_iterations,
    )
    return _manager.aggregate_results([result])


@deferred_tool(
    name="check_background_tasks",
    group="delegation", tags=["always"], source="mind.delegation",
    description="查看当前会话后台任务（子代理委托等）的运行状态与已完成结果。"
    "启动后台任务后用它查询进度，禁止凭空猜测任务状态。",
)
async def check_background_tasks() -> str:
    """查看当前会话的后台任务状态（运行中 + 已完成）。"""
    if _manager is None:
        return json.dumps({"error": "委托管理器未初始化"}, ensure_ascii=False)
    from agent.mind.tool_activation import ToolActivationManager
    snapshot = _manager.background_tasks_snapshot(ToolActivationManager.current_scope())
    snapshot["hint"] = (
        "有运行中任务时：可稍后用本工具再查，或 end_reply 结束本轮——"
        "任务完成时系统会自动通知你并触发新一轮回复。"
        if snapshot["running"] else "当前没有运行中的后台任务。"
    )
    return json.dumps(snapshot, ensure_ascii=False)
