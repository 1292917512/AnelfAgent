"""思维轮次编排：准备、调用、结果处理与终态收束。

模型可见：默认不注入轮次上限或剩余轮次提醒，显式配置正数时沿用预算提示。
Token 影响：不新增提示词；默认允许任务继续执行，实际用量随完成所需轮次增长。
缓存影响：轮次状态仍在动态尾部，不改变稳定前缀与工具目录。
"""


from __future__ import annotations

import time
from contextlib import nullcontext
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from agent.channel.delivery import capture_deliveries
from agent.memory.graph.curation import curation_window
from agent.mind.execution.completion import _handle_interrupt, _handle_security_leak, _handle_summary_round
from agent.mind.execution.model import _invoke_llm_round
from agent.mind.execution.reply_finalize import finish_think
from agent.mind.execution.responses import (
    _handle_length_recovery,
    resolve_tool_calls,
)
from agent.mind.execution.round_context import prepare_round_context
from agent.mind.execution.session import _prepare_think_context
from agent.mind.execution.state import ExecutionContext, RoundOutcome, RoundState, ThinkMode
from agent.mind.execution.text_round import _handle_text_only_round
from agent.mind.execution.tool_execution import _EarlyToolRunner
from agent.mind.execution.tool_round import _handle_tool_round
from agent.mind.execution.usage import _emit_context_usage
from agent.mind.recall.refresh import DialogueRecollection
from core.activity import ActivityHandle, activity_scope
from core.event_bus import (
    EVENT_THINKING_REPLY_ROUND,
    event_bus,
)
from core.log import log

if TYPE_CHECKING:
    from agent.messages import Everything
    from agent.mind.mind import Mind


async def think_loop(
        mind: Mind,
        mode: ThinkMode,
        tool_chain: List[Dict],
        execution_steps: List[str],
        start_time: float,
        safety_limit: int,
        collected_text: List[str],
        active_tools: List[Dict],
        anything: Optional[Everything] = None,
        base_messages: Optional[List[Dict]] = None,
        options: Optional[Dict] = None,
        *,
        adapter_key: str = "",
        blocked_tools: Optional[Set[str]] = None,
        completion: Optional[Dict] = None,
        reflect_tool_selectors: Optional[List[str]] = None,
        require_output: bool = False,
        activity: ActivityHandle | None = None,
        iteration_config_keys: tuple[str, ...] = ("max_tool_iterations",),
) -> None:
    """统一思维循环：对话和反思共享同一流程。

    通过 mode 参数区分行为：
    - REPLY：处理用户消息，通过工具发送回复，写入对话历史
    - REFLECT：收集内部文本到 collected_text；显式发送由工具权限控制

    base_messages 仅首轮获取，后续轮次复用缓存。
    工具集由调用方构建并传入，确保模式差异在入口处理。
    safety_limit 大于 0 时限制轮次，否则不设轮次上限。
    blocked_tools 为模式级禁用工具：schema 保留在数组中（跨调用前缀一致），
    执行侧拦截返回合成错误结果（可见性与权限分离）。
    reflect_tool_selectors 仅 REFLECT 模式使用：工具集版本变化重建
    active_tools 时按同一批选择器还原精简目录（与 mind.reflect 初始装配一致）。
    主循环只保留：中断检查 → LLM 调用 → 分发到阶段函数 → 终止条件判断。
    """
    ctx, state = await _prepare_think_context(
        mind, mode, tool_chain, execution_steps,
        collected_text, active_tools, anything, base_messages,
        options, adapter_key, blocked_tools, completion,
        reflect_tool_selectors=reflect_tool_selectors,
        require_output=require_output,
        turn_id=activity.turn_id if activity else "",
    )

    # 工具数组顺序由 ToolAssembly 跨回复追加式冻结（见 tool_assembly），
    # 会话内/回复间均字节稳定，无需在此再冻结。

    manager = nullcontext(activity) if activity is not None else activity_scope(
        ctx.turn_id, ctx.current_scope,
        label=anything.get_text_content() if anything is not None else "",
        kind="conversation" if mode == ThinkMode.REPLY else "reflection",
    )
    async with manager as activity:
        recollection = DialogueRecollection(mind.recollection, ctx.current_scope, ctx.base_messages)
        try:
            with curation_window(), capture_deliveries(
                ctx.current_scope, acknowledge=lambda scope, ts: mind.pfc.consume_scope_task(scope, through_ts=ts),
            ):
                await _run_think_rounds(ctx, state, safety_limit, start_time, recollection)
        except Exception:
            state.completion_reason = "failed"
            raise
        finally:
            await recollection.close()
            activity.status = "cancelled" if state.interrupted else state.completion_reason
            if state.completion_reason == "budget_exhausted":
                activity.limit = {"kind": "iterations", "value": safety_limit,
                                  "config_keys": list(iteration_config_keys)}
            # 结束原因收口（调用方传入 completion 容器时写出）：
            # 中断 > 预算耗尽标记 > 默认完成
            if ctx.completion is not None:
                ctx.completion["reason"] = (
                    "interrupted" if state.interrupted else state.completion_reason
                )
                # 最终消息链（base + 工具链）随容器带出：SubAgent 据此持久化
                # transcript 供 follow_up 续跑。中断路径消息不完整，同样带出
                # （是否可续跑由 messages 是否存在决定，收束路径恒有值）。
                ctx.completion["messages"] = ctx.base_messages + ctx.tool_chain
                ctx.completion["tools"] = ctx.active_tools
            # 正常结束由 _finish_round 收敛；未走正常出口的中断、异常和预算耗尽只可取消。
            if not state.plan_finalized:
                try:
                    from agent.planning import tracker as _plan_tracker
                    await _plan_tracker.finalize_plan(
                        ctx.current_scope,
                        "cancelled",
                    )
                except Exception:
                    pass  # 收敛失败不影响主流程


