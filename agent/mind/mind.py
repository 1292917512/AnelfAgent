"""Mind — 思维核心。

统一处理所有消息输入，通过 LLM 实现自主决策和多轮原生工具调用。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, Union

from core.event_bus import (
    event_bus,
    EVENT_THINKING_SESSION_START,
    EVENT_THINKING_SESSION_END,
    EVENT_THINKING_PHASE_CHANGE,
    EVENT_THINKING_SITUATION,
    EVENT_THINKING_DECISION,
    EVENT_THINKING_CONTEXT_BUILD,
    EVENT_THINKING_LLM_START,
    EVENT_THINKING_LLM_END,
    EVENT_THINKING_INTROSPECTION,
)
from agent.llm import ChatModel, ChatResult, ImageContent, ToolCall
from agent.llm.llm_client import LLMClient
from agent.llm.llm_manager import LLMManager
from agent.messages import (
    CharacterAgent,
    Everything,
    EverythingGroup,
    MessageAssistant,
)
from agent.mind.autonomous import (
    Decision,
    DecisionType,
    DECISION_TOOLS,
    MindPhase,
    MindTask,
    PendingMessage,
    SituationContext,
    TaskType,
    build_meta_decision_messages,
    parse_decisions_from_tool_calls,
)
from agent.heartbeat import (
    load_recent as _hb_load_recent,
    append_entry as _hb_append,
    write_log as _hb_write,
)
from agent.heartbeat.engine import HeartbeatEngine
from agent.memory.embedder import Embedder
from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_store import MemoryStore
from agent.memory.notes import build_notes_system_message
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.mind.interrupt import (
    InterruptRegistry,
    is_interrupt_enabled,
    match_interrupt_keyword,
)
from agent.mind.background_tasks import BackgroundTaskRegistry
from agent.mind import tool_activation as _tool_activation  # noqa: F401  # 注册 activate_tool_group 延迟工具
from agent.mind.context_compressor import ContextCompressor, register_compressor
from agent.mind.message_schema import normalize_for_send, normalize_roles
from agent.mind import context_audit
from agent.mind.tools.media_pipeline import MediaPipeline
from agent.mind.cross_channel import (
    ChannelSnapshot,
    update_channel_snapshot as _cc_update_snapshot,
    collect_channel_info as _cc_collect_channel_info,
    recall_cross_channel as _cc_recall,
    build_cross_channel_narrative as _cc_build_narrative,
)
from agent.mind.tools.decision_executor import (
    execute_decision as _de_execute,
    execute_reply as _de_execute_reply,
    execute_reflect as _de_reflect,
    build_proactive_target as _de_build_proactive,
    resolve_reply_target as _de_resolve_target,
    pop_next_reply_target as _de_pop_target,
)
from agent.mind.tools.think_loop import (
    ThinkMode,
    reply_entry as _tl_reply,
    reply_loop as _tl_reply_loop,
    think_loop as _tl_think_loop,
    collect_pending_images as _tl_collect_images,
    apply_vision as _tl_apply_vision,
    save_base64_image as _tl_save_b64_image,
    complete_reply as _tl_complete_reply,
)
from agent.channel.manager import ChannelManager
from agent.storage.data_center import ConversationData, EverythingData
from agent.storage.storage_router import StorageDomain
from core.entity import EntityRegistry

from core.log import log

if TYPE_CHECKING:
    from agent.storage.storage_router import StorageRouter

_END_REPLY_TOOL_NAME = "end_reply"


from entities._sdk import deferred_tool, activate_group


@deferred_tool(
    name=_END_REPLY_TOOL_NAME,
    group="thinking", tags=["always"], source="mind.core",
    description=(
        "结束本轮操作。当你已完成所有操作，不再需要继续时调用此工具。"
        "可与 send_message 等工具同批调用以节约轮次；"
        "若同批或同轮存在失败工具，结束将不生效并反馈失败原因，修正后需重新调用。"
    ),
)
def _end_reply_tool(reason: str = "") -> str:
    """结束本轮操作。

    Args:
        reason: 可选的结束备注，仅日志记录，通常不需要填写
    """
    if reason:
        log(f"AI 结束操作: {reason}", tag="思维")
    return json.dumps({"ok": True, "action": "end_reply"}, ensure_ascii=False)


def _normalize_message_roles(messages: List[Dict]) -> List[Dict]:
    """发送边界角色归一（已收拢至 message_schema.normalize_roles，此处保留兼容别名）。

    头部连续 system 块（提示词分层）保持 system 供 Anthropic 前缀缓存复用；
    中途的 system 注入（纠正提示/执行反馈/执行上下文）转为 user 保留位置语义。
    """
    return normalize_roles(messages)


class Mind:
    """统一思维核心：自主决策、LLM 对话、工具编排。"""

    def __init__(
            self,
            *,
            char: CharacterAgent,
            llm: ChatModel,
            llm_manager: Optional[LLMManager] = None,
            channel_manager: ChannelManager,
            everything_data: EverythingData,
            conversation_data: ConversationData,
            prefrontal_cortex: Optional[PrefrontalCortex] = None,
            storage_router: Optional["StorageRouter"] = None,
            memory_store: Optional[MemoryStore] = None,
    ) -> None:
        self.char = char
        self.llm = llm
        self.llm_manager = llm_manager
        self.channel_manager = channel_manager
        self.everything_data = everything_data
        self.conversation_data = conversation_data
        self.storage_router = storage_router

        self.pfc = prefrontal_cortex or PrefrontalCortex(
            everything_data=everything_data,
            channel_manager=channel_manager,
            conversation_data=conversation_data,
        )
        self.heartbeat_engine = HeartbeatEngine(self)

        self.memory_store = memory_store
        self.embedder = Embedder()
        self.retriever: Optional[MemoryRetriever] = None
        if self.memory_store:
            self.retriever = MemoryRetriever(self.memory_store, self.embedder)
            if self.llm_manager:
                # rerank 需要 MediaClient（有 rerank 方法），而非 LLMClient
                rerank_client = self.llm_manager.get_media_client("rerank")
                if rerank_client:
                    self.retriever.set_rerank_client(rerank_client)

        self.media_pipeline = MediaPipeline()

        self._active_scopes: set[str] = set()
        self._reply_idle_event = asyncio.Event()
        self._reply_idle_event.set()
        self._reply_adapter_key: str = ""
        self.phase: MindPhase = MindPhase.IDLE
        self._last_reflect_time: float = 0.0
        self._session_llm_params: dict = {}

        self._reflecting: bool = False
        self._cycle_lock = asyncio.Lock()
        self._heartbeat_active: bool = False

        self._channel_snapshots: dict[str, ChannelSnapshot] = {}

        # scope 级中断信号（think_loop 每轮检查，用户可刹车失控回复）
        self.interrupts = InterruptRegistry()

        # scope 级后台任务注册表（等待意图挂起 / 完成通知新轮次的统一原语）
        self.background_tasks = BackgroundTaskRegistry()

        # 当前模型上下文窗口缓存（tokens，0 = 未知）
        self._cached_context_length: int = 0

        self._init_subsystems()
        self._register_core_tools()

    def _init_subsystems(self) -> None:
        """初始化思维子系统：上下文压缩 / 技能自学习 / 子代理委托。"""
        # 上下文压缩器（think_loop 每轮调用前检查溢出风险）
        self.compressor = ContextCompressor(self)
        register_compressor(self.compressor)

        # 技能自学习系统（存储/匹配/策展/后台评审）
        from agent.skills import SkillCurator, SkillMatcher, SkillReviewer, SkillStore
        self.skill_store = SkillStore()
        self.skill_matcher = SkillMatcher(self.skill_store, self.embedder)
        self.skill_curator = SkillCurator(self.skill_store)
        self.skill_reviewer = SkillReviewer(self, self.skill_store)
        if self._skills_enabled():
            self.skill_reviewer.start()

        # 子代理委托管理器（delegate_task 工具注册）
        from agent.delegation import DelegationManager, register_delegation_tools
        self.delegation_manager = DelegationManager(self)
        register_delegation_tools(self.delegation_manager)

    # ==================================================================
    # 初始化与配置
    # ==================================================================

    def _register_core_tools(self) -> None:
        """激活 Mind 核心层工具（end_reply 等）。"""
        if _END_REPLY_TOOL_NAME not in EntityRegistry.get_all_names():
            activate_group("thinking", "思维工具 - 对话流程控制与工具编排")
            log("思维工具已注册 (end_reply)", "DEBUG", tag="思维")

    def _resolve_adapter_key(self) -> str:
        """获取当前回复的 adapter_key。"""
        if self._reply_adapter_key:
            key = self._reply_adapter_key
        else:
            tasks = self.pfc.peek_all_tasks()
            key = self.pfc.get_adapter_key(tasks[0][0]) if tasks else ""
        if key:
            from agent.channel.context import bind_current_channel
            bind_current_channel(key)
        return key

    @property
    def tool_executor(self) -> Optional[Callable[[ToolCall], Awaitable[str]]]:
        return EntityRegistry.execute_tool_call

    def get_model_context_length(self) -> int:
        """获取当前模型的上下文窗口（tokens，带缓存；0 表示未知）。"""
        if self._cached_context_length > 0:
            return self._cached_context_length
        llm_client = self.llm if isinstance(self.llm, LLMClient) else None
        if llm_client is None:
            return 0
        max_ctx = 0
        try:
            info = LLMClient.get_model_info(llm_client.config.litellm_model)
            max_ctx = info.get("max_input_tokens") or info.get("max_tokens") or 0
        except Exception:
            max_ctx = 0
        if not max_ctx:
            max_ctx = llm_client.config.context_window or 0
        self._cached_context_length = max_ctx
        return max_ctx

    @staticmethod
    def _get_mind_config():
        from agent.config import get_mind_config
        return get_mind_config()

    def _set_phase(self, phase: MindPhase) -> None:
        prev = self.phase
        self.phase = phase
        if event_bus.has_listeners(EVENT_THINKING_PHASE_CHANGE):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(event_bus.emit(EVENT_THINKING_PHASE_CHANGE, {
                    "phase": phase.value, "prev_phase": prev.value,
                }))
            except RuntimeError:
                pass

    # ==================================================================
    # 消息感知入口
    # ==================================================================

    async def accept_feel(self, anything: Everything) -> None:
        """接收外部消息：到达即写入对话历史（保证时序）；仅在 trigger_mind 时加入 PFC 任务队列。"""
        # 思维循环运行中不覆盖当前阶段，避免 UI 阶段抖动
        if not self._active_scopes and not self._cycle_lock.locked():
            self._set_phase(MindPhase.ACCEPTING)
        preview = str(anything)[:80] if anything else ""
        log(f"感知输入: {preview}", tag="思维")
        await self.add_conversation(anything)

        # 中断指令优先：整条消息精确匹配中断关键词且该 scope 正在回复时，
        # 请求中断进行中的会话，而非作为新消息入队（用户意图是"刹车"而非对话）
        scope = anything.entity_scope
        if (
            is_interrupt_enabled()
            and scope in self._active_scopes
            and match_interrupt_keyword(anything.get_text_content() or "")
        ):
            self.interrupts.request(scope, reason="用户发送中断指令")
            log(f"识别到中断指令，已请求中断: {scope}", tag="中断")
            return

        should_enqueue = self.should_enqueue_external_message(anything)
        if (
            not should_enqueue
            and bool(getattr(anything, "trigger_mind", True))
            and self._reflecting
            and isinstance(anything, EverythingGroup)
            and not bool(getattr(anything, "to_me", False))
        ):
            log(f"反思中忽略非 @ 群消息: {anything.entity_scope}", "DEBUG", tag="思维")
        if should_enqueue:
            await self.pfc.add_task(anything)
            self._update_channel_snapshot(anything)

    def _update_channel_snapshot(self, anything: Everything) -> None:
        """记录频道活动快照，供跨频道感知使用。"""
        _cc_update_snapshot(self, anything)

    @property
    def is_reply(self) -> bool:
        return bool(self._active_scopes)

    @property
    def is_reflecting(self) -> bool:
        return self._reflecting

    def interrupt(self, scope: str, reason: str = "") -> bool:
        """请求中断指定 scope 的进行中会话（协作式，下一轮检查点生效）。

        Returns:
            是否成功登记（该 scope 无进行中会话时返回 False）。
        """
        if scope not in self._active_scopes:
            return False
        self.interrupts.request(scope, reason=reason or "外部请求")
        return True

    def should_enqueue_external_message(self, anything: Everything) -> bool:
        """判断外部消息是否应进入待回复队列。"""
        if not bool(getattr(anything, "trigger_mind", True)):
            return False
        text = (anything.get_text_content() or "").strip()
        has_media = bool(getattr(anything, "images", None) or getattr(anything, "media_segments", None))
        if not text and not has_media:
            log(f"忽略空消息入队: {anything.entity_scope}", "DEBUG", tag="思维")
            return False
        if (
            self._reflecting
            and isinstance(anything, EverythingGroup)
            and not bool(getattr(anything, "to_me", False))
        ):
            return False
        return True

    async def execute_mind(self, *, is_heartbeat: bool = False) -> None:
        """触发自主循环。通过 _cycle_lock 防止多个循环并发执行。"""
        async with self._cycle_lock:
            if is_heartbeat:
                self._heartbeat_active = True
            try:
                await self._autonomous_cycle(is_heartbeat=is_heartbeat)
            finally:
                self._heartbeat_active = False

    async def try_execute_mind(self) -> None:
        """尝试触发自主循环；已有循环在执行时直接跳过（用于 fire-and-forget 场景）。"""
        if self._cycle_lock.locked():
            return
        async with self._cycle_lock:
            await self._autonomous_cycle()

    async def execute_mind_for_scope(self, scope: str) -> None:
        """针对指定 scope 执行回复（带 scope 级锁）。"""
        if scope in self._active_scopes:
            return
        self._active_scopes.add(scope)
        self._reply_idle_event.clear()
        await event_bus.emit(EVENT_THINKING_SESSION_START, {
            "is_heartbeat": False, "scope": scope,
        })
        try:
            await _de_execute_reply(self, Decision(type=DecisionType.REPLY, target=scope, priority=10))
        finally:
            self.pfc.clear_dynamic_tools()
            self._active_scopes.discard(scope)
            if not self._active_scopes:
                self._reply_idle_event.set()
                self._set_phase(MindPhase.IDLE)
            await event_bus.emit(EVENT_THINKING_SESSION_END, {
                "reason": "scope_completed", "scope": scope,
            })

    # ==================================================================
    # 自主循环：态势收集 → 元决策 → 分发执行
    # ==================================================================

    _DEFERRED_DECISIONS = frozenset({
        DecisionType.REFLECT, DecisionType.REMEMBER,
        DecisionType.PLAN, DecisionType.SELF_TASK,
    })

    async def _autonomous_cycle(self, *, is_heartbeat: bool = False) -> None:
        """自主循环：收集态势 → AI 决策 → 分发执行 → 写入心跳日志。"""
        await event_bus.emit(EVENT_THINKING_SESSION_START, {
            "is_heartbeat": is_heartbeat,
        })

        if is_heartbeat:
            await self._run_heartbeat_tick()

        situation = await self._gather_situation(is_heartbeat=is_heartbeat)

        if not situation.has_pending and not is_heartbeat:
            await event_bus.emit(EVENT_THINKING_SESSION_END, {"reason": "no_pending"})
            return

        self._set_phase(MindPhase.DECIDING)
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
            decisions = await self._think_and_decide(situation)
        log(f"决策结果: {', '.join(d.type.value for d in decisions)}", tag="思维")

        await event_bus.emit(EVENT_THINKING_DECISION, {
            "decisions": [
                {"type": d.type.value, "target": d.target, "reason": d.reason, "priority": d.priority}
                for d in decisions
            ],
        })

        sorted_decisions = sorted(decisions, key=lambda d: d.priority, reverse=True)
        immediate = [d for d in sorted_decisions if d.type not in self._DEFERRED_DECISIONS]
        deferred = [d for d in sorted_decisions if d.type in self._DEFERRED_DECISIONS]

        # 反思进行中时跳过新的 REFLECT 决策，避免重入
        if self._reflecting:
            deferred = [d for d in deferred if d.type != DecisionType.REFLECT]

        # 后台决策立即并行启动（不等待 REPLY）
        for d in deferred:
            asyncio.create_task(
                self._safe_execute(d),
                name=f"agent.mind.bg.{d.type.value}",
            )

        # 即时决策并行执行（不同 scope 的 REPLY 可并行，execute_reply 内有 scope 级锁）
        if immediate:
            await asyncio.gather(*(self._safe_execute(d) for d in immediate))

        self.pfc.clear_general_tasks()

        exec_results: List[str] = [f"{d.type.value} 已执行" for d in immediate]
        if is_heartbeat or decisions:
            _hb_write(
                task_names=[d.type.value for d in sorted_decisions],
                exec_results=exec_results,
                pending_messages=len(situation.pending_messages),
                active_goals=len(situation.active_goals),
            )

        self.pfc.clear_dynamic_tools()

        await event_bus.emit(EVENT_THINKING_SESSION_END, {
            "reason": "completed",
            "decisions_executed": [d.type.value for d in immediate],
            "decisions_deferred": [d.type.value for d in deferred],
        })

        # SESSION_END 之后检查：如果还有待处理任务（如后台任务完成注入的），自动触发新一轮
        if self.pfc.has_pending_tasks():
            log("自主循环结束后仍有待处理任务，自动触发新一轮", tag="思维")
            asyncio.create_task(self.try_execute_mind())

    async def _run_heartbeat_tick(self) -> None:
        """后台执行心跳 tick，不阻塞主循环的消息处理。"""
        try:
            executed = await self.heartbeat_engine.tick()
            if executed:
                log(f"心跳任务完成: {', '.join(executed)}", tag="心跳")
        except Exception as exc:
            log(f"心跳 tick 异常: {exc}", "WARNING", tag="心跳")

    async def _safe_execute(self, decision: Decision) -> None:
        """安全执行决策，异常转为通用错误任务。"""
        try:
            await self._execute_decision(decision)
        except Exception as exc:
            log(f"决策执行异常 [{decision.type.value}]: {exc}", "WARNING", tag="思维")
            self.pfc.add_general_task(MindTask(
                task_type=TaskType.ERROR,
                preview=f"{decision.type.value} 执行失败: {exc}",
                metadata={"decision": decision.type.value, "error": str(exc)},
            ))

    async def _gather_situation(self, *, is_heartbeat: bool = False) -> SituationContext:
        """收集当前态势：待处理消息、记忆、通道、目标等（纯读取，无副作用）。"""
        pending: List[PendingMessage] = []
        for item in self.pfc.peek_all_tasks():
            scope, uid, group_id, preview = item
            adapter_key = self.pfc.get_adapter_key(scope)
            pending.append(PendingMessage(
                scope=scope, uid=uid, group_id=group_id,
                preview=preview, timestamp=time.time(),
                adapter_key=adapter_key,
            ))

        recent_mem_lines: list[str] = []
        if self.memory_store:
            recent = await self.memory_store.list_recent(limit=5)
            recent_mem_lines = [e.content[:100] for e in recent]

        connected_channels = self._collect_channel_info()
        active_goals = await self._collect_active_goals()
        general_tasks = self.pfc.peek_general_tasks()
        heartbeat_log = _hb_load_recent(3) if is_heartbeat else ""

        return SituationContext(
            pending_messages=pending,
            pending_tasks=general_tasks,
            pending_profile_count=len(self.pfc.pending_analysis),
            recent_memories=recent_mem_lines,
            last_reflect_time=self._last_reflect_time,
            current_time=time.time(),
            is_heartbeat=is_heartbeat,
            connected_channels=connected_channels,
            active_goals=active_goals,
            heartbeat_log=heartbeat_log,
        )

    def _collect_channel_info(self) -> List[str]:
        """收集频道连接信息摘要，包含连接状态细节。"""
        return _cc_collect_channel_info(self)

    async def _collect_active_goals(self) -> List[str]:
        """从 MemoryStore 收集活跃目标摘要。"""
        if not self.memory_store:
            return []
        from agent.planning.tools import collect_active_goals
        return await collect_active_goals(self.memory_store)


    # ==================================================================
    # 元决策
    # ==================================================================

    async def _think_and_decide(self, situation: SituationContext) -> List[Decision]:
        """让 AI 根据态势做元决策，通过 Tool Calling 返回决策列表。"""
        memory_ctx: List[Dict] = []
        if self.retriever:
            if situation.pending_messages:
                combined_preview = " ".join(pm.preview for pm in situation.pending_messages)
                first_pm = situation.pending_messages[0]
                entity_scope = ""
                if first_pm.group_id:
                    entity_scope = f"group_{first_pm.group_id}"
                elif first_pm.uid:
                    entity_scope = f"user_{first_pm.uid}"
                memory_ctx = await self.retriever.recall(
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
                    memory_ctx = await self.retriever.recall(
                        [{"role": "user", "content": query}],
                        top_k=3,
                    )

        messages = build_meta_decision_messages(
            self.char.get_personality_msg(), situation, memory_ctx,
        )
        try:
            mc = self._get_mind_config()
            opts = {"temperature": mc.meta_decision_temperature}
            tc = {"type": "function", "function": {"name": "decide"}}
            if self.llm_manager:
                primary = self.llm if isinstance(self.llm, LLMClient) else None
                result = await self.llm_manager.chat_with_fallback(
                    messages,
                    options=opts,
                    tools=DECISION_TOOLS,
                    tool_choice=tc,
                    client=primary,
                    max_retries=mc.llm_max_retries,
                    timeout=mc.llm_timeout,
                )
            else:
                result = await self.llm.chat(
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
            return self._fallback_decisions(situation)

    def _fallback_decisions(self, situation: SituationContext) -> List[Decision]:
        """元决策失败时的兜底：为每条待处理消息生成 REPLY 决策。"""
        decisions: List[Decision] = []
        for pm in situation.pending_messages:
            decisions.append(Decision(
                type=DecisionType.REPLY,
                target=pm.scope,
                priority=10,
            ))
        return decisions or [Decision(type=DecisionType.IDLE)]

    # ==================================================================
    # 决策分发与执行（委托 decision_executor 模块）
    # ==================================================================

    async def _execute_decision(self, decision: Decision) -> None:
        """根据决策类型分发执行。"""
        await _de_execute(self, decision)

    async def _execute_reflect(self, decision: Optional[Decision] = None, *, skip_interval: bool = False) -> int:
        """执行反思决策。"""
        return await _de_reflect(self, decision, skip_interval=skip_interval)

    def _build_proactive_target(self, target: str) -> Optional[Everything]:
        """根据 target 字符串构建主动消息目标对象。"""
        return _de_build_proactive(self, target)

    def _resolve_reply_target(self, target: str) -> Optional[Everything]:
        """根据 target 在已知路由中查找并消费对应任务。"""
        return _de_resolve_target(self, target)

    async def _pop_next_reply_target(self) -> Optional[Everything]:
        """从 PFC 取出下一个待回复目标。"""
        return await _de_pop_target(self)

    # ==================================================================
    # 多轮对话循环（委托 think_loop 模块）
    # ==================================================================

    async def reply(
            self,
            anything: Everything,
            images: Optional[List[ImageContent]] = None,
    ) -> None:
        """执行回复，异常时发送错误提示。"""
        await _tl_reply(self, anything, images)

    def _collect_pending_images(self) -> List[ImageContent]:
        return _tl_collect_images(self)

    @staticmethod
    def _save_base64_image(b64_data: str, mime_type: str = "image/jpeg") -> str:
        """将 base64 图片数据保存为文件，返回路径。"""
        return _tl_save_b64_image(b64_data, mime_type)

    async def _apply_vision(
            self,
            messages: List[Dict],
            images: List[ImageContent],
            anything: Optional[Everything] = None,
    ) -> List[Dict]:
        """将图片路径以 [media_file:image:path] 标签注入到对话历史。"""
        return await _tl_apply_vision(self, messages, images, anything)

    async def _reply_loop(
            self,
            anything: Everything,
            images: Optional[List[ImageContent]] = None,
    ) -> None:
        """多轮对话循环入口。"""
        await _tl_reply_loop(self, anything, images)

    async def _think_loop(
            self,
            mode: ThinkMode,
            tool_chain: List[Dict],
            execution_steps: List[str],
            start_time: float,
            safety_limit: int,
            collected_text: List[str],
            active_tools: List[Dict],
            anything: Optional[Everything] = None,
            base_messages: Optional[List[Dict]] = None,
            options: Optional[Dict] = None,
    ) -> None:
        """统一思维循环。"""
        await _tl_think_loop(
            self, mode, tool_chain, execution_steps, start_time,
            safety_limit, collected_text, active_tools,
            anything, base_messages, options,
        )

    @staticmethod
    def _resolve_target_id(anything: Optional[Everything]) -> str:
        """从消息对象中提取 target_id。"""
        if not anything:
            return ""
        if isinstance(anything, EverythingGroup) and anything.is_group_scope:
            return str(anything.group_id)
        return str(anything.uid) if anything.uid else ""

    async def _complete_reply(
            self,
            anything: Everything,
            content: str,
            iterations: int,
            *,
            error: bool = False,
            tool_chain: Optional[List[Dict]] = None,
    ) -> None:
        """记录 AI 最终输出，清理回复状态。"""
        await _tl_complete_reply(self, anything, content, iterations, error=error, tool_chain=tool_chain)

    # ==================================================================
    # LLM 调用与重试
    # ==================================================================

    async def _invoke_llm_unified(
            self,
            messages: List[Dict],
            tools: Optional[list[dict]],
            anything: Optional[Everything] = None,
            *,
            tool_choice: Optional[str] = None,
            options: Optional[Dict] = None,
    ) -> ChatResult:
        """统一 LLM 调用（带重试、模型回退和事件追踪）。"""
        # 发送边界统一规整（message_schema.normalize_for_send）：
        # 角色归一（头部提示词分层保持 system 供 Anthropic 前缀缓存，中途注入
        # 转 user 保留位置语义）+ 尾部 assistant prefill 修复
        messages = normalize_for_send(messages)
        model_name = self.llm.config.model if isinstance(self.llm, LLMClient) else "unknown"
        log(f"调用 LLM: {model_name} msgs={len(messages)}", tag="思维")
        tool_names = [t.get("function", {}).get("name", "") for t in (tools or [])]
        await event_bus.emit(EVENT_THINKING_LLM_START, {
            "model": model_name,
            "message_count": len(messages),
            "tool_count": len(tools) if tools else 0,
            "tool_names": tool_names[:20],
        })
        t0 = time.time()
        try:
            result = await self._llm_chat_with_retry(messages, tools, tool_choice=tool_choice, options=options)
        except Exception as exc:
            # 请求级审计：异常交换同样落盘（未开启时零开销）
            await context_audit.record_exchange(
                model=model_name, messages=messages, tools=tools,
                error=exc, duration_ms=(time.time() - t0) * 1000,
            )
            raise
        elapsed_ms = (time.time() - t0) * 1000
        # 请求级审计：规整后最终发送的 messages + 完整响应（未开启时零开销）
        await context_audit.record_exchange(
            model=result.model or model_name, messages=messages, tools=tools,
            result=result, duration_ms=elapsed_ms,
        )
        mc = self._get_mind_config()
        if mc.log_ai_output:
            if result.reasoning_content:
                log(f"AI 推理: {result.reasoning_content[:300]}", "DEBUG", tag="思维")
            if result.content:
                log(f"AI 输出: {result.content[:500]}", tag="思维")
        usage_data: Dict = {}
        max_ctx = 0
        if result.usage:
            usage_data = {
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "total_tokens": result.usage.total_tokens,
            }
            llm_client = self.llm if isinstance(self.llm, LLMClient) else None
            if llm_client:
                try:
                    info = LLMClient.get_model_info(llm_client.config.litellm_model)
                    max_ctx = info.get("max_input_tokens") or info.get("max_tokens") or 0
                except Exception:
                    max_ctx = 0
                if not max_ctx:
                    max_ctx = llm_client.config.context_window or 0
        usage_percent: Optional[float] = None
        if usage_data.get("total_tokens") and max_ctx > 0:
            usage_percent = round(usage_data["total_tokens"] / max_ctx * 100, 1)
        await event_bus.emit(EVENT_THINKING_LLM_END, {
            "model": result.model or model_name,
            "duration_ms": round(elapsed_ms),
            "has_content": bool(result.content),
            "content_preview": (result.content or "")[:200],
            "tool_calls": [tc.name for tc in result.tool_calls] if result.tool_calls else [],
            "has_reasoning": bool(result.reasoning_content),
            "reasoning_preview": (result.reasoning_content or "")[:800],
            "usage": usage_data,
            "usage_percent": usage_percent,
            "max_tokens": max_ctx,
        })
        return result

    async def _llm_chat_with_retry(
            self,
            messages: List[Dict],
            tools: Optional[list[dict]],
            *,
            tool_choice: Optional[str] = None,
            options: Optional[dict] = None,
    ) -> ChatResult:
        mc = self._get_mind_config()
        merged_options = dict(options or {})
        if mc.reasoning_effort and "reasoning_effort" not in merged_options:
            merged_options["reasoning_effort"] = mc.reasoning_effort
        if self._session_llm_params:
            merged_options.update(self._session_llm_params)

        model_override_id = merged_options.pop("_model_id", None)
        final_options = merged_options or None

        if self.llm_manager:
            if model_override_id:
                primary = self.llm_manager.get_client(model_override_id)
                if not primary:
                    from core.log import log
                    log(f"指定模型 '{model_override_id}' 不存在，回退到默认模型", "WARNING", tag="思维")
                    primary = self.llm if isinstance(self.llm, LLMClient) else None
            else:
                primary = self.llm if isinstance(self.llm, LLMClient) else None
            result = await self.llm_manager.chat_with_fallback(
                messages,
                options=final_options,
                tools=tools,
                tool_choice=tool_choice,
                client=primary,
                max_retries=mc.llm_max_retries,
                timeout=mc.llm_timeout,
            )
        else:
            result = await asyncio.wait_for(
                self.llm.chat(messages, options=final_options, tools=tools, tool_choice=tool_choice),
                timeout=mc.llm_timeout,
            )
        if result.content:
            from core.tags import rm_unless_text
            result.content = await rm_unless_text(result.content)
        return result

    async def llm_chat(self, request_messages: List[Dict], options: Optional[dict] = None) -> ChatResult:
        """简单 LLM 调用封装（无工具，纯文本生成）。"""
        return await self.llm.chat(request_messages, options=options)

    async def summarize_text(self, prompt: str) -> str:
        """用主模型生成摘要文本（供上下文压缩等内部流程使用）。"""
        result = await self.llm_chat([{"role": "user", "content": prompt}])
        return (result.content or "").strip()

    _REFLECT_ALWAYS_BLOCKED = frozenset({
        "list_channels", "schedule_reply",
    })
    _REFLECT_OUTPUT_TOOLS = frozenset({
        "send_message", "send_photo", "send_voice", "send_file",
    })

    @classmethod
    def _build_reflect_blocklist(cls, allow_output_tools: bool) -> Set[str]:
        """根据任务策略构建 reflect 阶段工具黑名单。"""
        blocked = set(cls._REFLECT_ALWAYS_BLOCKED)
        if not allow_output_tools:
            blocked.update(cls._REFLECT_OUTPUT_TOOLS)
        return blocked

    async def reflect(
            self,
            messages: List[Dict],
            *,
            adapter_key: str = "",
            max_iterations: int = 0,
            options: Optional[dict] = None,
            tool_tags: Optional[List[str]] = None,
            allow_output_tools: bool = False,
            extra_blocked_tools: Optional[Set[str]] = None,
    ) -> str:
        """内部任务循环：与对话共享统一思维流程，默认禁止对外发送消息。

        tool_tags 非空时按选择器加载工具集（替代默认的 "heartbeat" 标签）。
        选择器优先按 tag 匹配，同时兼容按 group 名匹配（如 mcp:web-fetch）。
        默认过滤 output 类工具（send_message/send_file 等），可按任务配置放开。
        extra_blocked_tools 可追加屏蔽特定工具（如子代理 leaf 角色屏蔽 delegate_task）。

        Returns:
            LLM 产出的文本内容（所有轮次输出的合并）。
        """
        mc = self._get_mind_config()
        safety_limit = max_iterations or mc.max_tool_iterations
        blocked_tools = self._build_reflect_blocklist(allow_output_tools)
        if extra_blocked_tools:
            blocked_tools = blocked_tools | set(extra_blocked_tools)

        base_tools = await self.pfc.get_active_tool_schemas(adapter_key, scope="reflect")
        active_tools = [
            s for s in base_tools
            if s.get("function", {}).get("name", "") not in blocked_tools
        ]

        extra_selectors = tool_tags if tool_tags else ["heartbeat"]
        existing_names = {s.get("function", {}).get("name", "") for s in active_tools}

        def _merge_extra_schemas(schemas: List[Dict]) -> None:
            for schema in schemas:
                name = schema.get("function", {}).get("name", "")
                if name and name not in existing_names and name not in blocked_tools:
                    active_tools.append(schema)
                    existing_names.add(name)

        for selector in extra_selectors:
            sel = (selector or "").strip()
            if not sel:
                continue
            # 1) 先按 tag 匹配（历史行为）
            _merge_extra_schemas(EntityRegistry.get_tool_schema_by_tags([sel]))
            # 2) 再按 group 匹配，支持 mcp:web-fetch 这类分组选择器
            _merge_extra_schemas(EntityRegistry.get_tool_schemas_by_group(sel))
            # 3) 兼容简写：web-fetch -> mcp:web-fetch
            if ":" not in sel:
                _merge_extra_schemas(EntityRegistry.get_tool_schemas_by_group(f"mcp:{sel}"))

        collected_text: List[str] = []
        execution_steps: List[str] = []
        output_policy = "放开外发" if allow_output_tools else "禁用外发"
        log(f"反思循环开始: {len(active_tools)} 个工具可用, 策略={output_policy}, 上限 {safety_limit} 轮", tag="思维")

        from agent.mind.think_session import think_session
        with think_session(self, "reflect", with_token=False):
            await self._think_loop(
                mode=ThinkMode.REFLECT,
                tool_chain=[],
                execution_steps=execution_steps,
                start_time=time.time(),
                safety_limit=safety_limit,
                collected_text=collected_text,
                active_tools=active_tools,
                anything=None,
                base_messages=messages,
                options=options,
            )

        total = "\n".join(collected_text)
        log(f"反思循环结束: 产出 {len(total)} 字", tag="思维")
        return total

    async def execute_task(self, task_name: str) -> Optional[str]:
        """按名称执行指定任务，返回任务产出文本或 None。"""
        log(f"执行任务: {task_name}", tag="思维")
        return await self.heartbeat_engine.run_task(task_name)

    # ==================================================================
    # 上下文构建（回忆 + 对话历史）
    # ==================================================================

    async def get_recollection(
            self,
            conversation_list: Optional[List[Dict]] = None,
            anything: Optional[Everything] = None,
    ) -> List[Dict]:
        """构建完整 LLM 上下文（人设 + 工作记忆 + 语义召回 + 对话历史）。

        Args:
            conversation_list: 外部传入的对话历史（Introspection 场景）。
                若为 None，内部自动从 DB 获取最新对话。
            anything: 消息对象，用于确定对话 scope。
        """
        # 若未传入对话历史，从 DB 实时获取
        if conversation_list is None:
            conversation_list = await self.get_conversation(anything) if anything else []

        # 语义记忆召回（用最新对话尾部作为查询上下文）
        memory_msgs: List[Dict] = []
        entity_scope = self._resolve_entity_scope(anything)
        tail = conversation_list[-10:] if len(conversation_list) > 10 else conversation_list
        if self.retriever:
            scope_source = conversation_list[-30:] if len(conversation_list) > 30 else conversation_list
            related_scopes = self._extract_related_scopes(scope_source, entity_scope)
            if anything:
                for s in self._extract_scopes_from_anything(anything, entity_scope):
                    if s not in related_scopes:
                        related_scopes.insert(0, s)
            memory_msgs = await self.retriever.recall(
                tail, entity_scope=entity_scope, related_scopes=related_scopes,
            )
            log(f"语义召回: {len(memory_msgs)} 条", tag="思维")

        # 跨频道语义召回 + 叙事面包屑
        current_adapter = getattr(anything, "adapter_key", "") or ""
        cross_recall_msgs, recalled_scopes = await self._recall_cross_channel(
            tail, current_adapter, entity_scope,
        )
        if cross_recall_msgs:
            memory_msgs.extend(cross_recall_msgs)
        narrative = self._build_cross_channel_narrative(
            current_adapter, entity_scope, recalled_scopes,
        )
        if narrative:
            memory_msgs.append({"role": "system", "content": narrative})

        # 技能匹配注入（volatile 层）：当前对话语义匹配到的经验技能
        await self._inject_matched_skills(memory_msgs, tail)

        # Prompt 分层构建（参考 hermes 三层架构）：
        # stable 层（人设 + 工具提示）对话内冻结复用，context 层（便签）低频重建，
        # volatile 层（语义召回等）每轮构建并置于其后，保证前缀缓存命中。
        models_summary = self._get_models_summary()
        stable_text, context_text, stable_hit, context_hit = await self._build_layered_prompts(
            anything, models_summary,
        )

        await event_bus.emit(EVENT_THINKING_CONTEXT_BUILD, {
            "memory_msgs_count": len(memory_msgs),
            "stable_cache_hit": stable_hit,
            "context_cache_hit": context_hit,
        })

        return await self.pfc.build_llm_context(
            stable_text=stable_text,
            context_text=context_text,
            memory_msgs=memory_msgs,
            anything=anything,
            adapter_key=getattr(anything, "adapter_key", ""),
            target_id=self._resolve_target_id(anything),
            models_summary=models_summary,
            anthropic_breakpoint=self._is_anthropic_model(),
        )

    async def _inject_matched_skills(self, memory_msgs: List[Dict], tail: List[Dict]) -> None:
        """将当前对话匹配到的技能注入 volatile 层（并记录使用次数）。"""
        if not self._skills_enabled() or not tail:
            return
        try:
            query_texts = [
                m.get("content", "") for m in tail
                if isinstance(m.get("content"), str)
            ]
            from core.config import get_config_int
            top_k = get_config_int("skills_match_top_k", 3)
            matched_skills = await self.skill_matcher.match(query_texts, top_k=top_k)
            if not matched_skills:
                return
            skill_lines = ["[相关技能] 以下经验可能适用于当前任务，可参考复用："]
            for skill, score in matched_skills:
                skill_lines.append(
                    f"## {skill.name} — {skill.description}\n{skill.content[:800]}"
                )
                self.skill_store.record_use(skill.name)
            memory_msgs.append({
                "role": "system",
                "content": "\n\n".join(skill_lines),
            })
            log(f"技能注入: {', '.join(s.name for s, _ in matched_skills)}", "DEBUG", tag="技能")
        except Exception as exc:
            log(f"技能匹配失败: {exc}", "DEBUG", tag="技能")

    async def _build_layered_prompts(
            self,
            anything: Optional[Everything],
            models_summary: str,
    ) -> Tuple[str, str, bool, bool]:
        """构建 stable/context 两层提示（经 PromptCacheManager 缓存复用）。

        Returns:
            (stable_text, context_text, stable_hit, context_hit)
        """
        from agent.mind.prompt_layers import (
            LAYER_CONTEXT, LAYER_STABLE, prompt_cache_manager,
        )

        scope = self._resolve_entity_scope(anything)

        persona_parts = [
            msg["content"] for msg in self.char.get_personality_msg() if msg.get("content")
        ]
        direct_vision = self._direct_vision()
        stable_hash = prompt_cache_manager.compute_hash(
            *persona_parts, self.pfc.stable_fingerprint(models_summary, direct_vision),
        )
        stable_text, stable_hit = prompt_cache_manager.get_or_build(
            scope, LAYER_STABLE, stable_hash,
            lambda: self.pfc.build_stable_layer(persona_parts, models_summary, direct_vision),
        )

        notes_parts = [
            msg["content"] for msg in build_notes_system_message() if msg.get("content")
        ]
        context_hash = prompt_cache_manager.compute_hash(*notes_parts)
        context_text, context_hit = prompt_cache_manager.get_or_build(
            scope, LAYER_CONTEXT, context_hash,
            lambda: "\n\n".join(notes_parts),
        )
        return stable_text, context_text, stable_hit, context_hit

    @staticmethod
    def _skills_enabled() -> bool:
        """技能系统总开关。"""
        from core.config import get_config_bool
        return get_config_bool("skills_enabled", True)

    def _is_anthropic_model(self) -> bool:
        """当前主模型是否为 Anthropic（决定是否注入 cache_control 断点）。"""
        from agent.mind.prompt_layers import is_anthropic_breakpoint_enabled
        if not is_anthropic_breakpoint_enabled():
            return False
        llm_client = self.llm if isinstance(self.llm, LLMClient) else None
        if llm_client is None:
            return False
        model = (llm_client.config.litellm_model or "").lower()
        api_type = (getattr(llm_client.config, "api_type", "") or "").lower()
        return "anthropic" in model or "claude" in model or api_type == "anthropic"

    def _direct_vision(self) -> bool:
        """当前主模型是否支持视觉（决定图片直传与媒体规则文案）。"""
        llm_client = self.llm if isinstance(self.llm, LLMClient) else None
        return bool(llm_client and llm_client.config.supports_vision)

    async def get_conversation(self, anything: Everything) -> List[Dict]:
        """从 DB 获取指定对象的对话历史。"""
        return await self.conversation_data.get_conversation_record_by_everything(anything)

    async def add_conversation(self, anything: Everything) -> None:
        """将消息写入对话历史。"""
        await self.conversation_data.add_conversation_record_by_everything(anything)

    async def _add_system_context(self, anything: Everything, content: str, role: str = "system") -> None:
        """向对话存储追加一条系统上下文消息。

        Args:
            role: 存储角色（主流 OpenAI 格式）。系统上下文用 "system"，
                  AI 自身输出（如内心独白）用 "assistant"，用户消息用 "user"。
        """
        scope_type, scope_id = self._resolve_scope(anything)
        await self.conversation_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type=scope_type, scope_id=scope_id,
            role=role, content=content,
        )

    @staticmethod
    def _resolve_scope(anything: Everything) -> tuple[str, str]:
        """从 anything 解析 scope_type 和 scope_id。"""
        return anything.scope_type, anything.scope_id

    @staticmethod
    def _resolve_entity_scope(anything: Optional[Everything]) -> str:
        """从消息对象解析实体 scope（如 user_123 / group_456）。"""
        if not anything:
            return ""
        if isinstance(anything, EverythingGroup) and anything.is_group_scope:
            return f"group_{anything.group_id}"
        if anything.uid:
            return f"user_{anything.uid}"
        return ""

    _RELATED_UID_RE = re.compile(r"\[(?:uid|at_uid):([^\]]+)\]")

    def _extract_related_scopes(
        self, conversation_tail: List[Dict], primary_scope: str,
    ) -> List[str]:
        """从对话中提取涉及的用户 uid（发送者 [uid:] + @ 对象 [at_uid:]），构建画像加载列表。

        仅在群聊场景下有意义。
        """
        if not primary_scope.startswith("group_"):
            return []
        seen: set[str] = {primary_scope}
        scopes: List[str] = []
        for msg in conversation_tail:
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            for m in self._RELATED_UID_RE.finditer(content):
                uid = m.group(1)
                if uid == "all":
                    continue
                scope = f"user_{uid}"
                if scope not in seen:
                    seen.add(scope)
                    scopes.append(scope)
        return scopes

    def _extract_scopes_from_anything(
        self, anything: Everything, primary_scope: str,
    ) -> List[str]:
        """从当前消息对象提取发送者 uid 和 [at_uid:xxx] 中的 uid。"""
        seen: set[str] = {primary_scope}
        scopes: List[str] = []
        if anything.uid and anything.uid not in (0, "0"):
            scope = f"user_{anything.uid}"
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
        content = anything.get_text_content() if hasattr(anything, "get_text_content") else ""
        if content:
            for m in self._RELATED_UID_RE.finditer(content):
                uid = m.group(1)
                if uid == "all":
                    continue
                scope = f"user_{uid}"
                if scope not in seen:
                    seen.add(scope)
                    scopes.append(scope)
        return scopes

    # ==================================================================
    # 跨频道感知（委托 cross_channel 模块）
    # ==================================================================

    async def _recall_cross_channel(
        self,
        query_conversation: List[Dict],
        current_adapter_key: str,
        current_scope: str,
    ) -> Tuple[List[Dict], Set[str]]:
        """搜索其他频道的语义相关对话，返回 (注入消息列表, 已召回 scope 集合)。"""
        return await _cc_recall(self, query_conversation, current_adapter_key, current_scope)

    def _build_cross_channel_narrative(
        self,
        current_adapter_key: str,
        current_scope: str,
        already_recalled_scopes: Optional[Set[str]] = None,
    ) -> str:
        """生成跨频道近况叙述（已被语义召回覆盖的 scope 不重复出现）。"""
        return _cc_build_narrative(self, current_adapter_key, current_scope, already_recalled_scopes)

    def _get_models_summary(self) -> str:
        """生成可用模型摘要（供 PFC 工作记忆使用）。"""
        if not self.llm_manager:
            return ""
        summary = self.llm_manager.get_models_summary()
        if not summary:
            return ""
        return (
            "# 可用模型\n"
            f"{summary}\n"
            "不要编造工具不存在的功能或数据。"
        )

