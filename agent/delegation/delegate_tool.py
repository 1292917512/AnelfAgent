"""delegate_task 工具 — 主 Agent 将子任务委托给隔离的子代理执行。

通过 `register_delegation_tools()` 在 Mind 初始化时注入 DelegationManager 后注册。

Model Experience:
- 模型看到：delegate_task schema 新增 agent_name 参数（静态），命名档案内容不注入
  任何 prompt 层，AI 经 list_sub_agents 工具按需发现
- token 影响：schema 增量极小（一次性），档案查询按需
- KV Cache 影响：一次性 schema 变更（部署后新会话生效），无持续前缀层影响
"""
from __future__ import annotations

import json
from typing import Optional

from agent.delegation.delegation_manager import DelegationManager
from agent.delegation.sub_agent import current_depth, max_spawn_depth
from core.log import log
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import activate_group, deferred_tool

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


def _manager_not_ready() -> str:
    return tool_error(
        "委托管理器未初始化",
        cause=ErrorCause.STATE, retryable=False,
        hint="委托组件未初始化，请检查服务启动状态",
    )


@deferred_tool(
    name="delegate_task",
    group="delegation", tags=["always"], source="mind.delegation",
    description="将子任务委托给隔离的子代理执行。适合可独立完成的子任务（调研、分析、批量处理）。"
    "支持 tasks 数组并行委托多个子任务。子代理无法发送消息，只返回文字总结。"
    "可用 list_sub_agents 查看子代理档案（名称 → 模型候选池；含内置难度档 easy/medium/hard）。",
)
async def delegate_task(
        goal: str = "",
        context: str = "",
        tasks: str = "",
        role: str = "leaf",
        background: bool = False,
        max_iterations: int = 0,
        difficulty: int = 0,
        agent_name: str = "",
) -> str:
    """委托子任务给子代理执行。

    Args:
        goal: 子任务目标（单个任务时必填）
        context: 背景上下文（子代理只能看到 goal+context，看不到主对话）
        tasks: 并行任务数组的 JSON 字符串，如 [{"goal":"...","context":"..."}, ...]（提供时忽略 goal）；
            每项可用 "agent" 指定子代理档案、"difficulty" 指定难度（缺省继承顶层参数）
        role: 子代理角色：leaf（默认，不可再委托）/ orchestrator（可再委托，有深度限制）
        background: 是否后台执行（立即返回 delegation_id；完成时系统自动通知并触发新一轮回复，
            期间可用 check_background_tasks 查询进度）
        max_iterations: 子代理迭代预算（轮次），默认 15
        difficulty: 难度挡位，等价于指定内置档案 agent_name：1=easy（简单，检索/格式化等机械任务）
            2=medium（中等，常规分析/执行）3=hard（困难，复杂推理/多步规划）。
            0/不传=使用默认模型。只需按难度标注，无需关心具体模型。
        agent_name: 子代理档案名（优先于 difficulty）：档案携带有序模型候选池（首个可用者生效），
            适合已知某模型更适合某类工作的场景。档案用 create_sub_agent 管理、
            list_sub_agents 查看（内置 easy/medium/hard 也可直接指定）。
    """
    if not _delegation_enabled():
        return tool_error(
            "子代理委托已禁用",
            cause=ErrorCause.STATE, retryable=False,
            hint="如需启用，请将配置 delegation_enabled 设为 true",
        )
    if _manager is None:
        return _manager_not_ready()

    err = _validate_agent_name(agent_name)
    if err:
        return err

    # 深度硬限制：超过 max_depth 禁止再委托
    depth = current_depth()
    max_depth = max_spawn_depth()
    if depth >= max_depth:
        return tool_error(
            f"委托深度已达上限（{depth}/{max_depth}），请直接完成任务而非继续委托。",
            cause=ErrorCause.STATE, retryable=False,
        )

    # 并行模式：tasks 数组
    if tasks.strip():
        try:
            task_list = json.loads(tasks)
            if not isinstance(task_list, list) or not task_list:
                raise ValueError("tasks 必须是非空数组")
            for t in task_list:
                if not isinstance(t, dict) or not t.get("goal"):
                    raise ValueError("每个任务必须包含 goal 字段")
                item_agent = str(t.get("agent") or "")
                if item_agent:
                    item_err = _validate_agent_name(item_agent)
                    if item_err:
                        return item_err
        except (json.JSONDecodeError, ValueError) as exc:
            return error_from_exception(
                exc, action="解析 tasks 参数",
                hint="tasks 应为 JSON 数组字符串，每个元素是含 goal 字段的对象",
            )

        log(f"并行委托 {len(task_list)} 个子任务 (role={role})", tag="委托")
        try:
            results = await _manager.delegate_batch(
                task_list, role=role, max_iterations=max_iterations,
                difficulty=difficulty, agent_name=agent_name,
            )
        except ValueError as exc:
            return error_from_exception(exc, action="并行委托")
        return _manager.aggregate_results(results)

    # 单任务模式
    if not goal.strip():
        return tool_error("必须提供 goal 或 tasks 参数", cause=ErrorCause.PARAM, retryable=False)

    if background:
        from agent.mind.tool_activation import ToolActivationManager
        delegation_id = _manager.delegate_background(
            goal, context, role=role, max_iterations=max_iterations,
            scope=ToolActivationManager.current_scope(),
            difficulty=difficulty, agent_name=agent_name,
        )
        return json.dumps({
            "ok": True,
            "mode": "background",
            "delegation_id": delegation_id,
            "message": "子代理已在后台执行，完成后系统会自动通知你（可用 check_background_tasks 查询进度）。",
        }, ensure_ascii=False)

    result = await _manager.delegate(
        goal, context, role=role, max_iterations=max_iterations,
        difficulty=difficulty, agent_name=agent_name,
    )
    return _manager.aggregate_results([result])


