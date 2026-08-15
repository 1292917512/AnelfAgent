"""Mind 自主循环块：态势收集 → 元决策 → 分发执行 → 动态工具收尾。

函数以 mind 实例为第一参数（与 agent.mind.tools.* 同风格），
Mind 类持有一行薄委托，调用方签名零变化。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List

from agent.heartbeat import (
    load_recent as _hb_load_recent,
)
from agent.heartbeat import (
    write_log as _hb_write,
)
from agent.llm.llm_client import LLMClient
from agent.mind.autonomous import (
    DECISION_TOOLS,
    Decision,
    DecisionType,
    MindPhase,
    MindTask,
    PendingMessage,
    SituationContext,
    TaskType,
    build_meta_decision_messages,
    parse_decisions_from_tool_calls,
)
from core.event_bus import (
    EVENT_THINKING_DECISION,
    EVENT_THINKING_SITUATION,
    event_bus,
)
from core.log import log
from core.trace_session import detach_thinking_session

if TYPE_CHECKING:
    from agent.mind.mind import Mind


async def _cycle_body(mind: "Mind", end_payload: Dict[str, Any], *, is_heartbeat: bool = False) -> None:
    """自主循环主体（会话生命周期由 _autonomous_cycle 管理）。"""

    if is_heartbeat:
        # 心跳 tick 后台执行，不阻塞主循环的消息处理
        if not mind._heartbeat_running:
            mind._heartbeat_running = True
            asyncio.create_task(mind._run_heartbeat_tick_bg())

    # 廉价前置判断：仅检查队列是否有待处理项，命中 fast-path 时跳过昂贵的 memory/goals 查询
    cheap_pending = mind.pfc.peek_all_tasks()
    cheap_tasks = mind.pfc.peek_general_tasks()
    cheap_profiles = len(mind.pfc.pending_analysis)
    if not cheap_pending and not cheap_tasks and not cheap_profiles and not is_heartbeat:
        end_payload["reason"] = "no_pending"
        return

    # fast-path：有消息、无任务/画像/目标时直接 REPLY，跳过元决策和昂贵查询
    if (not is_heartbeat and cheap_pending and not cheap_tasks
            and cheap_profiles == 0):
        # 惰性检查目标（仅在 fast-path 候选时查询）
        active_goals = await mind._collect_active_goals()
        if not active_goals:
            decisions = [
                Decision(type=DecisionType.REPLY, target=scope, priority=10)
                for scope, _, _, _ in cheap_pending
            ]
            log("fast-path: direct reply (no meta-decision)", tag="思维")
            # 构建最小态势供后续流程使用
            situation = SituationContext(
                pending_messages=[
                    PendingMessage(
                        scope=scope, uid=uid, group_id=gid,
                        preview=preview, timestamp=time.time(),
                        adapter_key=mind.pfc.get_adapter_key(scope),
                    )
                    for scope, uid, gid, preview in cheap_pending
                ],
                pending_tasks=[],
                pending_profile_count=0,
                recent_memories=[],
                last_reflect_time=mind._last_reflect_time,
                current_time=time.time(),
                is_heartbeat=False,
                connected_channels=[],
                active_goals=[],
                heartbeat_log="",
            )
            await mind._execute_decisions_and_finalize(
                end_payload, situation, decisions, is_heartbeat=False,
            )
            return

    situation = await mind._gather_situation(is_heartbeat=is_heartbeat)

    if not situation.has_pending and not is_heartbeat:
        end_payload["reason"] = "no_pending"
        return

    mind._set_phase(MindPhase.DECIDING)
    task_count = len(situation.pending_tasks)
    msg_count = len(situation.pending_messages)
    log(f"态势收集: {msg_count} 条消息, {task_count} 个任务", tag="思维")

    await event_bus.emit(EVENT_THINKING_SITUATION, {
        "message_count": msg_count,
        "task_count": task_count,
        "pending_messages": [
            {"scope": pm.scope, "preview": pm.preview[:80]}
            for pm in situation.pending_messages
        ],
        "active_goals": situation.active_goals[:5],
        "is_heartbeat": is_heartbeat,
    })

    # 简单场景快速路径：跳过元决策
    if (not is_heartbeat
            and situation.pending_messages
            and not situation.pending_tasks
            and situation.pending_profile_count == 0
            and not situation.active_goals):
        decisions = [
            Decision(type=DecisionType.REPLY, target=pm.scope, priority=10)
            for pm in situation.pending_messages
        ]
        log("fast-path: direct reply (no meta-decision)", tag="思维")
    else:
        decisions = await mind._think_and_decide(situation)

    # 代码级兜底：有 pending 消息但元决策全非 REPLY 时，为每个 scope 补充 REPLY
    if situation.pending_messages and not any(d.type == DecisionType.REPLY for d in decisions):
        for pm in situation.pending_messages:
            decisions.append(Decision(
                type=DecisionType.REPLY, target=pm.scope, priority=10,
                reason="代码级兜底: 有消息但未产生 REPLY 决策",
            ))
        log("兜底: 补充 REPLY 决策 (元决策未覆盖待处理消息)", "WARNING", tag="思维")

    log(f"决策结果: {', '.join(d.type.value for d in decisions)}", tag="思维")

    await mind._execute_decisions_and_finalize(
        end_payload, situation, decisions, is_heartbeat=is_heartbeat,
    )


async def _execute_decisions_and_finalize(
        mind: "Mind",
        end_payload: Dict[str, Any],
        situation: SituationContext,
        decisions: List[Decision],
        *,
        is_heartbeat: bool = False,
) -> None:
    """执行决策列表并完成周期收尾（供 fast-path 和主路径复用）。

    会话结束信息写入 end_payload，由 _autonomous_cycle 的 finally 统一发射，
    确保异常路径下会话也能按 id 关闭。
    """
    mind._set_phase(MindPhase.DECIDING)

    await event_bus.emit(EVENT_THINKING_DECISION, {
        "decisions": [
            {"type": d.type.value, "target": d.target, "reason": d.reason, "priority": d.priority}
            for d in decisions
        ],
    })

    sorted_decisions = sorted(decisions, key=lambda d: d.priority, reverse=True)
    immediate = [d for d in sorted_decisions if d.type not in mind._DEFERRED_DECISIONS]
    deferred = [d for d in sorted_decisions if d.type in mind._DEFERRED_DECISIONS]

    if mind._reflecting or mind.heartbeat_engine.reflection_pending:
        deferred = [d for d in deferred if d.type != DecisionType.REFLECT]

    for d in deferred:
        asyncio.create_task(
            mind._safe_execute(d),
            name=f"agent.mind.bg.{d.type.value}",
        )

    snapshot_count = len(mind.pfc.peek_general_tasks())

    exec_ok: List[bool] = []
    if immediate:
        exec_ok = list(await asyncio.gather(*(mind._safe_execute(d) for d in immediate)))
        if any(exec_ok):
            # 有实质进展（即时决策执行成功）：重置自动续轮退避
            mind._auto_cycle_retry = 0

    mind.pfc.clear_general_tasks_before(snapshot_count)

    exec_results: List[str] = [
        f"{d.type.value} {'成功' if ok else '失败'}"
        for d, ok in zip(immediate, exec_ok, strict=False)
    ]
    if is_heartbeat or decisions:
        _hb_write(
            task_names=[d.type.value for d in sorted_decisions],
            exec_results=exec_results,
            pending_messages=len(situation.pending_messages),
            active_goals=len(situation.active_goals),
        )

    await mind._clear_dynamic_tools_when_idle()

    end_payload.update({
        "decisions_executed": [d.type.value for d in immediate],
        "decisions_deferred": [d.type.value for d in deferred],
    })

    if mind.pfc.has_pending_tasks():
        mind._schedule_next_cycle("自主循环结束后仍有待处理任务")


async def _clear_dynamic_tools_when_idle(mind: "Mind") -> None:
    """等所有回复会话结束后再清动态工具，避免并行 REPLY 被提前清掉。

    不阻塞当前周期：仍有活跃会话时挂到后台等待，
    避免长时间回复把自主循环（含心跳）整体卡住。
    """
    if not mind._active_scopes:
        mind.pfc.clear_dynamic_tools()
        return
    if mind._dynamic_tools_clear_pending:
        return
    mind._dynamic_tools_clear_pending = True
    log(
        f"延迟清理动态工具：仍有活跃会话 {sorted(mind._active_scopes)}",
        "DEBUG", tag="思维",
    )

    async def _wait_and_clear() -> None:
        try:
            await mind._reply_idle_event.wait()
            if not mind._active_scopes:
                mind.pfc.clear_dynamic_tools()
        finally:
            mind._dynamic_tools_clear_pending = False

    asyncio.create_task(_wait_and_clear(), name="agent.mind.clear_dynamic_tools")


async def _run_heartbeat_tick_bg(mind: "Mind") -> None:
    """后台执行心跳 tick，完成后触发新周期（不阻塞主循环）。"""
    # tick 是独立后台工作，不属于派生它的思维会话，脱离其链路上下文
    detach_thinking_session()
    try:
        executed = await mind.heartbeat_engine.tick()
        if executed:
            log(f"心跳任务完成: {', '.join(executed)}", tag="心跳")
    except Exception as exc:
        log(f"心跳 tick 异常: {exc}", "WARNING", tag="心跳")
    finally:
        mind._heartbeat_running = False
        # 完成后触发新周期（心跳可能注入了新任务）
        if mind.pfc.has_pending_tasks():
            mind._schedule_next_cycle("心跳完成后仍有待处理任务")


async def _safe_execute(mind: "Mind", decision: Decision) -> bool:
    """安全执行决策，异常转为通用错误任务。返回是否成功。"""
    try:
        await mind._execute_decision(decision)
        return True
    except Exception as exc:
        log(f"决策执行异常 [{decision.type.value}]: {exc}", "WARNING", tag="思维")
        mind.pfc.add_general_task(MindTask(
            task_type=TaskType.ERROR,
            preview=f"{decision.type.value} 执行失败: {exc}",
            metadata={"decision": decision.type.value, "error": str(exc)},
        ))
        return False


async def _gather_situation(mind: "Mind", *, is_heartbeat: bool = False) -> SituationContext:
    """收集当前态势：待处理消息、记忆、通道、目标等（纯读取，无副作用）。"""
    pending: List[PendingMessage] = []
    for item in mind.pfc.peek_all_tasks():
        scope, uid, group_id, preview = item
        adapter_key = mind.pfc.get_adapter_key(scope)
        pending.append(PendingMessage(
            scope=scope, uid=uid, group_id=group_id,
            preview=preview, timestamp=time.time(),
            adapter_key=adapter_key,
        ))

    recent_mem_lines: list[str] = []
    if mind.memory_store:
        recent = await mind.memory_store.list_recent(limit=5)
        recent_mem_lines = [e.content[:100] for e in recent]

    connected_channels = mind._collect_channel_info()
    active_goals = await mind._collect_active_goals()
    general_tasks = mind.pfc.peek_general_tasks()
    heartbeat_log = _hb_load_recent(3) if is_heartbeat else ""

    return SituationContext(
        pending_messages=pending,
        pending_tasks=general_tasks,
        pending_profile_count=len(mind.pfc.pending_analysis),
        recent_memories=recent_mem_lines,
        last_reflect_time=mind._last_reflect_time,
        current_time=time.time(),
        is_heartbeat=is_heartbeat,
        connected_channels=connected_channels,
        active_goals=active_goals,
        heartbeat_log=heartbeat_log,
    )


async def _think_and_decide(mind: "Mind", situation: SituationContext) -> List[Decision]:
    """让 AI 根据态势做元决策，通过 Tool Calling 返回决策列表。"""
    memory_ctx: List[Dict] = []
    if mind.retriever:
        if situation.pending_messages:
            from agent.messages import build_entity_scope
            combined_preview = " ".join(pm.preview for pm in situation.pending_messages)
            first_pm = situation.pending_messages[0]
            entity_scope = ""
            pm_adapter = str(getattr(first_pm, "adapter_key", "") or "")
            if first_pm.group_id:
                entity_scope = build_entity_scope("group", pm_adapter, str(first_pm.group_id))
            elif first_pm.uid:
                entity_scope = build_entity_scope("user", pm_adapter, str(first_pm.uid))
            memory_ctx = await mind.retriever.recall(
                [{"role": "user", "content": combined_preview}],
                top_k=5, entity_scope=entity_scope,
            )
        elif situation.is_heartbeat:
            query_parts: list[str] = []
            for mem in situation.recent_memories[:3]:
                query_parts.append(mem)
            for goal in situation.active_goals[:3]:
                query_parts.append(goal)
            if query_parts:
                query = " ".join(query_parts)
                memory_ctx = await mind.retriever.recall(
                    [{"role": "user", "content": query}],
                    top_k=3,
                )

    messages = build_meta_decision_messages(
        mind.char.get_personality_msg(), situation, memory_ctx,
    )
    try:
        mc = mind._get_mind_config()
        opts = {"temperature": mc.meta_decision_temperature}
        tc = {"type": "function", "function": {"name": "decide"}}
        if mind.llm_manager:
            primary = mind.llm if isinstance(mind.llm, LLMClient) else None
            result = await mind.llm_manager.chat_with_fallback(
                messages,
                options=opts,
                tools=DECISION_TOOLS,
                tool_choice=tc,
                client=primary,
                max_retries=mc.llm_max_retries,
                timeout=mc.llm_timeout,
            )
        else:
            result = await mind.llm.chat(
                messages, options=opts,
                tools=DECISION_TOOLS, tool_choice=tc,
            )
        if mc.log_ai_output:
            tc_preview = ", ".join(t.name for t in result.tool_calls) if result.tool_calls else "?"
            log(f"元决策结果: tool_calls=[{tc_preview}] content={result.content[:100] if result.content else ''}",
                tag="思维")
        return parse_decisions_from_tool_calls(result.tool_calls, situation)
    except Exception as exc:
        log(f"元决策 LLM 调用失败（含重试和回退），使用兜底决策: {exc}", "WARNING", tag="思维")
        return mind._fallback_decisions(situation)


def _fallback_decisions(mind: "Mind", situation: SituationContext) -> List[Decision]:
    """元决策失败时的兜底：为每条待处理消息生成 REPLY 决策。"""
    decisions: List[Decision] = []
    for pm in situation.pending_messages:
        decisions.append(Decision(
            type=DecisionType.REPLY,
            target=pm.scope,
            priority=10,
        ))
    return decisions or [Decision(type=DecisionType.IDLE)]
