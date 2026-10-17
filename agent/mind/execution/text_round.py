"""无工具调用轮次的投递、等待与结束决策。"""


from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from agent.context.message_schema import preserve_reasoning_fields
from agent.mind.execution.background import _format_running_tasks, _format_task_completions, _suspend_for_background
from agent.mind.execution.completion import _finish_round
from agent.mind.execution.messages import (
    _merge_new_messages,
    merge_after_messages,
)
from agent.mind.execution.responses import (
    _END_REPLY_TEXT_RE,
    _append_assistant_msg,
    _strip_think_blocks,
)
from agent.mind.execution.state import ExecutionContext, RoundOutcome, RoundState, ThinkMode
from agent.mind.execution.tool_execution import _await_with_abort
from agent.mind.mind_tools import END_REPLY_TOOL_NAME
from agent.mind.silence import should_suppress
from core.log import log

if TYPE_CHECKING:
    from agent.llm import ChatResult
    from agent.mind.background_tasks import BackgroundTaskInfo


_PROMPT_CONTINUE = (
    "[系统提示] 继续执行，若已完成所有操作请调用 end_reply 结束。"
)


_PROMPT_TASKS_STILL_RUNNING = (
    "[系统提示] 你等待的后台任务仍未完成（运行中：{tasks}）。\n"
    "请选择：\n"
    "1. 调用 check_background_tasks 查看最新进度\n"
    "2. 确认任务的 notification 策略后调用 end_reply 结束本轮；静默完成不会自动续轮\n"
    "3. 继续处理其他事务"
)


_MAX_REFLECT_TEXT_ROUNDS = 3


_SYNTHETIC_END_REPLY_CALL_ID = "call_end_reply_text_intent"


