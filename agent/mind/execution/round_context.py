"""执行轮次的上下文准备与发送快照组装。

模型可见：主回复与主自主工作感知待回复消息；自主工作可选择先回复再继续，隔离执行仅接收任务与显式转向。
Token：复用动态通知与正常回复调用；恢复只追加执行状态，显式 skill 标签沿用正文预算。
缓存：对话只追加执行链；通知与实时状态仍在动态尾部，不改稳定前缀。
"""

from __future__ import annotations

from agent.channel.delivery import observe_delivery_context
from agent.context.dynamic import append_execution_status, format_delivery_status
from agent.context.providers import collect_provider_messages
from agent.judgment.evidence import dialogue_messages
from agent.mind.background.context import background_context
from agent.mind.execution.compression import _compress_context, _precompact_flush
from agent.mind.execution.messages import _merge_new_messages, _merge_pushes, _merge_steered_messages
from agent.mind.execution.state import ExecutionContext, RoundState, ThinkMode
from agent.mind.execution.tool_execution import _await_with_abort
from agent.mind.execution.usage import _cache_status_hint, _token_budget_hint
from agent.mind.recall.refresh import DialogueRecollection
from core.activity import activity_step
from core.log import log


async def prepare_round_context(
    ctx: ExecutionContext, state: RoundState, safety_limit: int, start_time: float,
    recollection: DialogueRecollection,
) -> tuple[list[dict], bool]:
    """完成压缩和动态媒体准备后，合并实时消息并组装本次调用的输入。"""
    mind, anything, mode = ctx.mind, ctx.anything, ctx.mode
    execution_steps = ctx.execution_steps
    # Microcompact：完整压缩前的轻量清理（旧只读工具结果 → 占位符）
    if mind.compressor is not None:
        mind.compressor.microcompact(ctx.tool_chain)

    # 上下文压缩：溢出风险（或手动请求）时压缩中间轮次
    if mind.compressor is not None and mind.compressor.should_compress(
        ctx.base_messages + ctx.tool_chain,
        last_input_tokens=state.last_input_tokens,
        scope=ctx.current_scope,
    ):
        async def compress() -> None:
            async with activity_step("context_compress"), mind.compressor.scope_lock(ctx.current_scope):
                async with activity_step("memory_flush"):
                    flushed = await _precompact_flush(mind, ctx.current_scope)
                if flushed:
                    execution_steps.append(f"→ 第{state.iteration + 1}轮前: 压缩前已沉淀待定记忆")
                ctx.base_messages, ctx.tool_chain = await _compress_context(
                    mind, ctx.base_messages, ctx.tool_chain, ctx.current_scope, tools=ctx.active_tools,
                )

        try:
            _, aborted = await _await_with_abort(compress(), ctx.abort_event)
            if aborted:
                return [], False
        except Exception as exc:
            # 估算/手动触发的压缩失败不应杀死整轮回复：
            # 本轮不压缩继续（硬溢出路径由 _handle_overflow 另行处理，保留原语义）
            log(f"上下文压缩失败，本轮不压缩继续: {exc}", "WARNING", tag="压缩")
        else:
            # 压缩后旧真用量已失真，清零避免下轮以过期值重复触发压缩
            state.last_input_tokens = 0
            execution_steps.append(f"→ 第{state.iteration + 1}轮前: 上下文已压缩")

    provider_msgs = await collect_provider_messages(getattr(mind.models.llm, "config", None), ctx.current_scope)
    from agent.memory.probe import deep_probe_hub
    for message in provider_msgs:
        if message.get("_source", {}).get("provider") == "memory_deep_probe":
            deep_probe_hub.acknowledge(ctx.current_scope, message["content"])

    await _merge_new_messages(ctx, state)
    recollection.offer(ctx.base_messages + ctx.tool_chain, watermark=(state.last_merged_ts, state.last_merged_id))
    # 并入循环期间到达的实体推送（[push:] 系统通知，轮内弹窗）
    await _merge_pushes(ctx, state)
    # 并入转向指令（子代理步骤边界；主会话未绑定 drain 时零开销）
    _merge_steered_messages(ctx, state)

    if ctx.skill_context is not None:
        latest_input = next((message["content"] for message in reversed(dialogue_messages(ctx.base_messages))
                             if message.get("role") == "user"), "")
        provider_texts = [message["content"] for message in provider_msgs
                          if message.get("_source", {}).get("origin") == "context_provider"
                          and isinstance(message.get("content"), str)]
        provider_msgs.extend(await ctx.skill_context.build(latest_input, provider_texts, scope=ctx.current_scope))

    if mode == ThinkMode.REPLY or ctx.attend_conversations:
        from agent.mind.cross_channel import build_cross_channel_narrative
        try:
            cross_messages = await build_cross_channel_narrative(mind, ctx.current_scope)
            if cross_messages and ctx.attend_conversations:
                cross_messages.append({
                    "role": "system", "_layer": "provider", "_source": {"origin": "session_attention"},
                    "content": "[自主工作中的新消息] 请自行判断是否现在处理以上会话，也可先继续当前工作。"
                               "需要先处理时调用 switch_session(scope)：本批工具完成后将用目标会话的独立上下文回复，"
                               "然后自动回到当前任务，保留已有进展，无需结束或重新启动任务。"
                               "通知本身不表示已回复；不切换的待办会在当前工作结束后处理。",
                })
            provider_msgs = cross_messages + provider_msgs
            from agent.context.observation import emit_cross_channel_updates
            await emit_cross_channel_updates(cross_messages, state.observed_cross_messages, state.iteration + 1)
        except Exception as exc:
            log(f"跨会话上下文读取失败: {exc}", "WARNING", tag="思维")

    # 合并新消息可能重建媒体工具目录，以本次模型请求的最终目录为准。
    catalog = () if ctx.summary_only else ctx.active_tools
    state.available_tool_names = frozenset(tool["function"]["name"] for tool in catalog) - ctx.blocked_tools
    exec_context = mind.context.build_execution_context(
        execution_steps, start_time, state.iteration,
        adapter_key=ctx.adapter_key, safety_limit=safety_limit,
        available_tool_names=state.available_tool_names,
        anything=anything,
        budget_hint=_token_budget_hint(ctx, state),
        cache_hint=_cache_status_hint(state),
    )
    from agent.mind.tool_activation import current_owner_scope
    task_context = background_context(ctx.background, current_owner_scope() or ctx.current_scope)
    reply = mode == ThinkMode.REPLY
    delivery = (
        format_delivery_status(
            ctx.current_scope, state.reply_delivery_status, output_sent=state.output_sent,
            auto_reply_blocked=state.auto_reply_blocked, auto_reply_eligible=state.auto_reply_eligible,
        ) if reply else ""
    )
    # 纯工具模式（可选）且有可用工具时，API 级强制工具选择
    require_tools = bool(ctx.active_tools) and ctx.pure_tool_mode and not ctx.summary_only
    append_execution_status(
        exec_context, background=task_context, reply=reply, delivery=delivery,
        output_blocked="send_message" in ctx.blocked_tools,
    )
    llm_messages = (
        ctx.base_messages + ctx.tool_chain + provider_msgs + [exec_context]
    )
    deep_probe_hub.acknowledge_base(ctx.current_scope, llm_messages)
    from agent.context.observation import emit_recollection_update
    state.observed_recollection = await emit_recollection_update(provider_msgs, state.observed_recollection)
    observe_delivery_context(provider_msgs)

    return llm_messages, require_tools


async def attend_pending_conversations(ctx: ExecutionContext, state: RoundState) -> None:
    """让主自主工作在完整工具批次后处理其选中的会话，并记录恢复事实。"""
    outcomes, aborted = await _await_with_abort(
        ctx.mind.driver.service_session_attention(ctx.current_scope), ctx.abort_event,
    )
    if aborted:
        state.interrupted = True
        state.completion_reason = "interrupted"
        return
    for scope, ok in outcomes:
        status = "回复执行已结束，送达情况以该会话回执为准" if ok else "回复执行失败，未确认送达"
        ctx.tool_chain.append({
            "role": "system", "content": f"[返回原任务] {scope}：{status}。当前已恢复原任务上下文，请从已有进展继续。",
            "_layer": "tool_chain", "_source": {"origin": "session_resume"},
        })
        ctx.execution_steps.append(f"→ 处理会话 {scope} 后返回原任务：{status}")
