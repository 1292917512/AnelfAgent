"""Mind 自主循环块：态势收集 → 元决策 → 分发执行 → 动态工具收尾。

按态势、决策、分发和收尾组织函数；内部步骤直接调用所属模块。

模型可见：元决策召回围绕最早待处理会话，其他会话仍以独立待办呈现；
消息周期抑制工具行动时，通用待办保留到后续周期重新决策。
Token：不拼接多个会话生成混合召回查询；保留待办按正常续轮决策，不重放旧决策。
缓存：只影响本次元决策证据，不修改各会话稳定前缀。
"""

from __future__ import annotations

import time
from dataclasses import replace
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
    PendingMessage,
    SituationContext,
    build_meta_decision_messages,
    parse_decisions_from_tool_calls,
)
from agent.mind.cross_channel import collect_channel_info
from agent.mind.decisions import execute_decision, normalize_target_scope
from agent.planning.situation import active_goal_lines
from core.event_bus import (
    EVENT_THINKING_DECISION,
    EVENT_THINKING_SITUATION,
    event_bus,
)
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind


# 反思回执不回喂元决策，避免历史回执反复触发同类决策。
_DECISION_ECHO_PREFIXES = ("反思已登记", "反思完成", "反思登记被冷却拒绝")


def _strip_decision_echo(text: str) -> str:
    """剥离心跳日志中的反思域决策回执行行（保留维护议程类信息）。"""
    kept = [
        line for line in text.splitlines()
        if not line.lstrip("- ").startswith(_DECISION_ECHO_PREFIXES)
    ]
    return "\n".join(kept).strip()


async def run_cycle(mind: "Mind", end_payload: Dict[str, Any], *, is_heartbeat: bool = False) -> None:
    """自主循环主体（会话生命周期由 _autonomous_cycle 管理）。"""

    # 廉价前置判断：仅检查队列是否有待处理项，命中 fast-path 时跳过昂贵的 memory/goals 查询
    cheap_pending = mind.pfc.peek_all_tasks()
    cheap_tasks = mind.pfc.peek_general_tasks()
    cheap_profiles = len(mind.pfc.pending_analysis)
    if not cheap_pending and not cheap_tasks and not cheap_profiles and not is_heartbeat:
        end_payload["reason"] = "no_pending"
        return

    # 交互频道的请求由回复会话独占；无关后台目标不应再派生同一请求的工具反思。
    from agent.channel.reply_policy import get_reply_policy
    from agent.messages import parse_entity_scope

    def addressed(scope: str) -> bool:
        signal = mind.pfc.get_pending_signal(scope)
        return (signal is None or signal.kind in {"", "chat"}) and (
            parse_entity_scope(scope)[0] == "user" or bool(signal and signal.to_me))

    direct = [
        PendingMessage(scope=scope, uid=uid, group_id=gid, preview=preview,
                       adapter_key=mind.pfc.get_adapter_key(scope), timestamp=time.time())
        for scope, uid, gid, preview in cheap_pending
        if get_reply_policy(mind.pfc.get_adapter_key(scope)).direct_reply or addressed(scope)
    ]
    if direct:
        log(f"channel fast-path: {len(direct)} direct replies (no meta-decision)", tag="思维")
        await execute_decisions(
            mind, end_payload, SituationContext(pending_messages=direct),
            [Decision(type=DecisionType.REPLY, target=pm.scope, priority=10) for pm in direct],
            is_heartbeat=is_heartbeat, consume_general_tasks=False,
        )
        return

    # fast-path：有消息、无任务/画像/目标时直接 REPLY，跳过元决策和昂贵查询
    if (not is_heartbeat and cheap_pending and not cheap_tasks
            and cheap_profiles == 0):
        # 惰性检查目标（仅在 fast-path 候选时查询）
        active_goals = await active_goal_lines()
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
            await execute_decisions(
                mind, end_payload, situation, decisions, is_heartbeat=False,
            )
            return

    # 完整决策周期开始：DECIDING 覆盖态势收集 + 元决策全程（收集可能涉及
    # 记忆/目标查询耗时可观，期间不应显示 idle）；no_pending 早退由周期
    # 完成边界（execute_mind finally）统一归零
    mind.set_phase(MindPhase.DECIDING)
    situation = await gather_situation(mind, is_heartbeat=is_heartbeat)

    if not situation.has_pending and not is_heartbeat:
        end_payload["reason"] = "no_pending"
        return

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
        decisions = await think_and_decide(mind, situation)

    decisions = cover_pending_replies(mind, situation, decisions)

    log(f"决策结果: {', '.join(d.type.value for d in decisions)}", tag="思维")

    await execute_decisions(
        mind, end_payload, situation, decisions, is_heartbeat=is_heartbeat,
    )