def _validate_agent_name(agent_name: str) -> str:
    """子代理档案前置校验：返回错误 tool 结果（空串 = 通过）。

    区分「档案不存在」（附可用名称列表供 AI 自纠正）与「候选池无可用模型」。
    """
    if not agent_name:
        return ""
    mgr = getattr(_manager._mind, "llm_manager", None) if _manager else None
    profiles = {p["name"]: p for p in mgr.list_sub_agents()} if mgr is not None else {}
    if agent_name not in profiles:
        available = "、".join(profiles) if profiles else "（暂无，可用 create_sub_agent 创建）"
        return tool_error(
            f"子代理档案 '{agent_name}' 不存在，可用: {available}",
            cause=ErrorCause.PARAM, retryable=False,
        )
    profile = profiles[agent_name]
    if not profile["model_enabled"]:
        return tool_error(
            f"子代理档案 '{agent_name}' 候选池 {profile['models']} 无可用模型（缺失或停用），"
            "请用 update_sub_agent 调整模型池，或改用 difficulty",
            cause=ErrorCause.STATE, retryable=False,
        )
    return ""


@deferred_tool(
    name="check_background_tasks",
    group="delegation", tags=["always"], source="mind.delegation",
    description="查看当前会话后台任务（子代理委托等）的运行状态与已完成结果。"
    "启动后台任务后用它查询进度，禁止凭空猜测任务状态。",
)
async def check_background_tasks(task_id: str = "") -> str:
    """查看当前会话的后台任务状态（运行中 + 已完成）。

    Args:
        task_id: 可选，指定任务时改为增量读取其输出——每次只返回自上次
            读取以来的新增内容（消费型，不重复发送），适合轮询长任务进度。
    """
    if _manager is None:
        return _manager_not_ready()
    from agent.mind.tool_activation import ToolActivationManager
    scope = ToolActivationManager.current_scope()
    registry = getattr(_manager._mind, "background_tasks", None) if _manager._mind else None
    if task_id and registry is not None:
        result = registry.read_task_output(scope, task_id)
        if not result.get("ok"):
            from core.tool_errors import ErrorCause, tool_error
            return tool_error(
                result.get("error", "读取任务输出失败"),
                cause=ErrorCause.NOT_FOUND, retryable=False,
            )
        return json.dumps(result, ensure_ascii=False)
    snapshot = _manager.background_tasks_snapshot(scope)
    snapshot["hint"] = (
        "有运行中任务时：可稍后用本工具再查，或 end_reply 结束本轮——"
        "任务完成时系统会自动通知你并触发新一轮回复。"
        "长任务（如后台 shell）可传 task_id 增量读取新输出。"
        if snapshot["running"] else "当前没有运行中的后台任务。"
    )
    return json.dumps(snapshot, ensure_ascii=False)
