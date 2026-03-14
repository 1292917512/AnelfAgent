"""决策执行器：各类决策的具体执行逻辑。

函数以 mind 实例为第一参数，由 Mind 方法委托调用。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Dict, List, Optional, Union

from core.event_bus import (
    event_bus,
    EVENT_THINKING_SESSION_START,
    EVENT_THINKING_SESSION_END,
    EVENT_THINKING_INTROSPECTION,
)
from agent.core.messages import (
    Everything,
    MessageAssistant,
    MessageAssistantGroup,
)
from agent.core.mind.autonomous import Decision, DecisionType
from agent.core.mind.heartbeat import append_entry as _hb_append
from agent.core.mind.memory.memory_types import MemoryEntry, MemoryType
from agent.core.mind.autonomous import MindPhase
from core.log import log

if TYPE_CHECKING:
    from agent.core.mind.mind import Mind


async def execute_decision(mind: Mind, decision: Decision) -> None:
    """根据决策类型分发执行。"""
    log(f"执行决策: {decision.type.value} target={decision.target} reason={decision.reason}", tag="思维")

    if decision.type == DecisionType.REPLY:
        await execute_reply(mind, decision)
    elif decision.type == DecisionType.REFLECT:
        await execute_reflect(mind, decision)
    elif decision.type == DecisionType.REMEMBER:
        await execute_remember(mind, decision)
    elif decision.type == DecisionType.PROACTIVE:
        await execute_proactive(mind, decision)
    elif decision.type == DecisionType.TOOL_ACTION:
        await execute_tool_action(mind, decision)
    elif decision.type == DecisionType.PLAN:
        await execute_plan(mind, decision)
    elif decision.type == DecisionType.SELF_TASK:
        await execute_self_task(mind, decision)


async def execute_reply(mind: Mind, decision: Decision) -> None:
    """执行回复决策：解析目标、收集媒体，然后进入多轮思考循环。"""
    target = decision.target or ""
    anything = resolve_reply_target(mind, target)
    if not anything:
        anything = await pop_next_reply_target(mind)
    if not anything:
        return

    scope = target or mind._resolve_entity_scope(anything)
    if scope in mind._active_scopes:
        log(f"跳过重复回复: {scope}", "DEBUG", tag="思维")
        return
    mind._active_scopes.add(scope)
    mind._reply_idle_event.clear()
    try:
        mind._reply_adapter_key = getattr(anything, "adapter_key", "") or ""
        mind._set_phase(MindPhase.RECALLING)

        pending_images = mind._collect_pending_images()
        if pending_images:
            log(f"注入待处理图片: {len(pending_images)} 张", tag="思维")

        pending_media = mind.pfc.collect_media()
        if mind.media_pipeline and pending_media:
            media_texts = await mind.media_pipeline.process_segments(pending_media)
            if media_texts:
                combined = '\n'.join(media_texts)
                await mind._add_system_context(anything, combined)

        await mind.reply(anything, pending_images)
    finally:
        mind._active_scopes.discard(scope)
        if not mind._active_scopes:
            mind._reply_idle_event.set()
            mind._set_phase(MindPhase.IDLE)


async def execute_reflect(mind: Mind, decision: Optional[Decision] = None, *, skip_interval: bool = False) -> int:
    """执行反思决策：运行内省编排器（支持有实体和无实体两种模式）。

    REFLECT 始终以后台 asyncio.Task 方式执行，原会话在此之前已结束。
    因此本方法主动发射 SESSION_START / SESSION_END，为反思过程创建独立的追踪会话，
    确保所有 INTROSPECTION 事件都能被 Tracer 正确捕获。

    反思期间外部消息会暂缓处理（留在 PFC 队列），反思结束后自动排空。

    Args:
        mind: Mind 实例
        decision: 决策对象（可选，Web 手动触发时为 None）
        skip_interval: 是否跳过间隔检查（手动触发时为 True）

    Returns:
        产出模块数量
    """
    mind._reflecting = True
    mind._set_phase(MindPhase.INTROSPECTING)

    intro_cfg = mind.intro.config
    if not skip_interval:
        hours_since = (time.time() - intro_cfg.last_reflect_time) / 3600.0
        if intro_cfg.last_reflect_time > 0 and hours_since < intro_cfg.reflect_min_hours:
            log(f"反思间隔不足 ({hours_since:.1f}h < {intro_cfg.reflect_min_hours}h)，跳过", tag="思维")
            _hb_append(f"反思跳过: 间隔仅 {hours_since:.1f}h < {intro_cfg.reflect_min_hours}h")
            mind._reflecting = False
            return 0

    analysis_entity = await mind.pfc.pop_analysis_task()
    memory_warnings_checked: bool = bool(decision.params.get("memory_warnings_checked", False)) if decision else False
    entity_desc = analysis_entity.get_entity_desc() if analysis_entity else "全局"

    await event_bus.emit(EVENT_THINKING_SESSION_START, {
        "is_heartbeat": True,
        "is_introspection": True,
        "entity": entity_desc,
    })

    try:
        await event_bus.emit(EVENT_THINKING_INTROSPECTION, {
            "stage": "start",
            "entity": entity_desc,
        })
        results = await mind.intro.run(
            analysis_entity,
            memory_warnings_checked=memory_warnings_checked,
        )
        await event_bus.emit(EVENT_THINKING_INTROSPECTION, {
            "stage": "end",
            "entity": entity_desc,
            "module_count": len(results) if results else 0,
        })

        if analysis_entity and results:
            await mind._add_system_context(
                analysis_entity,
                f"[内省] 已对 {entity_desc} 完成分析 ({len(results)} 个模块)",
            )

        extra_count = 0
        max_extra = 2
        while extra_count < max_extra and not mind.pfc.pending_analysis.is_empty():
            extra_entity = await mind.pfc.pop_analysis_task()
            if not extra_entity:
                break
            extra_desc = extra_entity.get_entity_desc()
            log(f"批量实体分析: {extra_desc} ({extra_count + 1}/{max_extra})", tag="思维")
            extra_result = await mind.intro.run_entity_only(extra_entity)
            if extra_result:
                await mind._add_system_context(
                    extra_entity,
                    f"[内省] 已对 {extra_desc} 完成画像分析",
                )
                _hb_append(f"批量画像: {extra_desc}")
            extra_count += 1

        total = (len(results) if results else 0) + extra_count
        mind._last_reflect_time = time.time()
        _hb_append(f"反思完成: {entity_desc} ({total} 个模块, +{extra_count} 个实体)")

        try:
            intro_cfg.last_reflect_time = mind._last_reflect_time
            intro_cfg.save()
        except Exception as e:
            log(f"反思配置保存失败: {e}", "DEBUG", tag="思维")
        return total
    finally:
        mind._reflecting = False
        await event_bus.emit(EVENT_THINKING_SESSION_END, {
            "reason": "introspection_completed",
        })
        if mind.pfc.has_pending_tasks():
            asyncio.create_task(
                mind.execute_mind(),
                name="agent.mind.post_reflect",
            )


async def execute_remember(mind: Mind, decision: Decision) -> None:
    """将决策内容存入语义记忆（去重后）。"""
    if not decision.content or not mind.memory_store:
        return
    if await is_duplicate_memory(mind, decision.content):
        log(f"记忆去重跳过: {decision.content[:80]}", tag="思维")
        return
    entry = MemoryEntry(
        memory_type=MemoryType.SEMANTIC,
        content=decision.content,
        importance=0.7,
    )
    if mind.embedder.available:
        entry.embedding = await mind.embedder.embed_one(decision.content)
    await mind.memory_store.add(entry)
    log(f"AI 主动记忆: {decision.content[:80]}", tag="思维")


async def is_duplicate_memory(mind: Mind, content: str) -> bool:
    """检查记忆是否与已有记忆高度相似。"""
    if not mind.memory_store:
        return False
    if mind.embedder.available:
        vec = await mind.embedder.embed_one(content)
        if vec:
            similar = await mind.memory_store.search_vector(vec, limit=3, min_score=0.80)
            if similar:
                log(f"记忆重复检测（向量 score={similar[0][1]:.2f}): {similar[0][0].content[:60]}", tag="思维")
                return True
    if await mind.memory_store.has_similar_content(content):
        log("记忆重复检测（全文）", tag="思维")
        return True
    return False


async def execute_proactive(mind: Mind, decision: Decision) -> None:
    """主动发送消息：将指令写入 PFC 短期记忆，然后进入思考循环。"""
    target = decision.target
    content = decision.content
    if not target or not content:
        return
    anything = resolve_reply_target(mind, target)
    if not anything:
        anything = build_proactive_target(mind, target)
    if not anything:
        log(f"PROACTIVE 无法构建目标: {target}", "WARNING", tag="思维")
        return

    proactive_prompt = (
        f"你要主动联系 {target}。\n"
        f"原因：{decision.reason or '主动关心'}\n"
        f"你想表达的内容：{content}\n"
        "请用自然的语气表达，不要提及这是系统指令，像朋友一样自然地说话。"
    )
    mind.pfc.add_temporary({"role": "user", "content": proactive_prompt})

    mind._reply_adapter_key = getattr(anything, "adapter_key", "") if anything else ""
    log(f"AI 主动消息: target={target}", tag="思维")
    await mind.reply(anything)


async def execute_tool_action(mind: Mind, decision: Decision) -> None:
    """自主执行工具操作：进入思维循环，AI 自主选择并调用工具。"""
    content = decision.content or ""
    if not content:
        return

    action_prompt = (
        f"你需要执行以下操作：{content}\n"
        f"原因：{decision.reason or '自主决策'}\n"
        "请使用合适的工具完成操作，完成后调用 end_reply。"
    )
    messages = (
            mind.char.get_personality_msg()
            + [{"role": "user", "content": action_prompt}]
    )
    try:
        output = await mind.reflect(messages)
        _hb_append(f"工具操作: {content[:60]}")
        log(f"AI 自主工具操作完成: {content[:60]}", tag="思维")
        if decision.target and output:
            anything = resolve_reply_target(mind, decision.target)
            if not anything:
                anything = build_proactive_target(mind, decision.target)
            if anything:
                await mind.channel_manager.reply(anything, output)
    except Exception as exc:
        _hb_append(f"工具操作失败: {content[:40]} - {exc}")
        log(f"AI 自主工具操作失败: {exc}", "WARNING", tag="思维")


async def execute_plan(mind: Mind, decision: Decision) -> None:
    """执行规划决策：构建规划 prompt 并进入思考循环。"""
    content = decision.content or ""
    if not content:
        return

    memory_msgs: List[Dict] = []
    if mind.retriever:
        memory_msgs = await mind.retriever.recall(
            [{"role": "user", "content": content}], top_k=3,
        )

    plan_prompt = (
        f"请根据以下规划说明：{content}\n"
        "使用工具 create_goal、list_goals、update_goal 管理目标计划。"
        "需要时使用 web_search 搜索相关信息。"
    )
    messages = (
            mind.char.get_personality_msg()
            + memory_msgs
            + [{"role": "user", "content": plan_prompt}]
    )
    try:
        await mind.reflect(messages)
        _hb_append(f"规划执行: {content[:60]}")
        log(f"AI 规划执行: {content[:60]}", "DEBUG", tag="思维")
    except Exception as exc:
        _hb_append(f"规划失败: {content[:40]} - {exc}")
        log(f"AI 规划执行失败: {exc}", "WARNING", tag="思维")


async def execute_self_task(mind: Mind, decision: Decision) -> None:
    """AI 自主执行待办任务：进入思维循环完成任务。"""
    content = decision.content or ""
    if not content:
        return

    task_prompt = (
        f"你有一个待办任务需要完成：{content}\n"
        "请使用合适的工具完成任务，完成后调用 end_reply。"
    )
    messages = (
            mind.char.get_personality_msg()
            + [{"role": "user", "content": task_prompt}]
    )
    try:
        await mind.reflect(messages)
        _hb_append(f"自主任务完成: {content[:60]}")
        log(f"AI 自主任务完成: {content[:60]}", tag="思维")
    except Exception as exc:
        _hb_append(f"自主任务失败: {content[:40]} - {exc}")
        log(f"AI 自主任务失败: {exc}", "WARNING", tag="思维")


def build_proactive_target(mind: Mind, target: str) -> Optional[Everything]:
    """根据 target 字符串构建主动消息目标对象。"""
    if not target:
        return None

    channel_keys = set(mind.channel_manager.list_channels().keys())
    if target in channel_keys:
        return MessageAssistant(uid="proactive", adapter_key=target)
    if not channel_keys:
        return None

    default_key = next(iter(channel_keys))
    if target.startswith("group_"):
        group_id: Union[int, str] = target[6:]
        try:
            group_id = int(group_id)
        except ValueError:
            pass
        return MessageAssistantGroup(group_id=group_id, adapter_key=default_key)

    uid: Union[int, str] = target.removeprefix("user_")
    try:
        uid = int(uid)
    except ValueError:
        pass
    return MessageAssistant(uid=uid, adapter_key=default_key)


def resolve_reply_target(mind: Mind, target: str) -> Optional[Everything]:
    """根据 target 在已知路由中查找并消费对应任务。

    支持格式：user_123 / group_456 / 纯 ID（自动补前缀）。
    """
    if not target:
        return None

    if target.startswith("group_"):
        group_id: Union[int, str] = target[6:]
        try:
            group_id = int(group_id)
        except ValueError:
            pass
        adapter_key = mind.pfc.get_adapter_key(target)
        mind.pfc.consume_group_task(group_id)
        return MessageAssistantGroup(group_id=group_id, adapter_key=adapter_key)

    if target.startswith("user_"):
        uid: Union[int, str] = target[5:]
        try:
            uid = int(uid)
        except ValueError:
            pass
        adapter_key = mind.pfc.get_adapter_key(target)
        mind.pfc.consume_user_task(uid)
        return MessageAssistant(uid=uid, adapter_key=adapter_key)

    scope_user = f"user_{target}"
    adapter_key = mind.pfc.get_adapter_key(scope_user)
    if mind.pfc.consume_user_task(target):
        log(f"将 target '{target}' 补充 user_ 前缀匹配到 {scope_user}", tag="思维")
        return MessageAssistant(uid=target, adapter_key=adapter_key)

    scope_group = f"group_{target}"
    adapter_key = mind.pfc.get_adapter_key(scope_group)
    if mind.pfc.consume_group_task(target):
        log(f"将 target '{target}' 补充 group_ 前缀匹配到 {scope_group}", tag="思维")
        return MessageAssistantGroup(group_id=target, adapter_key=adapter_key)

    return None


async def pop_next_reply_target(mind: Mind) -> Optional[Everything]:
    """从 PFC 取出下一个待回复目标。"""
    tasks = mind.pfc.peek_all_tasks()
    if not tasks:
        return None
    scope, uid, group_id, _ = tasks[0]
    adapter_key = mind.pfc.get_adapter_key(scope)
    if group_id and group_id not in (0, "0"):
        await mind.pfc.pop_group_task()
        return MessageAssistantGroup(group_id=group_id, adapter_key=adapter_key)
    await mind.pfc.pop_user_task()
    return MessageAssistant(uid=uid, adapter_key=adapter_key)