async def _handle_text_only_round(
        ctx: ExecutionContext,
        state: RoundState,
        result: ChatResult,
) -> RoundOutcome:
    """纯文本轮：等待后台结果、收束内部反思或审核并投递普通会话的最终回复。"""
    tool_chain = ctx.tool_chain
    execution_steps = ctx.execution_steps
    raw_text = _strip_think_blocks(result.content or "").strip()

    if not raw_text:
        # 空输出：可接受（思考中/无意回复），不注入纠正提示；
        # 连续 2 次空输出安静结束本轮
        state.consecutive_empty_calls += 1
        if result.reasoning_content:
            _append_assistant_msg(tool_chain, result, "")
        execution_steps.append(f"→ 第{state.iteration + 1}轮: 空输出（思考中）")
        log(
            f"空输出，继续循环 (轮次 {state.iteration + 1}, "
            f"连续 {state.consecutive_empty_calls} 次)",
            "DEBUG", tag="思维",
        )
        if state.consecutive_empty_calls >= 2:
            log(f"连续空输出 {state.consecutive_empty_calls} 次，结束本轮", "DEBUG", tag="思维")
            execution_steps.append(
                f"→ 第{state.iteration + 1}轮: 连续空输出 {state.consecutive_empty_calls} 次，结束"
            )
            return await _finish_round(ctx, state)
    elif ctx.mode == ThinkMode.REPLY and should_suppress(raw_text):
        # [SILENT] 精确匹配 / 幻觉沉默旁白：AI 决定不回复，直接结束本轮
        log(f"AI 选择沉默（{raw_text[:30]}），结束本轮", "DEBUG", tag="思维")
        _append_assistant_msg(tool_chain, result, raw_text)
        execution_steps.append(f"→ 第{state.iteration + 1}轮: AI 选择沉默，结束")
        return await _finish_round(ctx, state, explicit_end=True)
    else:
        state.consecutive_empty_calls = 0
        # REPLY：弱模型把 end_reply 写成文本——按结束意图处理，并以规范
        # function calling 形态入链（assistant tool_calls + tool 结果）：
        # 幻觉文本本身不留痕，上下文与执行摘要看到的都是正确格式，不强化文本形态调用
        if ctx.mode == ThinkMode.REPLY and _END_REPLY_TEXT_RE.match(raw_text):
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": _SYNTHETIC_END_REPLY_CALL_ID,
                    "type": "function",
                    "function": {"name": END_REPLY_TOOL_NAME, "arguments": "{}"},
                }],
            }
            preserve_reasoning_fields(assistant_msg, result, tool_turn=True)
            tool_chain.append(assistant_msg)
            tool_chain.append({
                "role": "tool",
                "tool_call_id": _SYNTHETIC_END_REPLY_CALL_ID,
                "content": '{"ok": true, "action": "end_reply"}',
            })
            log("文本形态的 end_reply，按结束意图规范入链并结束（不投递）", "WARNING", tag="思维")
            execution_steps.append(
                f"→ 第{state.iteration + 1}轮: 文本形态 end_reply，按结束处理"
            )
            return await _finish_round(ctx, state, explicit_end=True)
        _append_assistant_msg(tool_chain, result, raw_text)
        ctx.collected_text.append(raw_text)

        bg = ctx.background
        running_bg: List[BackgroundTaskInfo] = []
        if ctx.mode == ThinkMode.REPLY and ctx.anything and bg is not None:
            running_bg = bg.running(ctx.current_scope, joinable_only=True)

        waiting = bool(running_bg and state.wait_budget > 0)
        if ctx.mode == ThinkMode.REPLY:
            from agent.mind.execution.reply_delivery import deliver_reply, publish_delivery
            receipt, aborted = await _await_with_abort(deliver_reply(
                ctx, state, raw_text, interim=waiting, messages=ctx.base_messages + tool_chain[:-1],
            ), ctx.abort_event)
            await publish_delivery(ctx, state, receipt)
            if aborted:
                state.interrupted = True
                state.completion_reason = "interrupted"
                return await _finish_round(ctx, state)

        if bg is not None and ctx.anything is not None and waiting:
            new_user = await _merge_new_messages(ctx, state)
            current_bg = bg.running(ctx.current_scope, joinable_only=True)
            running_ids = {task.task_id for task in current_bg}
            completed_during_delivery = any(task.task_id not in running_ids for task in running_bg)
            if new_user or completed_during_delivery:
                if state.reply_delivery_status != "unknown":
                    state.auto_reply_blocked = False
                state.iteration += 1
                return RoundOutcome.CONTINUE
            running_bg = current_bg
            # 等待挂起：后台任务运行中时的纯文本一律视为等待——挂起会合
            # （结构性判定，不解析文本语义）。
            # 挂起期间新消息照常实时入库，中断/新消息/完成/超时都会安全唤醒；
            # 超时只清零等待预算，由 AI 继续操作或提交最终回复。
            reason, completions, elapsed = await _suspend_for_background(
                ctx.mind, ctx.anything, bg, ctx.current_scope,
                state.last_merged_ts, min(ctx.wait_per_round, state.wait_budget), ctx.interrupts,
                since_id=state.last_merged_id,
            )
            execution_steps.append(
                f"→ 第{state.iteration + 1}轮: 等待后台任务（{reason}，{elapsed:.0f}s）"
            )
            if reason == "completed":
                state.wait_budget -= elapsed
                if state.reply_delivery_status != "unknown":
                    state.auto_reply_blocked = False
                tool_chain.append({
                    "role": "system",
                    "content": _format_task_completions(
                        completions, bg.running(ctx.current_scope),
                    ),
                    "_source": {"origin": "background_task"},
                })
            elif reason == "timeout":
                state.wait_budget = 0.0
                tool_chain.append({
                    "role": "system",
                    "content": _PROMPT_TASKS_STILL_RUNNING.format(
                        tasks=_format_running_tasks(running_bg),
                    ),
                    "_source": {"origin": "background_task"},
                })
            # interrupted：不追加提示，循环顶部统一并入新消息 / 处理中断
            state.iteration += 1
            return RoundOutcome.CONTINUE

        if ctx.mode == ThinkMode.REFLECT:
            # 反思模式：连续纯文本达到上限即收束（产出已累积在 collected_text）；
            # 收束边界先消费 after 档追加指令——有后续要求则续跑而非结束
            state.reflect_text_rounds += 1
            if state.reflect_text_rounds >= _MAX_REFLECT_TEXT_ROUNDS:
                if merge_after_messages(ctx, state):
                    state.reflect_text_rounds = 0
                    state.iteration += 1
                    return RoundOutcome.CONTINUE
                log(
                    f"反思连续纯文本 {state.reflect_text_rounds} 次，结束本轮反思",
                    "WARNING", tag="思维",
                )
                execution_steps.append(
                    f"→ 第{state.iteration + 1}轮: 反思连续纯文本 {state.reflect_text_rounds} 次，结束"
                )
                return await _finish_round(ctx, state)
            tool_chain.append({"role": "system", "content": _PROMPT_CONTINUE})
            execution_steps.append(f"→ 第{state.iteration + 1}轮: {ctx.mode_label}中")
        else:
            return await _finish_round(ctx, state)

    state.iteration += 1
    return RoundOutcome.CONTINUE
