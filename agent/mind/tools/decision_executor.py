"""决策执行器：各类决策的具体执行逻辑。

函数以 mind 实例为第一参数，由 Mind 方法委托调用。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Dict, List, Optional, Union

from agent.heartbeat.log import append_entry as _hb_append
from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.messages import (
    Everything,
    MessageAssistant,
    MessageAssistantGroup,
    build_entity_scope,
    parse_entity_scope,
)
from agent.mind.autonomous import Decision, DecisionType, MindPhase
from core.config import get_config_int, register_configs_safe
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind


# ------------------------------------------------------------------
# REFLECT 决策冷却配置
# ------------------------------------------------------------------

_REFLECT_CONFIGS = {
    "mind/reflect": {
        "reflection_cooldown_minutes": {
            "description": (
                "REFLECT 决策冷却：距上次反思执行不足该分钟数时拒绝登记/执行，"
                "防元决策对同一态势逐拍重复判定（0 = 关闭）"
            ),
            "default": 30,
            "min": 0,
            "max": 1440,
            "unit": "分钟",
        },
    },
}

register_configs_safe(_REFLECT_CONFIGS)


def _reflection_cooldown_minutes() -> int:
    return max(0, get_config_int("reflection_cooldown_minutes", 30))


def _reflection_cooldown_state(mind: Mind) -> tuple[bool, float]:
    """冷却判定：距上次反思执行（mind.reflect 入口打点）是否已过冷却窗口。

    锚点为进程内内存态，重启清零后放行一次（对齐「本次启动以来尚未反思」
    哨兵语义）；返回 (是否放行, 距上次反思分钟数)。
    """
    minutes = _reflection_cooldown_minutes()
    last = float(getattr(mind, "_last_reflect_time", 0.0) or 0.0)
    if minutes <= 0 or last <= 0:
        return True, 0.0
    elapsed = (time.time() - last) / 60.0
    return elapsed >= minutes, elapsed


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
    # 激活时刻先于活跃登记：accept_feel 的中断判定以登记为准，
    # 登记后到达的中断请求 requested_at ≥ 激活时刻，启动清理不会误清
    mind._reply_activated_at[scope] = time.time()
    mind._active_scopes.add(scope)
    mind._reply_idle_event.clear()
    # 崩溃尾部修复：登记进行中回复检查点（正常/协作中断结束在 finally 清除；
    # 进程崩溃/SIGKILL 残留的行由启动 recover_interrupted 扫描并注入中断元消息）
    _checkpoint_registered = False
    try:
        await mind.conversation_data.router.sqlite.record_reply_checkpoint(
            scope, adapter_key=getattr(anything, "adapter_key", "") or "",
            phase="reply",
        )
        _checkpoint_registered = True
    except Exception as exc:
        log(f"回复检查点登记失败（不影响回复）: {exc}", "DEBUG", tag="思维")
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
        mind._reply_activated_at.pop(scope, None)
        if _checkpoint_registered:
            # 清除检查点（正常结束/协作中断/异常都算收束）。关停取消场景
            # 若清除未完成，残留行下次启动注入"被中断"提示——语义上同样成立
            try:
                await mind.conversation_data.router.sqlite.clear_reply_checkpoint(scope)
            except Exception as exc:
                log(f"回复检查点清除失败（已忽略）: {exc}", "DEBUG", tag="思维")
        if not mind._active_scopes:
            mind._reply_idle_event.set()
            mind._set_phase(MindPhase.IDLE)


async def execute_reflect(mind: Mind, decision: Optional[Decision] = None, *, skip_interval: bool = False) -> int:
    """执行反思决策：登记待执行标记，由 idle 调度在空闲心跳消费。

    配置了 idle 调度时不再立即执行（反思不打断对话节奏）：标记挂在心跳引擎，
    下个空闲 tick（活动刷新计数归零、无其他到期任务）时运行 self_reflection，
    反思原因经 executor extra_note 注入任务指令尾部。
    未配置 idle 调度时回退为立即执行（兼容旧部署）。

    两条路径共用入口冷却（reflection_cooldown_minutes）：元决策对同一态势
    会逐拍重复判定（2026-09「对话质量下滑」连爆事故），软提示（距上次反思
    <0.5h 避免重复）依赖 LLM 自觉、实测被无视，此处为机械门控。
    """
    reason = decision.reason if decision else ""
    engine = mind.heartbeat_engine

    cooldown_ok, elapsed = _reflection_cooldown_state(mind)
    if not cooldown_ok:
        cooldown = _reflection_cooldown_minutes()
        _hb_append(
            f"反思登记被冷却拒绝: 距上次反思 {elapsed:.0f} 分钟 < 冷却 {cooldown} 分钟，原因丢弃 - {reason[:40]}"
        )
        log(
            f"反思冷却中（{elapsed:.0f} 分钟 < {cooldown} 分钟），忽略登记: {reason[:60]}",
            tag="思维",
        )
        return 0

    if engine.has_idle_schedule():
        engine.mark_reflection_pending(reason)
        _hb_append(f"反思已登记，待空闲心跳执行: {reason[:60] or '元决策'}")
        log(f"反思延迟到空闲窗口（原因: {reason[:60]}）", tag="思维")
        return 0

    if mind._reflecting:
        # 同批/并发 REFLECT 只执行一个（批量决策的去重过滤发生在
        # 任一任务置位之前，这里是执行入口的最后防线）
        return 0
    mind._reflecting = True
    mind._set_phase(MindPhase.INTROSPECTING)

    try:
        result = await engine.run_task("self_reflection")
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
        anything = routable_target(mind, target)
    if not anything:
        log(f"PROACTIVE 目标不可路由，已放弃: {target}", "WARNING", tag="思维")
        _hb_append(f"主动消息放弃: 目标 '{target}' 无法解析为可路由会话 - {decision.content[:40]}")
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
                anything = routable_target(mind, decision.target)
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


def normalize_target_scope(mind: Mind, target: str) -> str:
    """把决策目标标识规范化为可路由 scope；无法唯一确定时返回空串。

    决策 target 由 LLM 自由生成，形态不定：规范 scope（user_qq:123 /
    group_qq:456，含 #session 后缀）直接采信；其余形态（旧格式 user_123、
    裸 id、频道前缀 qq:123 及其下划线变体）按已知会话（待回复队列 + 路由
    登记）唯一解析回填类型与频道前缀。无匹配或歧义（同 base id 命中多个
    会话）视为不可路由——由调用方回退队列消费或放弃，不拼出影子会话。
    """
    text = target.strip()
    if not text:
        return ""

    scope_type, adapter, base_id, session_id = parse_entity_scope(text)
    if scope_type and adapter:
        return build_entity_scope(scope_type, adapter, base_id, session_id)

    candidates: set[str] = set()
    for scope in mind.pfc.known_scopes():
        s_type, s_adapter, s_base, s_session = parse_entity_scope(scope)
        if not s_type:
            continue
        if scope_type:
            # 旧格式 user_123：类型明确，按 base id（含子会话）补频道前缀
            if (s_type, s_base) == (scope_type, base_id) and session_id in ("", s_session):
                candidates.add(scope)
        else:
            # 裸 id / qq:123 / qq_123：与已知会话的 base id 或 scope_id 全等命中
            scope_id = f"{s_adapter}:{s_base}"
            if text in (s_base, scope_id) or text.replace("_", ":", 1) == scope_id:
                candidates.add(scope)
    if len(candidates) == 1:
        return candidates.pop()
    return ""


def _coerce_base_id(base_id: str) -> Union[int, str]:
    """数字 base id 归一为 int（频道投递侧普遍按数字 uid 寻址）。"""
    try:
        return int(base_id)
    except ValueError:
        return base_id


def _build_target_message(scope: str) -> Everything:
    """按规范 scope 构造投递目标消息（uid/group_id 取 base id，携带频道与子会话）。"""
    scope_type, adapter, base_id, session_id = parse_entity_scope(scope)
    target_id = _coerce_base_id(base_id)
    if scope_type == "group":
        return MessageAssistantGroup(group_id=target_id, adapter_key=adapter, session_id=session_id)
    return MessageAssistant(uid=target_id, adapter_key=adapter, session_id=session_id)


def routable_target(mind: Mind, target: str) -> Optional[Everything]:
    """规范化决策目标为投递目标（不消费待回复队列）。

    供主动消息、工具操作结果投递等无待回复事实的场景使用。
    """
    scope = normalize_target_scope(mind, target)
    return _build_target_message(scope) if scope else None


def resolve_reply_target(mind: Mind, target: str) -> Optional[Everything]:
    """解析决策目标并精确消费其待回复条目。

    目标先规范化（见 normalize_target_scope）；不可路由或未命中待回复
    队列时返回 None，由调用方回退队列顺序消费（pop_next_reply_target）。
    """
    scope = normalize_target_scope(mind, target)
    if not scope or scope in mind._active_scopes:
        return None
    if not mind.pfc.consume_scope_task(scope):
        return None
    return _build_target_message(scope)


async def pop_next_reply_target(mind: Mind) -> Optional[Everything]:
    """从 PFC 取出下一个待回复目标（保留 session_id 传播）。

    跳过正在回复的 scope：盲弹队首会把"活跃会话的新消息"消费掉，
    而该消息本该由在途 think_loop 的轮内合并接管——弹走后周期不再
    触发，消息无人回复。
    """
    for scope, _, _, _ in mind.pfc.peek_all_tasks():
        if scope in mind._active_scopes:
            continue
        scope_type, scope_adapter, _base_id, _session_id = parse_entity_scope(scope)
        if not scope_type or not scope_adapter:
            # 毒丸条目：无法解析或缺少频道前缀的 scope 出站投递不了，留在
            # 队列会让自主循环以 0 退避无限空转——就地清除并告警
            log(f"清除无法路由的待处理条目: scope={scope!r}", "WARNING", tag="思维")
            mind.pfc.consume_scope_task(scope)
            continue
        # 按 scope 精确消费（含未读计数/预览清理），不依赖队首位置
        mind.pfc.consume_scope_task(scope)
        return _build_target_message(scope)
    return None