def cover_pending_replies(mind: "Mind", situation: SituationContext, decisions: List[Decision]) -> List[Decision]:
    """规范化并去重 REPLY，只执行态势中的真实待办，补齐未覆盖会话。"""
    pending = {pm.scope for pm in situation.pending_messages}
    covered: set[str] = set()
    result: List[Decision] = []
    for decision in decisions:
        if decision.type != DecisionType.REPLY:
            result.append(decision)
            continue
        scope = normalize_target_scope(mind, decision.target or "")
        if scope not in pending or scope in covered:
            log(f"丢弃无效或重复 REPLY 决策: {decision.target}", "WARNING", tag="思维")
            continue
        covered.add(scope)
        result.append(replace(decision, target=scope))
    result.extend(Decision(type=DecisionType.REPLY, target=pm.scope, priority=10,
                           reason="待处理消息未被有效 REPLY 覆盖")
                  for pm in situation.pending_messages if pm.scope not in covered)
    return result


async def execute_decisions(
        mind: "Mind",
        end_payload: Dict[str, Any],
        situation: SituationContext,
        decisions: List[Decision],
        *,
        is_heartbeat: bool = False,
        consume_general_tasks: bool = True,
) -> None:
    """执行决策列表并完成周期收尾（供 fast-path 和主路径复用）。

    会话结束信息写入 end_payload，由 _autonomous_cycle 的 finally 统一发射，
    确保异常路径下会话也能按 id 关闭。
    """
    # 用户请求只由 REPLY 的工具循环执行：同一指令若再派生 TOOL_ACTION，
    # 会在另一条 reflect 工具循环中重复执行。心跳周期的自主维护仍可使用 TOOL_ACTION。
    if not is_heartbeat and situation.pending_messages:
        suppressed = [d for d in decisions if d.type == DecisionType.TOOL_ACTION]
        if suppressed:
            decisions = [d for d in decisions if d.type != DecisionType.TOOL_ACTION]
            # 决策与通用待办没有逐项对应关系，不能确认被抑制行动的待办已完成。
            consume_general_tasks = False
            log(f"用户消息周期抑制 {len(suppressed)} 个 TOOL_ACTION 决策"
                "（由 REPLY 工具循环统一执行）", tag="思维")

    await event_bus.emit(EVENT_THINKING_DECISION, {
        "decisions": [
            {"type": d.type.value, "target": d.target, "reason": d.reason, "priority": d.priority}
            for d in decisions
        ],
    })

    sorted_decisions = sorted(decisions, key=lambda d: d.priority, reverse=True)
    if mind._reflecting or mind.heartbeat_engine.reflection_pending:
        sorted_decisions = [d for d in sorted_decisions if d.type != DecisionType.REFLECT]

    snapshot_count = len(mind.pfc.peek_general_tasks()) if consume_general_tasks else 0
    executed: List[tuple[Decision, bool]] = []
    yielded: List[Decision] = []
    for index, decision in enumerate(sorted_decisions):
        ok = await execute_safely(mind, decision)
        executed.append((decision, ok))
        # 无通用待办需要结算时，心跳决策在用户消息到达后让路；
        # 有快照待办时先完成本批，避免执行与清除口径错位。
        if (
            is_heartbeat and snapshot_count == 0
            and (not mind.pfc.pending_user.is_empty() or not mind.pfc.pending_group.is_empty())
        ):
            yielded = sorted_decisions[index + 1:]
            if yielded:
                log(f"用户消息到达，{len(yielded)} 个心跳决策延后到后续周期", tag="思维")
            mind.driver.request_cycle()
            break

    progressed = any(ok and decision.type != DecisionType.IDLE for decision, ok in executed)
    if progressed:
        # 有实质进展（决策执行成功）：重置自动续轮退避
        mind._auto_cycle_retry = 0

    if not yielded:
        mind.pfc.clear_general_tasks_before(snapshot_count)

    exec_results: List[str] = [
        f"{decision.type.value} {'成功' if ok else '失败'}"
        for decision, ok in executed
    ]
    exec_results.extend(f"{decision.type.value} 用户让路" for decision in yielded)
    if is_heartbeat or decisions:
        _hb_write(
            task_names=[d.type.value for d in sorted_decisions],
            exec_results=exec_results,
            pending_messages=len(situation.pending_messages),
            active_goals=len(situation.active_goals),
        )

    mind.tool_assembly.clear_when_idle(mind._active_scopes, mind._reply_idle_event)

    end_payload.update({
        "decisions_executed": [decision.type.value for decision, ok in executed if ok],
        "decisions_failed": [decision.type.value for decision, ok in executed if not ok],
        "decisions_deferred": [decision.type.value for decision in yielded],
    })

    if mind.pfc.has_pending_tasks():
        mind._schedule_next_cycle("自主循环结束后仍有待处理任务", backoff=not progressed)


