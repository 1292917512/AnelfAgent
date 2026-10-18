"""工具调用轮次的结果处理、投递与恢复决策。"""


from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, List, Optional

from agent.channel.delivery import delivery_count, delivery_tags
from agent.mind.execution.completion import _finish_round
from agent.mind.execution.messages import (
    merge_after_messages,
)
from agent.mind.execution.responses import (
    _END_REPLY_TEXT_RE,
    _check_tool_results_all_errors,
    _collect_round_error_briefs,
    _reply_tool_results,
    _strip_think_blocks,
    should_end_reply,
)
from agent.mind.execution.state import ExecutionContext, RoundOutcome, RoundState, ThinkMode
from agent.mind.execution.tool_execution import _await_with_abort, _EarlyToolRunner, execute_tool_calls
from agent.mind.silence import should_suppress
from agent.mind.tools.core import END_REPLY_TOOL_NAME, OUTPUT_TOOL_NAMES
from core.log import log

if TYPE_CHECKING:
    from agent.llm import ChatResult, ToolCall


_PROMPT_TOOL_ERROR_ESCALATION = (
    "[工具恢复] 工具调用已连续两轮返回错误。下一步应获取能纠正原因的新证据："
    "先阅读回执中的 cause/hint 与实际输出；路径问题从已确认存在的目录列举或搜索，"
    "未知工具先查工具目录，参数问题先核对 schema。"
    "不要仅换一个猜测值继续尝试，也不要把搜索无匹配当作系统故障。"
    "如已无法取得新证据，向用户说明具体阻碍后结束本轮。"
)


