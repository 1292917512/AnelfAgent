"""delegate_task 工具 — 主 Agent 将子任务委托给隔离的子代理执行。

DelegationManager 引用经 ``delegation_manager_port`` 晚绑定端口分发
（工具 import 时注册、拿不到 Mind 构造参数；由 agent.runtime.wiring 统一施绑）。

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
from core.entity import EntityRegistry
from core.latebind import LateBinding
from core.log import log
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from core.tool_registry import deferred_tool

EntityRegistry.register_group_order("delegation", 22)

#: 委托管理器端口（bootstrap 经 agent.runtime.wiring 施绑）
delegation_manager_port: LateBinding[DelegationManager] = LateBinding("delegation.manager")


def _manager_or_none() -> Optional[DelegationManager]:
    """取委托管理器（未施绑时返回 None，工具降级为未就绪错误）。"""
    return delegation_manager_port.get() if delegation_manager_port.bound else None


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
    "支持 tasks 数组并行委托多个子任务。子代理无法发送消息，只返回文字总结"
    "（含 turns/tokens 用量；档案带 output_schema 时为 JSON）。"
    "可用 list_sub_agents 查看子代理档案（名称 → 模型候选池 + 专职守则/工具面/输出契约；"
    "含内置难度档 easy/medium/hard）。",
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
        fork_context: bool = False,
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
        fork_context: 是否把主对话近期记录（最近 20 条只读快照）带给子代理，
            适合"接着当前话题深查"类任务；默认不带（子代理只看 goal+context）
    """
    if not _delegation_enabled():
        return tool_error(
            "子代理委托已禁用",
            cause=ErrorCause.STATE, retryable=False,
            hint="如需启用，请将配置 delegation_enabled 设为 true",
        )
    manager = _manager_or_none()
    if manager is None:
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
            results = await manager.delegate_batch(
                task_list, role=role, max_iterations=max_iterations,
                difficulty=difficulty, agent_name=agent_name,
            )
        except ValueError as exc:
            return error_from_exception(exc, action="并行委托")
        return manager.aggregate_results(results)

    # 单任务模式
    if not goal.strip():
        return tool_error("必须提供 goal 或 tasks 参数", cause=ErrorCause.PARAM, retryable=False)

    if background:
        from agent.mind.tool_activation import current_owner_scope
        # 归属会话解析（委托链绑定的父会话优先）：子代理内发起的后台委托
        # 也登记到发起会话，完成通知才能路由回对话
        delegation_id = manager.delegate_background(
            goal, context, role=role, max_iterations=max_iterations,
            scope=current_owner_scope(),
            difficulty=difficulty, agent_name=agent_name,
            fork_context=fork_context,
        )
        return json.dumps({
            "ok": True,
            "mode": "background",
            "delegation_id": delegation_id,
            "message": "子代理已在后台执行，完成后系统会自动通知你（可用 check_background_tasks 查询进度）。",
        }, ensure_ascii=False)

    result = await manager.delegate(
        goal, context, role=role, max_iterations=max_iterations,
        difficulty=difficulty, agent_name=agent_name, fork_context=fork_context,
    )
    return manager.aggregate_results([result])


def _validate_agent_name(agent_name: str) -> str:
    """子代理档案前置校验：返回错误 tool 结果（空串 = 通过）。

    区分「档案不存在」（附可用名称列表供 AI 自纠正）与「候选池无可用模型」。
    """
    if not agent_name:
        return ""
    manager = _manager_or_none()
    mgr = getattr(manager._mind, "llm_manager", None) if manager else None
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
    name="send_to_agent",
    group="delegation", tags=["always"], source="mind.delegation",
    description="向运行中的委托发送指令（steer）：指令在子代理当前步骤完成后注入，"
    "可改变进行中的工作——不必取消重开、已完成的部分保留。"
    "适合用户中途修改需求、补充约束、追加信息等场景。"
    "deliver_as=after 时改为收束边界投递（子代理本要结束时注入续跑，"
    "适合「做完这批后顺便…」型追加）。"
    "目标 delegation_id 可从 delegate_task 的返回或 check_background_tasks 获取。",
)
async def send_to_agent(
        delegation_id: str,
        message: str,
        deliver_as: str = "steer",
) -> str:
    """向运行中的子代理发送指令（按档位选择注入边界）。

    Args:
        delegation_id: 目标委托 ID（delegate_task 返回 / check_background_tasks 查看）
        message: 指令内容（需求变更、补充约束等；应明确说明要调整什么）
        deliver_as: steer（默认，步骤边界注入——改变进行中的工作）/
            after（收束边界注入——子代理本要结束时追加续跑）
    """
    if not _delegation_enabled():
        return tool_error(
            "子代理委托已禁用",
            cause=ErrorCause.STATE, retryable=False,
            hint="如需启用，请将配置 delegation_enabled 设为 true",
        )
    manager = _manager_or_none()
    if manager is None:
        return _manager_not_ready()
    result = manager.steer(delegation_id.strip(), message, deliver_as.strip().lower())
    if "error" in result:
        return tool_error(
            result["error"],
            cause=ErrorCause.NOT_FOUND if "不存在" in result["error"] else ErrorCause.PARAM,
            retryable=False,
            hint=result.get("hint"),
        )
    return json.dumps(result, ensure_ascii=False)


@deferred_tool(
    name="follow_up_agent",
    group="delegation", tags=["always"], source="mind.delegation",
    description="续跑一个已结束的委托：上次的完整执行上下文（工具调用与中间结论）"
    "原样在场，追加指令后继续——无损续聊，优于把总结当 context 重新委托。"
    "适合「接着上次那个调研继续深挖」、失败委托换个思路重试等场景。"
    "运行中的委托不可续跑（用 send_to_agent）；transcript 过期清理后不可续跑"
    "（默认保留 7 天，重新委托即可）。",
)
async def follow_up_agent(
        delegation_id: str,
        message: str,
        background: bool = False,
        max_iterations: int = 0,
) -> str:
    """续跑已结束的委托（transcript 消息链为上下文）。

    Args:
        delegation_id: 上次委托的 ID（delegate_task 返回 / check_background_tasks 已完成列表）
        message: 续跑指令（要继续做什么、补充什么信息、修正什么方向）
        background: 是否后台执行（立即返回新 delegation_id，完成自动通知）
        max_iterations: 续跑迭代预算（0 = 沿用上次委托的预算）
    """
    if not _delegation_enabled():
        return tool_error(
            "子代理委托已禁用",
            cause=ErrorCause.STATE, retryable=False,
            hint="如需启用，请将配置 delegation_enabled 设为 true",
        )
    manager = _manager_or_none()
    if manager is None:
        return _manager_not_ready()
    if not (message or "").strip():
        return tool_error("message 不能为空", cause=ErrorCause.PARAM, retryable=False)
    result = await manager.follow_up(
        delegation_id.strip(), message,
        background=background, max_iterations=max_iterations,
    )
    if "error" in result:
        return tool_error(
            result["error"],
            cause=ErrorCause.NOT_FOUND, retryable=False,
            hint=result.get("hint"),
        )
    return json.dumps(result, ensure_ascii=False)