async def execute_safely(mind: "Mind", decision: Decision) -> bool:
    """返回实际执行结果；失败记录到日志和周期结果，不派生可再次执行的错误任务。"""
    try:
        return await execute_decision(mind, decision)
    except Exception as exc:
        log(f"决策执行异常 [{decision.type.value}]: {exc}", "WARNING", tag="思维")
        return False


async def gather_situation(mind: "Mind", *, is_heartbeat: bool = False) -> SituationContext:
    """收集当前态势：待处理消息、记忆、通道、目标等（纯读取，无副作用）。"""
    pending: List[PendingMessage] = []
    for item in mind.pfc.peek_all_tasks():
        scope, uid, group_id, preview = item
        adapter_key = mind.pfc.get_adapter_key(scope)
        signal = mind.pfc.get_pending_signal(scope)
        pending.append(PendingMessage(
            scope=scope, uid=uid, group_id=group_id,
            preview=preview, timestamp=time.time(),
            adapter_key=adapter_key,
            to_me=signal.to_me if signal else False,
            kind=signal.kind if signal else "",
        ))

    recent_mem_lines: list[str] = []
    if mind.memory_store:
        recent = await mind.memory_store.list_recent(limit=5)
        recent_mem_lines = [e.content[:100] for e in recent]

    connected_channels = collect_channel_info(mind)
    active_goals = await active_goal_lines()
    general_tasks = mind.pfc.peek_general_tasks()
    heartbeat_log = _hb_load_recent(3) if is_heartbeat else ""
    if heartbeat_log:
        heartbeat_log = _strip_decision_echo(heartbeat_log)

    # 出站事实（其他思维链近期已向各会话投递的内容）：元决策不经 think_loop
    # 的 provider 收集，态势里显式携带（渲染零 I/O，无事实返回空串不呈现）
    from agent.channel.outbound_guard import render_outbound_facts
    outbound_facts = render_outbound_facts("_global")

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
        outbound_facts=outbound_facts,
    )


async def think_and_decide(mind: "Mind", situation: SituationContext) -> List[Decision]:
    """让 AI 根据态势做元决策，通过 Tool Calling 返回决策列表。"""
    memory_ctx: List[Dict] = []
    if mind.retriever:
        if situation.pending_messages:
            first_pm = situation.pending_messages[0]
            memory_ctx = await mind.retriever.recall(
                [{"role": "user", "content": first_pm.preview}],
                top_k=5, entity_scope=first_pm.scope,
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
        mc = mind.config
        opts = {"temperature": mc.meta_decision_temperature}
        tc = {"type": "function", "function": {"name": "decide"}}
        models = mind.models
        if models.llm_manager:
            primary = models.llm if isinstance(models.llm, LLMClient) else None
            result = await models.llm_manager.chat_with_fallback(
                messages,
                options=opts,
                tools=DECISION_TOOLS,
                tool_choice=tc,
                client=primary,
                max_retries=mc.llm_max_retries,
                timeout=mc.llm_timeout,
            )
        else:
            result = await models.llm.chat(
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
        return fallback_decisions(situation)


def fallback_decisions(situation: SituationContext) -> List[Decision]:
    """元决策失败时的兜底：为每条待处理消息生成 REPLY 决策。"""
    decisions: List[Decision] = []
    for pm in situation.pending_messages:
        decisions.append(Decision(
            type=DecisionType.REPLY,
            target=pm.scope,
            priority=10,
        ))
    return decisions or [Decision(type=DecisionType.IDLE)]