async def _handle_tool_round(
        ctx: ExecutionContext,
        state: RoundState,
        result: ChatResult,
        tool_calls: List[ToolCall],
        *,
        early_runner: Optional["_EarlyToolRunner"] = None,
) -> RoundOutcome:
    """工具执行轮：执行工具批次、全错升级与 end_reply 结束拦截。"""
    from agent.mind.autonomous import MindPhase

    mind = ctx.mind
    tool_chain = ctx.tool_chain
    execution_steps = ctx.execution_steps
    guardrail = ctx.guardrail

    mind.set_phase(MindPhase.TOOL_EXECUTING)
    state.consecutive_empty_calls = 0
    state.reflect_text_rounds = 0
    # 反思产出语义：模型发起工作工具调用即说明此前的纯文本是中间独白而非
    # 最终结论——从产出中移除（过程留痕进 execution_steps），产出只保留
    # 收束前最后一个未被工具调用打断的连续文本段。
    # end_reply 是收束信号而非工作工具：纯 end_reply 批次不构成"打断"，
    # 已收集的连续文本段即最终结论，必须保留（同批正文在下方收束处理纳入）
    pure_end_reply = all(tc.name == END_REPLY_TOOL_NAME for tc in tool_calls)
    if ctx.mode == ThinkMode.REFLECT and ctx.collected_text and not pure_end_reply:
        dropped_chars = sum(len(s) for s in ctx.collected_text)
        execution_steps.append(
            f"→ 第{state.iteration + 1}轮: 中间独白 {dropped_chars} 字归档为过程"
            "（产出以最终总结段为准）"
        )
        ctx.collected_text.clear()
    called = {tc.name for tc in tool_calls}
    text = _strip_think_blocks(result.content or "").strip()
    from agent.mind.execution.reply_delivery import deliver_reply, observe_send_results, publish_delivery
    delivery_task = None
    sent_before = delivery_count()
    if (ctx.mode == ThinkMode.REPLY and text and not should_suppress(text)
            and not _END_REPLY_TEXT_RE.match(text) and not (called & (OUTPUT_TOOL_NAMES | {END_REPLY_TOOL_NAME}))):
        if state.reply_delivery_status != "unknown":
            state.auto_reply_blocked = False
        delivery_task = asyncio.create_task(_await_with_abort(deliver_reply(
            ctx, state, text, interim=True, messages=ctx.base_messages + tool_chain,
        ), ctx.abort_event))
    try:
        partial_abort = await execute_tool_calls(
            mind, tool_chain, result, tool_calls, state.iteration, ctx.anything,
            guardrail=guardrail, pipeline=ctx.pipeline, blocked_tools=ctx.blocked_tools,
            abort_event=ctx.abort_event, early=early_runner, journal=ctx.journal,
        )
        if delivery_task is not None:
            receipt, aborted = await delivery_task
            await publish_delivery(ctx, state, receipt)
            if aborted:
                state.interrupted = True
                state.completion_reason = "interrupted"
    finally:
        if delivery_task is not None:
            if not delivery_task.done():
                delivery_task.cancel()
            await asyncio.gather(delivery_task, return_exceptions=True)
    if partial_abort:
        state.partial_tool_abort = True
    if delivery_count() > sent_before:
        state.output_sent = True
        state.auto_reply_blocked = True
        state.reply_delivery_status = "sent"
    observe_send_results(ctx, state, tool_calls)
    if state.interrupted:
        return await _finish_round(ctx, state)

    # 记录目标工具使用（goal nag 提醒的计数依据）
    try:
        from agent.planning.nag import note_tools_used
        note_tools_used(ctx.current_scope, [tc.name for tc in tool_calls])
    except Exception:
        pass

    # 检测本轮工具结果是否全部为错误
    all_errors = _check_tool_results_all_errors(tool_chain, tool_calls)
    if all_errors:
        state.consecutive_tool_errors += 1
        briefs = _collect_round_error_briefs(tool_chain, tool_calls)
        detail = "; ".join(briefs) if briefs else "工具返回失败但未提供错误详情"
        log(
            f"本轮工具调用全部失败 (轮次 {state.iteration + 1}, "
            f"连续 {state.consecutive_tool_errors} 轮): {detail}",
            "WARNING", tag="思维",
        )
    else:
        state.consecutive_tool_errors = 0

    for tc in tool_calls:
        mind.tool_assembly.record_tool_use(tc.name)
    mind.tool_assembly.expand_discovered_tools(tool_calls, scope=ctx.current_scope)

    tool_names = ", ".join(tc.name for tc in tool_calls)
    failures = _collect_round_error_briefs(tool_chain, tool_calls)
    outcome = "；失败：" + "; ".join(failures) if failures else "；结果见对应工具回执"
    execution_steps.append(f"→ 第{state.iteration + 1}轮: 调用工具 [{tool_names}]{outcome}")

    if ctx.mode == ThinkMode.REPLY:
        from agent.channel.reply_policy import get_reply_policy

        handoff = get_reply_policy(ctx.adapter_key).handoff_to_events
        if handoff is not None and handoff(_reply_tool_results(tool_chain)):
            state.auto_reply_blocked = True
            handoff_note = delivery_tags("event", "pending", ctx.current_scope) + "任务已受理，交给频道终态事件汇报"
            tool_chain.append({"role": "system", "content": handoff_note,
                               "_source": {"origin": "delivery_handoff"}})
            execution_steps.append("→ " + handoff_note)
            return await _finish_round(ctx, state)

    if (guardrail.halt_decision is not None or state.consecutive_tool_errors >= 3) and not should_end_reply(tool_calls):
        from agent.context.observation import emit_context_update
        from agent.mind.execution.tool_recovery import assess_tool_recovery

        trigger = (guardrail.halt_decision.message if guardrail.halt_decision is not None
                   else f"连续 {state.consecutive_tool_errors} 轮工具调用全部失败")
        decision, aborted = await _await_with_abort(
            assess_tool_recovery(ctx.current_scope, ctx.base_messages + tool_chain, trigger), ctx.abort_event,
        )
        if aborted:
            state.interrupted = True
            state.completion_reason = "interrupted"
            return await _finish_round(ctx, state)
        message = {"role": "system", "content": trigger + "。" + decision.reason,
                   "_source": {"origin": "tool_recovery"}}
        tool_chain.append(message)
        execution_steps.append(f"→ 第{state.iteration + 1}轮: {decision.reason}")
        await emit_context_update([message], origin="recovery", scope=ctx.current_scope, iteration=state.iteration + 1)
        if not decision.proceed:
            state.completion_reason = "failed"
            log(decision.reason, "WARNING", tag="守卫")
            return await _finish_round(ctx, state)
        guardrail.resume_after_halt()
        state.consecutive_tool_errors = 0

    # 连续错误达到阈值时注入警告
    if state.consecutive_tool_errors >= 2:
        tool_chain.append({
            "role": "system",
            "content": _PROMPT_TOOL_ERROR_ESCALATION,
            "_source": {"origin": "tool_recovery"},
        })

    if should_end_reply(tool_calls):
        # 收束边界：子代理本要结束时收到的追加指示（after 档）优先于收束——
        # 注入续跑而非随收束清箱丢弃（对齐 pi followUp 档：仅在即将停止时投递）
        if merge_after_messages(ctx, state):
            state.reflect_text_rounds = 0
            state.iteration += 1
            return RoundOutcome.CONTINUE
        # end_reply 即静默收束：REPLY 下同批正文与此前独白均不投递
        # （回复走 send_message，结束备注写 reason 仅内部日志）；REFLECT 下
        # 收束信号不是工作工具，同批文本即最终连续文本段，纳入产出
        end_text = _strip_think_blocks(result.content or "").strip()
        if ctx.mode == ThinkMode.REFLECT and ctx.require_output and not end_text and not ctx.collected_text:
            ctx.summary_only = True
            tool_chain.append({
                "role": "system",
                "content": (
                    "你已请求结束，但尚未提交最终总结。下一轮只根据已有工具结果补交总结，"
                    "有输出契约则提交对应 JSON。所有工具调用均已禁用，不能继续或重做操作；"
                    "未验证的事项如实标为未确认，不得推测成功。直接输出总结正文，不能只调用 end_reply。"
                ),
                "_source": {"origin": "output_recovery"},
            })
            execution_steps.append(f"→ 第{state.iteration + 1}轮: 缺少最终总结，补交一轮（禁用操作）")
            state.iteration += 1
            return RoundOutcome.CONTINUE
        if ctx.mode == ThinkMode.REPLY:
            execution_steps.append(f"→ 第{state.iteration + 1}轮: end_reply 请求静默收束，过程正文不投递")
        elif end_text:
            ctx.collected_text.append(end_text)
        log(f"AI 请求结束{ctx.mode_label} (轮次 {state.iteration + 1})", tag="思维")
        # Plan 收敛由 finish_think 统一处理（所有正常结束路径的必经之地）
        return await _finish_round(ctx, state, explicit_end=True)

    state.iteration += 1
    return RoundOutcome.CONTINUE