async def _run_think_rounds(
        ctx: ExecutionContext,
        state: RoundState,
        safety_limit: int,
        start_time: float,
        recollection: DialogueRecollection,
) -> None:
    """轮次主循环：中断检查 → LLM 调用 → 阶段分发 → 终止判断。"""
    from agent.mind.autonomous import MindPhase
    from agent.mind.tool_activation import tool_activation as _tool_act_mgr
    from core.entity import EntityRegistry

    mind = ctx.mind
    anything = ctx.anything
    mode = ctx.mode
    execution_steps = ctx.execution_steps

    # 工具集版本快照：每轮检查版本变化，变了就重建 active_tools（保持 prefix 缓存友好）。
    # registry 版本覆盖运行期注册表增删（MCP 热同步/reload/entities 重载/WebUI 开关）——
    # 否则粘性激活的 stay_awake 服务在回复进行中新增工具，要等下一个版本事件才可见
    last_tools_version = (
        mind.tool_assembly.tools_version,
        _tool_act_mgr.version,
        EntityRegistry.version(),
    )

    while safety_limit <= 0 or state.iteration < safety_limit:
        # 中断检查点（协作式）：用户/守卫请求中断时安全收束
        if await _handle_interrupt(ctx, state):
            return

        # 工具集版本检查：激活/发现/注册表变化时重建 active_tools。
        # REPLY 与无选择器的 REFLECT（reflect_share_reply_tools，与
        # mind.reflect 初始装配同族）走回复级全量装配（追加式冻结保证
        # 前缀字节稳定）；带选择器的 REFLECT 走精简目录装配（与初始
        # 装配同一入口，重建不会退回全量、也不会丢失选择器工具）
        cur_tools_version = (
            mind.tool_assembly.tools_version,
            _tool_act_mgr.version,
            EntityRegistry.version(),
        )
        if cur_tools_version != last_tools_version:
            last_tools_version = cur_tools_version
            if mode is ThinkMode.REFLECT and ctx.reflect_tool_selectors:
                selected_tools = await mind.tool_assembly.get_reflect_tool_schemas(
                    ctx.adapter_key, scope=ctx.current_scope,
                    selectors=list(ctx.reflect_tool_selectors),
                )
            else:
                selected_tools = await mind.tool_assembly.get_active_tool_schemas(
                    ctx.adapter_key, scope=ctx.current_scope,
                )
            if mode is ThinkMode.REFLECT:
                from agent.mind.tool_assembly import inherit_tool_catalog
                ctx.active_tools = await inherit_tool_catalog(ctx.active_tools, selected_tools, scope=ctx.current_scope)
            else:
                ctx.active_tools = selected_tools
            log(f"工具集版本变化，重建 active_tools: {len(ctx.active_tools)} 个", "DEBUG", tag="思维")

        await event_bus.emit(EVENT_THINKING_REPLY_ROUND, {
            "iteration": state.iteration,
            "safety_limit": safety_limit,
            "elapsed": time.time() - start_time,
            "steps_so_far": len(execution_steps),
            "mode": mode.value,
        })

        llm_messages, require_tools = await prepare_round_context(
            ctx, state, safety_limit, start_time, recollection,
        )
        if await _handle_interrupt(ctx, state):
            return

        mind.set_phase(MindPhase.LLM_CALLING)
        # 流中早执行：只读调用在流继续生成时提前分发，工具轮按 id 回收
        early_runner = _EarlyToolRunner(
            mind, anything, state.iteration,
            blocked_tools=ctx.blocked_tools, guardrail=ctx.guardrail,
        )
        if ctx.summary_only:
            early_runner.abandon()
        # 超时/上下文超限已在 _invoke_llm_round 内注入恢复提示或紧急压缩
        result = await _invoke_llm_round(
            ctx, state, llm_messages, require_tools, early_runner=early_runner,
        )
        early_runner.abandon()
        if result is None:
            continue

        state.consecutive_overflow_compressions = 0
        if result.usage:
            # 输入占用锚点用归一化总输入口径：独占口径（原生 Anthropic）下
            # prompt_tokens 不含缓存读/写，以其为锚会低估真实占用
            if result.usage.total_input_tokens:
                state.last_input_tokens = result.usage.total_input_tokens
            state.last_cache_read_tokens = result.usage.cache_read_input_tokens
            state.last_cache_creation_tokens = result.usage.cache_creation_input_tokens
            state.last_cache_hit_rate = result.usage.cache_hit_rate
            state.last_cache_observable = result.usage.cache_observable

        # 上下文用量快照（usage 锚定：API 真实用量优先；供 webui 状态栏显示）
        await _emit_context_usage(ctx, state)

        # max_output_tokens 截断恢复：长度截断时注入续写提示
        if _handle_length_recovery(ctx, state, result) is RoundOutcome.CONTINUE:
            continue

        tool_calls = resolve_tool_calls(result)

        # 安全检测：AI 输出复述了会话令牌 → 注入纠正提示重试
        outcome = await _handle_security_leak(ctx, state, result, tool_calls)
        if outcome is RoundOutcome.BREAK:
            return
        if outcome is RoundOutcome.CONTINUE:
            continue

        if ctx.summary_only:
            outcome = await _handle_summary_round(ctx, state, result, tool_calls)
        elif not tool_calls:
            outcome = await _handle_text_only_round(ctx, state, result)
        else:
            outcome = await _handle_tool_round(
                ctx, state, result, tool_calls, early_runner=early_runner,
            )
        if outcome is RoundOutcome.BREAK:
            return

    # 达到安全上限：产出可能只是中途状态，标记结束原因供调用方决策
    state.completion_reason = "budget_exhausted"
    if ctx.completion is not None:
        ctx.completion["reason"] = state.completion_reason
    log(f"达到安全上限 ({safety_limit} 轮)，强制结束", "WARNING", tag="思维")
    if mode == ThinkMode.REPLY and anything:
        await finish_think(mind, anything, execution_steps, safety_limit, ctx.tool_chain,
                           completion=ctx.completion, turn_id=ctx.turn_id)
