"""执行轮次的上下文准备；保持压缩、消息水位与动态采集顺序。"""

from __future__ import annotations

from agent.channel.delivery import observe_delivery_context
from agent.context.dynamic import append_execution_status, format_delivery_status
from agent.context.providers import collect_provider_messages
from agent.mind.background.context import background_context
from agent.mind.execution.compression import _compress_context, _precompact_flush
from agent.mind.execution.messages import _merge_new_messages, _merge_pushes, _merge_steered_messages
from agent.mind.execution.state import ExecutionContext, RoundState, ThinkMode
from agent.mind.execution.usage import _cache_status_hint, _token_budget_hint
from agent.mind.recall.refresh import DialogueRecollection
from core.log import log


async def prepare_round_context(
    ctx: ExecutionContext, state: RoundState, safety_limit: int, start_time: float,
    recollection: DialogueRecollection,
) -> tuple[list[dict], bool]:
    """压缩、实时并入、动态取数与组装按固定顺序完成。"""
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
        try:
            async with mind.compressor.scope_lock(ctx.current_scope):
                # PreCompact flush：压缩前把该会话的待定对话抢跑沉淀为
                # 长期记忆，防止压缩摘要丢弃未提取的细节（信息不失帧）；
                # fail-open，提取失败/超时绝不阻断压缩
                if await _precompact_flush(mind, ctx.current_scope):
                    execution_steps.append(
                        f"→ 第{state.iteration + 1}轮前: 压缩前已沉淀待定记忆"
                    )
                ctx.base_messages, ctx.tool_chain = await _compress_context(
                    mind, ctx.base_messages, ctx.tool_chain, ctx.current_scope,
                    tools=ctx.active_tools,
                )
        except Exception as exc:
            # 估算/手动触发的压缩失败不应杀死整轮回复：
            # 本轮不压缩继续（硬溢出路径由 _handle_overflow 另行处理，保留原语义）
            log(f"上下文压缩失败，本轮不压缩继续: {exc}", "WARNING", tag="压缩")
        else:
            # 压缩后旧真用量已失真，清零避免下轮以过期值重复触发压缩
            state.last_input_tokens = 0
            execution_steps.append(f"→ 第{state.iteration + 1}轮前: 上下文已压缩")

    # 并入循环期间新增的收发消息（让 AI 在当前回复中一并处理，
    # 而非另起周期导致上下文断裂/忘记已回复）
    await _merge_new_messages(ctx, state)
    recollection.offer(ctx.base_messages + ctx.tool_chain, watermark=(state.last_merged_ts, state.last_merged_id))
    # 并入循环期间到达的实体推送（[push:] 系统通知，轮内弹窗）
    await _merge_pushes(ctx, state)
    # 并入转向指令（子代理步骤边界；主会话未绑定 drain 时零开销）
    _merge_steered_messages(ctx, state)

    exec_context = mind.context.build_execution_context(
        execution_steps, start_time, state.iteration,
        adapter_key=ctx.adapter_key, safety_limit=safety_limit,
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
    # exec_context（每轮动态）置于末尾：保持 stable/context/volatile/历史前缀
    # 字节稳定供 Prompt Caching 复用，且当前轮状态在模型注意力最强的末尾位置
    # （缓存断点不在此注入——发送边界由 llm/prompt_cache 按 _layer 统一装饰，
    # 链尾锚点天然随链增长前移）
    # provider 实时注入：每轮收集最新快照，置于工具链之后、exec_context 之前
    provider_msgs = await collect_provider_messages(getattr(getattr(mind, "llm", None), "config", None), ctx.current_scope)
    from agent.memory.probe import deep_probe_hub
    for message in provider_msgs:
        if message.get("_source", {}).get("provider") == "memory_deep_probe":
            deep_probe_hub.acknowledge(ctx.current_scope, message["content"])
    from agent.mind.cross_channel import build_cross_channel_narrative
    try:
        cross_messages = await build_cross_channel_narrative(mind, ctx.current_scope)
        provider_msgs = cross_messages + provider_msgs
        from agent.context.observation import emit_cross_channel_updates
        await emit_cross_channel_updates(cross_messages, state.observed_cross_messages, state.iteration + 1)
    except Exception as exc:
        log(f"跨会话上下文读取失败: {exc}", "WARNING", tag="思维")
    llm_messages = (
        ctx.base_messages + ctx.tool_chain + provider_msgs + [exec_context]
    )
    from agent.context.observation import emit_recollection_update
    state.observed_recollection = await emit_recollection_update(provider_msgs, state.observed_recollection)
    observe_delivery_context(provider_msgs)

    return llm_messages, require_tools
