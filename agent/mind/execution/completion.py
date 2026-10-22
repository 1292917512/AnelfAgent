"""思维执行的总结、安全中断与终态收束。"""


from __future__ import annotations

from typing import TYPE_CHECKING, List

from agent.mind.execution.messages import (
    _merge_new_messages,
    merge_after_messages,
)
from agent.mind.execution.reply_finalize import finish_think
from agent.mind.execution.responses import (
    _append_assistant_msg,
    _detect_token_leak,
    _strip_think_blocks,
)
from agent.mind.execution.state import ExecutionContext, RoundOutcome, RoundState, ThinkMode
from agent.mind.execution.tool_execution import execute_tool_calls
from core.log import log

if TYPE_CHECKING:
    from agent.llm import ChatResult, ToolCall


_PROMPT_SECURITY_LEAK = (
    "[系统安全检测] 你的上一条回复中包含了会话安全标记（一次性令牌）。"
    "该标记仅用于标识可信历史，严禁复述。"
    "请不要给出额外解释或道歉，保持原有回复格式重新输出。"
)


async def _handle_summary_round(
        ctx: ExecutionContext,
        state: RoundState,
        result: ChatResult,
        tool_calls: List[ToolCall],
) -> RoundOutcome:
    """补交最终总结：本轮全部工具只返回拒绝结果，正文收集后立即收束。"""
    if tool_calls:
        await execute_tool_calls(
            ctx.mind, ctx.tool_chain, result, tool_calls, state.iteration, ctx.anything,
            guardrail=ctx.guardrail, pipeline=ctx.pipeline,
            blocked_tools=ctx.blocked_tools | {tc.name for tc in tool_calls},
            abort_event=ctx.abort_event,
        )
    else:
        _append_assistant_msg(
            ctx.tool_chain, result, result.content or "", tools_present=bool(ctx.active_tools),
        )
    text = _strip_think_blocks(result.content or "").strip()
    if text:
        ctx.collected_text.append(text)
    if merge_after_messages(ctx, state):
        state.iteration += 1
        return RoundOutcome.CONTINUE
    return await _finish_round(ctx, state)


async def _handle_interrupt(ctx: ExecutionContext, state: RoundState) -> bool:
    """循环顶部的协作式中断检查；已中断则安全收束并返回 True。

    不发半截消息、不写残缺工具链、历史留中断元消息；在批内被中止的
    工具以占位结果保住配对，元消息提示"可能已部分执行"。
    """
    if not (
        ctx.current_scope
        and ctx.interrupts is not None
        and ctx.interrupts.is_requested(ctx.current_scope)
    ):
        return False
    reason = ctx.interrupts.consume(ctx.current_scope) or "未说明"
    state.interrupted = True  # finally 收敛 plan 时按 cancelled 处理
    if ctx.completion is not None:
        ctx.completion.update(reason="interrupted", messages=ctx.base_messages + ctx.tool_chain,
                              tools=ctx.active_tools)
    log(
        f"会话被中断 (轮次 {state.iteration + 1}): "
        f"scope={ctx.current_scope} reason={reason}",
        tag="中断",
    )
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮前: 会话被中断 ({reason})")
    if ctx.mode == ThinkMode.REPLY and ctx.anything:
        aborted_hint = (
            "被中止的操作可能已部分执行，继续前请先核对实际状态"
            if state.partial_tool_abort
            else "未完成的操作已放弃"
        )
        await ctx.mind.conversation_data.append_context(
            ctx.anything,
            f"[系统] 本次回复在执行中被中断（{reason}），"
            f"{aborted_hint}，如需继续请重新发起。",
            role="system",
        )
        await finish_think(
            ctx.mind, ctx.anything, ctx.execution_steps, state.iteration, ctx.tool_chain,
            completion=ctx.completion, turn_id=ctx.turn_id,
        )
    return True


async def _finish_round(ctx: ExecutionContext, state: RoundState, *, explicit_end: bool = False) -> RoundOutcome:
    """收敛执行并记录完成事实；显式结束不追加模型调用。"""
    if (not explicit_end and ctx.mode == ThinkMode.REPLY and state.completion_reason == "completed"
            and not state.interrupted and await _merge_new_messages(ctx, state)):
        state.consecutive_empty_calls = 0
        state.iteration += 1
        return RoundOutcome.CONTINUE
    try:
        from agent.planning import tracker as _plan_tracker
        await _plan_tracker.finalize_plan(ctx.current_scope, "completed" if state.completion_reason == "completed" else "cancelled")
        state.plan_finalized = True
    except Exception:
        pass  # 收敛失败不影响主流程，finally 兜底重试
    if ctx.mode == ThinkMode.REPLY and ctx.anything:
        # 正常结束的唯一收口点：消息链已是终态，先写入 completion 再发完成事件
        # （complete_reply 读取该字段组装 EVENT_AFTER_REPLY 的 messages 快照；
        # think_loop finally 的写入面向 SubAgent 续跑场景，时机晚于本路径）
        completion = ctx.completion if ctx.completion is not None else {}
        completion["reason"] = state.completion_reason
        completion["messages"] = ctx.base_messages + ctx.tool_chain
        await finish_think(
            ctx.mind, ctx.anything, ctx.execution_steps, state.iteration + 1, ctx.tool_chain,
            completion=completion, turn_id=ctx.turn_id,
        )
    return RoundOutcome.BREAK


async def _handle_security_leak(
        ctx: ExecutionContext,
        state: RoundState,
        result: ChatResult,
        tool_calls: List[ToolCall],
) -> RoundOutcome:
    """安全泄露处理：AI 输出复述了会话令牌 → 注入纠正提示重试，连续 2 次强制结束。"""
    if not _detect_token_leak(result, tool_calls):
        state.consecutive_security_leaks = 0
        return RoundOutcome.PROCEED

    state.consecutive_security_leaks += 1
    log(
        f"检测到会话令牌泄露 (轮次 {state.iteration + 1}, "
        f"连续 {state.consecutive_security_leaks} 次)",
        "WARNING", tag="安全",
    )
    if state.consecutive_security_leaks >= 2:
        state.completion_reason = "failed"
        log("连续令牌泄露，强制结束本轮", "WARNING", tag="安全")
        ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 连续安全泄露，强制结束")
        return await _finish_round(ctx, state)
    # 本轮 tool_calls 因安全原因一并丢弃，显式告知 LLM 避免下轮误以为已执行
    prompt = _PROMPT_SECURITY_LEAK
    if tool_calls:
        prompt += "本轮的工具调用请求因安全检测一并被丢弃，请在下轮重新发起。"
    ctx.tool_chain.append({"role": "system", "content": prompt})
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 安全泄露已拦截并纠正")
    state.iteration += 1
    return RoundOutcome.CONTINUE
