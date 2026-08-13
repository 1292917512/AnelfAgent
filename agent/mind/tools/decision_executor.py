"""决策执行器：各类决策的具体执行逻辑。

函数以 mind 实例为第一参数，由 Mind 方法委托调用。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Dict, List, Optional, Union

from agent.heartbeat.log import append_entry as _hb_append
from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.messages import (
    Everything,
    MessageAssistant,
    MessageAssistantGroup,
    parse_entity_scope,
)
from agent.mind.autonomous import Decision, DecisionType, MindPhase
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind


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

    # scope 一律按解析后的实体规范化（target 可能是裸 ID "123"，
    # 直接用会导致去重登记与中断匹配失效）
    scope = mind._resolve_entity_scope(anything)
    if scope in mind._active_scopes:
        log(f"跳过重复回复: {scope}", "DEBUG", tag="思维")
        return
    mind._active_scopes.add(scope)
    mind._reply_idle_event.clear()
    try:
        adapter_key = getattr(anything, "adapter_key", "") or ""
        mind._set_phase(MindPhase.RECALLING)

        pending_images = mind._collect_pending_images(scope=scope)
        if pending_images:
            log(f"注入待处理图片: {len(pending_images)} 张", tag="思维")

        pending_media = mind.pfc.collect_media(scope=scope)
        # 按实际携带的媒体激活媒体工具（recognize_image 等），确保本轮 schema 可用
        if pending_images or pending_media:
            mind.pfc.activate_media_tools(pending_images, pending_media)
        if mind.media_pipeline and pending_media:
            media_texts = await mind.media_pipeline.process_segments(pending_media)
            if media_texts:
                combined = '\n'.join(media_texts)
                await mind._add_system_context(anything, combined)

        await mind.reply(anything, pending_images, adapter_key=adapter_key)
    finally:
        mind._active_scopes.discard(scope)
        if not mind._active_scopes:
            mind._reply_idle_event.set()
            mind._set_phase(MindPhase.IDLE)


async def execute_reflect(mind: Mind, decision: Optional[Decision] = None, *, skip_interval: bool = False) -> int:
    """执行反思决策：通过心跳引擎运行自我反思任务。"""
    mind._reflecting = True
    mind._set_phase(MindPhase.INTROSPECTING)

    try:
        result = await mind.heartbeat_engine.run_task("self_reflection")
        count = 1 if result else 0
        _hb_append(f"反思完成: {'有产出' if result else '无产出'}")
        return count
    finally:
        mind._reflecting = False
        if mind.pfc.has_pending_tasks():
            asyncio.create_task(
                mind.try_execute_mind(),
                name="agent.mind.post_reflect",
            )


async def execute_remember(mind: Mind, decision: Decision) -> None:
    """将决策内容存入语义记忆——与 memorize 工具同一套去重裁决管线。

    save honesty：裁决结果写入心跳日志（模型在后续自省/心跳中可见），
    重复跳过/合并更新都不是"新记一条"，避免静默吞没导致的谎报。
    """
    if not decision.content or not mind.memory_store:
        return
    from agent.memory import metrics
    store = mind.memory_store
    content = decision.content

    # 决策记忆接入标签网络：type:fact + 当前 scope 标签（联想/上下文加权生效）
    tags = ["type:fact"]
    try:
        from agent.memory.tools import _current_scope_tag
        scope_tag = _current_scope_tag()
        if scope_tag:
            tags.append(scope_tag)
    except Exception:
        pass

    try:
        # 第一级：规则判重（零成本快速拦截）
        if await store.has_similar_content(content):
            metrics.incr("write.dedup_rule_skip")
            log(f"AI 主动记忆裁决: 重复跳过（规则）: {content[:80]}", tag="思维")
            _hb_append(f"主动记忆裁决: 与既有记忆重复，未写入 - {content[:60]}")
            return

        # 第二级：LLM 语义裁决（store / skip / update / merge）
        from agent.memory.dedup import apply_update, gather_dedup_candidates, judge_write
        candidates = await gather_dedup_candidates(store, mind.embedder, content)
        verdict = await judge_write(content, candidates)
        action = verdict.get("action", "store")
        metrics.incr(f"write.dedup_llm_{action}")

        from agent.memory.embedding import wake_embedding_worker
        if action == "skip":
            log(f"AI 主动记忆裁决: 重复跳过（{verdict.get('reason', '语义重复')}）", tag="思维")
            _hb_append(f"主动记忆裁决: 已有等价记忆，未写入 - {content[:60]}")
            return
        if action == "update" and verdict.get("target_id"):
            updated = await apply_update(
                store, int(verdict["target_id"]),
                str(verdict.get("content") or content), tags,
            )
            if updated is not None:
                wake_embedding_worker()
                log(f"AI 主动记忆裁决: 合并更新到记忆 #{updated.id}", tag="思维")
                _hb_append(f"主动记忆裁决: 已合并更新既有记忆 #{updated.id} - {content[:60]}")
                return
            # 目标已不存在等异常：回退为正常写入
        if action == "merge" and verdict.get("target_ids"):
            merge_ids = [int(i) for i in verdict["target_ids"]]
            new_id = await store.merge_memories(merge_ids, str(verdict.get("content") or content))
            if new_id:
                wake_embedding_worker()
                log(f"AI 主动记忆裁决: 合并 {len(merge_ids)} 条为新记忆 #{new_id}", tag="思维")
                _hb_append(f"主动记忆裁决: 已合并为记忆 #{new_id} - {content[:60]}")
                return

        entry = MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content=content,
            importance=0.7,
            tags=tags,
        )
        mid = await store.add(entry)
        wake_embedding_worker()
        log(f"AI 主动记忆: 已记住 #{mid}: {content[:80]}", tag="思维")
        _hb_append(f"主动记忆裁决: 已记住 #{mid} - {content[:60]}")
    except Exception as exc:
        # 裁决管线故障不应杀死决策执行：记日志并放弃本次记忆（不静默谎报）
        log(f"AI 主动记忆失败: {exc}", "WARNING", tag="思维")
        _hb_append(f"主动记忆裁决: 写入失败（{exc}），未记住 - {content[:60]}")


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
    mind.pfc.add_temporary(
        {"role": "user", "content": proactive_prompt},
        scope=anything.entity_scope,
    )

    # adapter_key 按调用链显式传递（并行多 scope 回复时共享字段会串台）
    adapter_key = getattr(anything, "adapter_key", "") if anything else ""
    log(f"AI 主动消息: target={target}", tag="思维")
    await mind.reply(anything, adapter_key=adapter_key)


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
        "使用工具 create_goal、list_goals、update_goal 管理目标计划。\n"
        "与该目标相关的重要产出用 memorize 记录时打上 goal:{goal_id} 标签（替换为实际 goal_id），便于目标视角串联召回。\n"
        "若该规划是反复出现的周期性工作，用 create_task + set_task_schedule 沉淀为自动任务，"
        "不要每次手动重复规划。\n"
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
    scope_type, scope_adapter, base_id, session_id = parse_entity_scope(target)
    adapter_key = scope_adapter or default_key
    if scope_type == "group":
        group_id: Union[int, str] = base_id
        try:
            group_id = int(base_id)
        except ValueError:
            log("build_proactive_target 异常已忽略", "DEBUG")
        return MessageAssistantGroup(
            group_id=group_id, adapter_key=adapter_key, session_id=session_id
        )

    # 兼容裸 group_/user_ 前缀之外的旧调用形态
    raw = target.removeprefix("user_") if scope_type != "user" else base_id
    uid: Union[int, str] = raw
    try:
        uid = int(raw)
    except ValueError:
        log("build_proactive_target 异常已忽略", "DEBUG")
    return MessageAssistant(uid=uid, adapter_key=adapter_key, session_id=session_id)


def _build_reply_message(mind: Mind, scope: str, *, require_pending: bool) -> Optional[Everything]:
    """按 scope 消费待回复任务并构造带 session_id 的回复目标消息。"""
    if scope in mind._active_scopes:
        return None
    scope_type, scope_adapter, base_id, session_id = parse_entity_scope(scope)
    if not scope_type:
        return None
    adapter_key = scope_adapter or mind.pfc.get_adapter_key(scope)
    consumed = mind.pfc.consume_scope_task(scope)
    if require_pending and not consumed:
        return None
    target_id: Union[int, str] = base_id
    try:
        target_id = int(base_id)
    except ValueError:
        log("_build_reply_message 异常已忽略", "DEBUG")
    if scope_type == "group":
        return MessageAssistantGroup(group_id=target_id, adapter_key=adapter_key, session_id=session_id)
    return MessageAssistant(uid=target_id, adapter_key=adapter_key, session_id=session_id)


def resolve_reply_target(mind: Mind, target: str) -> Optional[Everything]:
    """根据 target 在已知路由中查找并消费对应任务。

    支持格式：user_123 / group_456 / user_123#chat_id / 纯 ID（自动补前缀）。
    判活前置：scope 已被占用时不消费队列条目（避免并行 scope 串台丢消息）。
    """
    if not target:
        return None

    if target.startswith(("user_", "group_")):
        return _build_reply_message(mind, target, require_pending=False)

    msg = _build_reply_message(mind, f"user_{target}", require_pending=True)
    if msg is not None:
        log(f"将 target '{target}' 补充 user_ 前缀匹配到 user_{target}", tag="思维")
        return msg
    msg = _build_reply_message(mind, f"group_{target}", require_pending=True)
    if msg is not None:
        log(f"将 target '{target}' 补充 group_ 前缀匹配到 group_{target}", tag="思维")
    return msg


async def pop_next_reply_target(mind: Mind) -> Optional[Everything]:
    """从 PFC 取出下一个待回复目标（保留 session_id 传播）。"""
    tasks = mind.pfc.peek_all_tasks()
    if not tasks:
        return None
    scope, _, _, _ = tasks[0]
    scope_type, scope_adapter, base_id, session_id = parse_entity_scope(scope)
    adapter_key = scope_adapter or mind.pfc.get_adapter_key(scope)
    if not scope_type:
        return None
    target_id: Union[int, str] = base_id
    try:
        target_id = int(base_id)
    except ValueError:
        log("pop_next_reply_target 异常已忽略", "DEBUG")
    if scope_type == "group":
        await mind.pfc.pop_group_task()
        return MessageAssistantGroup(group_id=target_id, adapter_key=adapter_key, session_id=session_id)
    await mind.pfc.pop_user_task()
    return MessageAssistant(uid=target_id, adapter_key=adapter_key, session_id=session_id)
