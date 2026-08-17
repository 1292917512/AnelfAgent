"""Mind — 思维核心。

统一处理所有消息输入，通过 LLM 实现自主决策和多轮原生工具调用。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from agent.channel.manager import ChannelManager
from agent.heartbeat.engine import HeartbeatEngine
from agent.llm import ChatModel, ChatResult, ImageContent, ToolCall
from agent.llm.llm_client import LLMClient
from agent.llm.llm_manager import LLMManager
from agent.memory.embedding import get_embedder
from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_store import MemoryStore
from agent.messages import (
    CharacterAgent,
    Everything,
    EverythingGroup,
)
from agent.mind import context_audit  # noqa: F401  # 模块级符号：tests monkeypatch agent.mind.mind.context_audit
from agent.mind import cycle as _cycle
from agent.mind import llm_invoker as _llm_invoker
from agent.mind import recollection as _recollection
from agent.mind import tool_activation as _tool_activation  # noqa: F401  # 注册 activate_tool_group 延迟工具
from agent.mind.autonomous import (
    Decision,
    DecisionType,
    MindPhase,
    SituationContext,
)
from agent.mind.background_tasks import BackgroundTaskRegistry
from agent.mind.context_compressor import ContextCompressor, register_compressor
from agent.mind.cross_channel import (
    ChannelSnapshot,
)
from agent.mind.cross_channel import (
    build_cross_channel_narrative as _cc_build_narrative,
)
from agent.mind.cross_channel import (
    collect_channel_info as _cc_collect_channel_info,
)
from agent.mind.cross_channel import (
    recall_cross_channel as _cc_recall,
)
from agent.mind.cross_channel import (
    update_channel_snapshot as _cc_update_snapshot,
)
from agent.mind.interrupt import (
    InterruptRegistry,
    is_interrupt_enabled,
    match_interrupt_keyword,
)
from agent.mind.message_schema import (
    normalize_for_send,  # noqa: F401  # 模块级符号：tests monkeypatch agent.mind.mind.normalize_for_send
    normalize_roles,
)
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.mind.tools.decision_executor import (
    build_proactive_target as _de_build_proactive,
)
from agent.mind.tools.decision_executor import (
    execute_decision as _de_execute,
)
from agent.mind.tools.decision_executor import (
    execute_reflect as _de_reflect,
)
from agent.mind.tools.decision_executor import (
    pop_next_reply_target as _de_pop_target,
)
from agent.mind.tools.decision_executor import (
    resolve_reply_target as _de_resolve_target,
)
from agent.mind.tools.media_pipeline import MediaPipeline
from agent.mind.tools.think_loop import (
    ThinkMode,
)
from agent.mind.tools.think_loop import (
    apply_vision as _tl_apply_vision,
)
from agent.mind.tools.think_loop import (
    collect_pending_images as _tl_collect_images,
)
from agent.mind.tools.think_loop import (
    complete_reply as _tl_complete_reply,
)
from agent.mind.tools.think_loop import (
    reply_entry as _tl_reply,
)
from agent.mind.tools.think_loop import (
    reply_loop as _tl_reply_loop,
)
from agent.mind.tools.think_loop import (
    save_base64_image as _tl_save_b64_image,
)
from agent.mind.tools.think_loop import (
    think_loop as _tl_think_loop,
)
from agent.storage.data_center import ConversationData, EverythingData
from agent.storage.storage_router import StorageDomain
from core.entity import EntityRegistry
from core.event_bus import (
    EVENT_THINKING_PHASE_CHANGE,
    event_bus,
)
from core.log import log
from core.trace_session import thinking_session

if TYPE_CHECKING:
    from agent.storage.storage_router import StorageRouter

_END_REPLY_TOOL_NAME = "end_reply"


from entities._sdk import activate_group, deferred_tool


@deferred_tool(
    name=_END_REPLY_TOOL_NAME,
    group="thinking", tags=["always"], source="mind.core",
    description=(
        "结束本轮操作。任务已全部完成或无需继续时调用；参数留空即可（reason 仅内部日志，不发给用户）。"
        "回复用户请在结束前用 send_message，或直接输出最终回复正文。"
        "若同批或同轮存在失败工具，结束将不生效并反馈失败原因，修正后需重新调用。"
    ),
)
def _end_reply_tool(reason: str = "") -> str:
    """结束本轮操作。

    Args:
        reason: 可选备注，仅内部日志，不会发给用户，通常留空
    """
    if reason:
        log(f"AI 结束操作: {reason}", tag="思维")
    return json.dumps({"ok": True, "action": "end_reply"}, ensure_ascii=False)


def _normalize_message_roles(messages: List[Dict]) -> List[Dict]:
    """发送边界角色归一（委托 message_schema.normalize_roles）。

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
        self.embedder = get_embedder("text")
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
        self.phase: MindPhase = MindPhase.IDLE
        self._last_reflect_time: float = 0.0
        # 最近一次真实思考活动时间戳（idle 空闲计数锚点，见 last_activity_ts）
        self._last_activity_ts: float = 0.0
        # 会话级 LLM 参数覆盖（model_control 工具经此下发 temperature 等，随会话存活）
        self._session_llm_params: dict = {}

        self._reflecting: bool = False
        self._cycle_lock = asyncio.Lock()
        self._heartbeat_active: bool = False
        self._heartbeat_running: bool = False
        # 自动续轮退避计数（周期内无实质进展时递增，防紧凑重试烧 token）
        self._auto_cycle_retry: int = 0
        # 动态工具后台清理去重标记（见 _clear_dynamic_tools_when_idle）
        self._dynamic_tools_clear_pending: bool = False

        self._channel_snapshots: dict[str, ChannelSnapshot] = {}

        # scope 级中断信号（think_loop 每轮检查，用户可刹车失控回复）
        self.interrupts = InterruptRegistry()

        # 后台任务自动唤醒预算（防"完成→回复→又启后台任务"的自我激励循环；
        # 真人输入到达即重置，见 wake_budget 模块说明）
        from agent.mind.wake_budget import WakeBudgetTracker
        self.wake_budget = WakeBudgetTracker()

        # /name 技能手势待注入队列（scope -> [技能名]，recollection 消费后清空）
        self._pending_skill_gestures: Dict[str, List[str]] = {}

        # scope 级后台任务注册表（等待意图挂起 / 完成通知新轮次的统一原语）
        self.background_tasks = BackgroundTaskRegistry()
        try:
            self.background_tasks.bind_loop(asyncio.get_running_loop())
        except RuntimeError:
            pass  # 非异步上下文（测试等）：工作线程完成时退化为直接调用
        # 轮外完成回调：后台任务完成且无等待者时，排入回复队列触发新 REPLY
        self.background_tasks.set_unclaimed_callback(self._on_bg_task_unclaimed)

        # 实体推送中枢（[push:] 系统通知：短期记忆 + 入队唤醒 + 轮内弹窗）
        from agent.mind.push import PushHub
        self.push_hub = PushHub(self)
        try:
            self.push_hub.bind_loop(asyncio.get_running_loop())
        except RuntimeError:
            pass  # 非异步上下文（测试等）：跨线程推送退化为直接调用

        # 当前模型上下文窗口缓存（tokens，0 = 未知）
        self._cached_context_length: int = 0
        self._cached_model_name: str = ""

        # 文件型层 mtime 快检缓存（便签/索引文件未变时跳过 I/O）
        from agent.mind.prompt_layers import FileLayerCache
        self._file_cache = FileLayerCache()

        self._init_subsystems()
        self._register_core_tools()

    def _init_subsystems(self) -> None:
        """初始化思维子系统：上下文压缩 / 技能自学习 / 子代理委托。"""
        # 上下文压缩器（think_loop 每轮调用前检查溢出风险）
        self.compressor = ContextCompressor(self)
        register_compressor(self.compressor)

        # 技能自学习系统（存储/事实索引/匹配/策展/后台评审）
        from agent.skills import (
            SkillCurator,
            SkillMatcher,
            SkillReviewer,
            SkillStore,
            register_skill_tools,
        )
        self.skill_store = SkillStore()
        self.skill_matcher = SkillMatcher(self.skill_store, self.embedder)
        self.skill_curator = SkillCurator(self.skill_store, self.skill_matcher.index)
        self.skill_reviewer = SkillReviewer(self, self.skill_store, index=self.skill_matcher.index)
        if self._skills_enabled():
            self.skill_reviewer.start()
        # 重绑定工具依赖到本实例：bootstrap 先行注册用的是独立 matcher/store
        # （Mind 创建晚于工具注册节点），不重绑会产生两套向量缓存重复嵌入。
        # activate_group 二次调用为 no-op（延迟注册表已弹出），仅刷新依赖指针。
        register_skill_tools(self.skill_store, self.skill_matcher)

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
        if "switch_session" not in EntityRegistry.get_all_names():
            count = activate_group("session", "会话管理 — 多频道/多会话的发现与切换")
            if count:
                log(f"会话管理工具已注册 ({count} 个)", "DEBUG", tag="思维")

    def _resolve_adapter_key(self) -> str:
        """获取当前回复的 adapter_key（从待处理任务推断的回退路径）。"""
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
        """获取当前模型的上下文窗口（tokens，带缓存；0 表示未知，委托 llm_invoker 模块）。"""
        return _llm_invoker.get_model_context_length(self)

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
            # 真实外部事件：重置自动续轮退避 + 后台任务唤醒预算（真人参与
            # 后自动唤醒链路重新合法，对齐 dsh maxConsecutiveWakes 的重置语义）
            self._auto_cycle_retry = 0
            self.wake_budget.reset(scope)
            # /name 技能手势：真实外部消息正文以 /技能名 开头 → 确定性触发
            # （防伪造：仅此路径检测，工具结果/子代理输出里的 "/x" 不构成手势）
            self._detect_skill_gesture(anything, scope)
            await self.pfc.add_task(anything)
            self._update_channel_snapshot(anything)

    def _detect_skill_gesture(self, anything: Everything, scope: str) -> None:
        """检测 /name 技能手势并登记待注入（recollection 消费后清空）。"""
        try:
            from core.config import get_config_bool
            if not get_config_bool("skill_user_gesture_enabled", True):
                return
            from agent.skills.gesture import parse_skill_gesture
            from core.tags import strip_message_meta_tags
            text = strip_message_meta_tags(anything.get_text_content() or "").strip()
            name = parse_skill_gesture(text)
            if not name:
                return
            pending = self._pending_skill_gestures.setdefault(scope, [])
            if len(pending) < 3 and name not in pending:
                pending.append(name)
                log(f"识别到技能手势 /{name}，将在召回时确定性注入: {scope}", "DEBUG", tag="技能")
        except Exception:
            pass  # 手势检测失败不影响消息入队

    def _schedule_next_cycle(self, reason: str) -> None:
        """以指数退避调度下一轮自主循环（默认 0.5s 起、8s 封顶），避免紧凑重试。

        有待回复的用户消息时不退避——退避是为空闲自驱续轮防抖，
        用户消息已在排队等待，延迟直接损害对话体验。
        """
        from core.config import get_config_float
        base = get_config_float("auto_cycle_base_delay", 0.5)
        if not self.pfc.pending_user.is_empty() or not self.pfc.pending_group.is_empty():
            delay = 0.0
        else:
            delay = min(base * (2.0 ** self._auto_cycle_retry), 8.0)
        self._auto_cycle_retry += 1
        log(f"{reason}，{delay:.1f}s 后自动触发新一轮 (第{self._auto_cycle_retry}次续轮)", tag="思维")

        async def _later() -> None:
            await asyncio.sleep(delay)
            await self.try_execute_mind()

        asyncio.create_task(_later(), name="agent.mind.auto_cycle")

    def _update_channel_snapshot(self, anything: Everything) -> None:
        """记录频道活动快照，供跨频道感知使用。"""
        _cc_update_snapshot(self, anything)

    def _on_bg_task_unclaimed(self, scope: str, description: str, summary: str) -> None:
        """后台任务轮外完成回调：排入回复队列触发新 REPLY（与 delegation_manager 同路径）。

        唤醒预算：连续自动唤醒超过 background_wake_budget（默认 3，0=关闭）时
        不再触发新周期（防"完成→回复→又启后台任务→完成"的自我激励循环），
        完成信息仍写短期记忆，下次真人消息触发时模型可见，信息不丢。
        """
        from agent.mind.tools.scheduler import enqueue_scope_reply
        prompt = (
            f"[后台任务完成] {description}\n"
            f"结果：{summary[:800]}\n"
            "请根据结果继续处理（回复用户请调用 send_message，完成请调用 end_reply）。"
        )
        enqueue_scope_reply(
            self.pfc, scope,
            self.pfc.get_adapter_key(scope),
            f"后台任务完成: {description[:60]}",
            prompt,
        )
        if not self.wake_budget.allow(scope):
            self.wake_budget.note_suppressed(scope, description)
            return
        self.wake_budget.consume(scope)
        asyncio.create_task(self.try_execute_mind())

    @property
    def is_reply(self) -> bool:
        return bool(self._active_scopes)

    @property
    def is_reflecting(self) -> bool:
        return self._reflecting

    @property
    def last_activity_ts(self) -> float:
        """最近一次真实思考活动的时间戳（epoch 秒；0 = 进程启动后尚未思考）。

        由 note_activity 在 reply()/reflect() 入口刷新，供心跳引擎的 idle
        空闲计数消费；心跳元决策自身的 LLM 调用不经 reflect，不会刷新它。
        """
        return self._last_activity_ts

    def note_activity(self) -> None:
        """标记一次真实思考活动（回复/反思/任务执行/子代理均覆盖）。"""
        self._last_activity_ts = time.time()

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

    # ==================================================================
    # 自主循环：态势收集 → 元决策 → 分发执行
    # ==================================================================

    _DEFERRED_DECISIONS = frozenset({
        DecisionType.REFLECT, DecisionType.REMEMBER,
        DecisionType.PLAN, DecisionType.SELF_TASK,
    })

    async def _autonomous_cycle(self, *, is_heartbeat: bool = False) -> None:
        """自主循环：收集态势 → AI 决策 → 分发执行 → 写入心跳日志。

        会话生命周期由 thinking_session 管理：退出（含异常）保证发射 SESSION_END，
        会话内所有链路事件经 ContextVar 归属本会话。
        """
        async with thinking_session({"is_heartbeat": is_heartbeat}) as session:
            await self._cycle_body(session.end, is_heartbeat=is_heartbeat)

    async def _cycle_body(self, end_payload: Dict[str, Any], *, is_heartbeat: bool = False) -> None:
        """自主循环主体（委托 cycle 模块；会话生命周期由 _autonomous_cycle 管理）。"""
        await _cycle._cycle_body(self, end_payload, is_heartbeat=is_heartbeat)

    async def _execute_decisions_and_finalize(
            self,
            end_payload: Dict[str, Any],
            situation: SituationContext,
            decisions: List[Decision],
            *,
            is_heartbeat: bool = False,
    ) -> None:
        """执行决策列表并完成周期收尾（委托 cycle 模块，供 fast-path 和主路径复用）。"""
        await _cycle._execute_decisions_and_finalize(
            self, end_payload, situation, decisions, is_heartbeat=is_heartbeat,
        )

    async def _clear_dynamic_tools_when_idle(self) -> None:
        """等所有回复会话结束后再清动态工具（委托 cycle 模块，后台等待不阻塞周期）。"""
        await _cycle._clear_dynamic_tools_when_idle(self)

    async def _run_heartbeat_tick_bg(self) -> None:
        """后台执行心跳 tick，完成后触发新周期（委托 cycle 模块，不阻塞主循环）。"""
        await _cycle._run_heartbeat_tick_bg(self)

    async def _safe_execute(self, decision: Decision) -> bool:
        """安全执行决策，异常转为通用错误任务（委托 cycle 模块）。返回是否成功。"""
        return await _cycle._safe_execute(self, decision)

    async def _gather_situation(self, *, is_heartbeat: bool = False) -> SituationContext:
        """收集当前态势（委托 cycle 模块，纯读取无副作用）。"""
        return await _cycle._gather_situation(self, is_heartbeat=is_heartbeat)

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
        """让 AI 根据态势做元决策（委托 cycle 模块）。"""
        return await _cycle._think_and_decide(self, situation)

    def _fallback_decisions(self, situation: SituationContext) -> List[Decision]:
        """元决策失败时的兜底（委托 cycle 模块）。"""
        return _cycle._fallback_decisions(self, situation)

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
            *,
            adapter_key: str = "",
    ) -> None:
        """执行回复，异常时发送错误提示。"""
        self.note_activity()
        await _tl_reply(self, anything, images, adapter_key=adapter_key)

    def _collect_pending_images(self, scope: str = "") -> List[ImageContent]:
        return _tl_collect_images(self, scope=scope)

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
            *,
            adapter_key: str = "",
            blocked_tools: Optional[Set[str]] = None,
    ) -> None:
        """统一思维循环。"""
        await _tl_think_loop(
            self, mode, tool_chain, execution_steps, start_time,
            safety_limit, collected_text, active_tools,
            anything, base_messages, options,
            adapter_key=adapter_key, blocked_tools=blocked_tools,
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
            stream: bool = False,
            on_delta: Optional[Any] = None,
            purpose: str = "reply",
    ) -> ChatResult:
        """统一 LLM 调用（带重试、模型回退和事件追踪，委托 llm_invoker 模块）。

        purpose 标记调用用途（reply/reflect 等），缓存命中统计按用途分桶，
        避免无共享前缀的辅助调用污染主对话命中率口径。
        """
        return await _llm_invoker._invoke_llm_unified(
            self, messages, tools, anything,
            tool_choice=tool_choice, options=options, stream=stream, on_delta=on_delta,
            purpose=purpose,
        )

    def _merge_llm_options(self, options: Optional[dict]) -> dict:
        """合并 LLM 调用选项（委托 llm_invoker 模块）。"""
        return _llm_invoker._merge_llm_options(self, options)

    async def _llm_chat_with_retry(
            self,
            messages: List[Dict],
            tools: Optional[list[dict]],
            *,
            tool_choice: Optional[str] = None,
            options: Optional[dict] = None,
    ) -> ChatResult:
        """非流式 LLM 调用（带回退重试，委托 llm_invoker 模块）。"""
        return await _llm_invoker._llm_chat_with_retry(
            self, messages, tools, tool_choice=tool_choice, options=options,
        )

    async def _llm_chat_stream_once(
            self,
            messages: List[Dict],
            tools: Optional[list[dict]],
            *,
            tool_choice: Optional[str] = None,
            options: Optional[dict] = None,
            on_delta: Optional[Any] = None,
    ) -> ChatResult:
        """主客户端单次流式调用（委托 llm_invoker 模块）。"""
        return await _llm_invoker._llm_chat_stream_once(
            self, messages, tools, tool_choice=tool_choice, options=options, on_delta=on_delta,
        )

    async def llm_chat(self, request_messages: List[Dict], options: Optional[dict] = None) -> ChatResult:
        """简单 LLM 调用封装（无工具，纯文本生成）。"""
        return await self.llm.chat(request_messages, options=options)

    async def summarize_text(self, prompt: str) -> str:
        """生成摘要文本（供对话折叠/上下文压缩等内部流程使用）。

        优先经 chat_with_fallback 调用：主模型超时/故障时自动回退其他可用
        chat 模型，避免内部小任务被单点模型拖死（摘要输入小、对时效敏感）。
        """
        messages = [{"role": "user", "content": prompt}]
        if self.llm_manager is not None:
            result = await self.llm_manager.chat_with_fallback(
                messages, max_retries=1, timeout=180.0,
            )
        else:
            result = await self.llm_chat(messages)
        return (result.content or "").strip()

    async def prewarm_scope_cache(self, scope_type: str, scope_id: str) -> None:
        """折叠后预热：用该会话的新前缀发一次 1-token 轻调用写热缓存。

        折叠重写摘要块 = 缓存前缀断点；预热把"断点后首次全价预读"转移到
        空闲后台，用户发起的下一轮真实调用直接命中。经 build_llm_context
        走同一组装管线（tools + stable/summary/conversation/context 便签），
        召回/画像等每会话动态区不参与（它们在真实调用的前缀匹配范围之外）。
        预热成本即下一轮真实调用本应付的全价预读，净零额外开销；
        失败静默（下次真实调用自然回温）。
        """
        from agent.messages import Everything, EverythingGroup

        adapter, _, base_id = scope_id.partition(":")
        anything: Everything
        if scope_type == "group":
            anything = EverythingGroup(group_id=base_id, adapter_key=adapter)
        else:
            anything = Everything(uid=base_id, adapter_key=adapter)
        entity_scope = self._resolve_entity_scope(anything)

        # 永久块（真实流程经召回提升进 context 层；预热不跑检索，直接取 pins 组装）
        permanent_text = ""
        if self.retriever is not None:
            pinned = await self.retriever._load_permanent_pins([])
            pin_msgs = await self.retriever._format_unified_results(pinned) if pinned else []
            permanent_text = str(pin_msgs[0]["content"]) if pin_msgs else ""

        models_summary = self._get_models_summary()
        layered = await self._build_layered_prompts(anything, models_summary, permanent_text)
        persona_text, tools_text, context_text = layered[0], layered[1], layered[2]
        summary_row = await self.conversation_data.get_conversation_summary(anything)
        conversation = await self.conversation_data.get_conversation_record_by_everything(anything)
        messages = await self.pfc.build_llm_context(
            persona_text=persona_text,
            tools_text=tools_text,
            context_text=context_text,
            memory_msgs=[],
            anything=anything,
            adapter_key=adapter,
            summary_row=summary_row,
            prefetched_conversation=conversation,
            scope=entity_scope,
        )
        llm_client = self.llm if isinstance(self.llm, LLMClient) else None
        if llm_client is None:
            return
        from agent.llm.prompt_cache import decorate_messages, is_anthropic_wire
        messages = decorate_messages(messages, anthropic=is_anthropic_wire(
            llm_client.config.litellm_model or "",
            getattr(llm_client.config, "api_type", "") or "",
        ))
        # 发送边界统一规整：与 _invoke_llm_unified 一致（decorate 之后 normalize），
        # 剥离 _layer 标签——缺此步会把内部分类标签泄露给供应商（潜在 400）
        messages = normalize_for_send(messages)
        # tools 数组是请求最前段，必须与真实调用一致（缺它前缀从第 0 字节就不匹配）
        tools = await self.pfc.get_active_tool_schemas(adapter, scope=entity_scope)
        log(
            f"缓存预热 [{entity_scope}]: model={llm_client.config.model} "
            f"msgs={len(messages)} tools={len(tools or [])}",
            "DEBUG", tag="缓存",
        )
        async for _delta in llm_client.chat_stream(
            messages, options={"max_tokens": 1}, tools=tools or None,
        ):
            pass

    # reflect（子代理/心跳）不得操作频道调度类工具。
    # present_plan/update_goal 已放行：tracker 对非用户 scope（reflect 等）
    # 只持久化 goal、不发射前端事件（事件泄漏在 tracker 层根治），
    # 心跳任务因此可以正常创建/推进自己的计划。
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
        self.note_activity()
        mc = self._get_mind_config()
        safety_limit = max_iterations or mc.max_tool_iterations
        blocked_tools = self._build_reflect_blocklist(allow_output_tools)
        if extra_blocked_tools:
            blocked_tools = blocked_tools | set(extra_blocked_tools)

        # 每次反思会话使用唯一 scope：并行子代理/心跳 reflect 的 plan 与
        # 工具激活状态按 scope 隔离，共享字面量会互相串扰
        reflect_scope = f"reflect:{uuid.uuid4().hex[:8]}"
        base_tools = await self.pfc.get_active_tool_schemas(adapter_key, scope=reflect_scope)
        # 可见性与权限分离：schema 数组全量保留（与回复调用共享同一冻结
        # 前缀，tools 是 prompt 最大头，裁剪会在数组早期位置断裂缓存）；
        # 禁用工具由 think_loop 执行侧拦截（合成错误结果，模型自我纠正）
        active_tools = list(base_tools)

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
            # 2) 再按 group 匹配（含 mcp:web-fetch 分组选择器与 web-fetch 简写）
            groups = [sel] if ":" in sel else [sel, f"mcp:{sel}"]
            for group in groups:
                _merge_extra_schemas(EntityRegistry.get_tool_schemas_by_group(group))

        collected_text: List[str] = []
        execution_steps: List[str] = []
        output_policy = "放开外发" if allow_output_tools else "禁用外发"
        log(f"反思循环开始: {len(active_tools)} 个工具可用, 策略={output_policy}, 上限 {safety_limit} 轮", tag="思维")

        from agent.mind.think_session import think_session
        with think_session(self, reflect_scope, with_token=False):
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
                blocked_tools=blocked_tools,
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
            *,
            lean: bool = False,
    ) -> List[Dict]:
        """构建完整 LLM 上下文（人设 + 工作记忆 + 语义召回 + 对话历史，委托 recollection 模块）。

        lean=True 为任务精简模式：只保留人设 + 工具 + 永久记忆，
        不做环境注入（便签/召回/状态），任务按规则经工具按需取数。
        """
        return await _recollection.get_recollection(self, conversation_list, anything, lean=lean)

    async def _match_skills(
            self,
            tail: List[Dict],
            *,
            query_vec: Optional[List[float]] = None,
            scope: str = "",
    ) -> List[Dict]:
        """匹配当前对话相关的技能（委托 recollection 模块）。"""
        return await _recollection._match_skills(self, tail, query_vec=query_vec, scope=scope)

    async def _build_layered_prompts(
            self,
            anything: Optional[Everything],
            models_summary: str,
            permanent_text: str = "",
            *,
            lean: bool = False,
    ) -> Tuple[str, str, str, bool, bool, bool, str]:
        """构建 stable 人设块/工具块/context 层三段提示（委托 recollection 模块）。

        permanent_text：永久记忆置顶块（召回路径剥离，字节稳定），并入 context 层
        走内容寻址缓存。返回追加 status_text：心跳维护的记忆状态区块（尾部动态区
        独立注入，不入 context 层，避免计数更新击穿缓存前缀）。
        lean 为任务精简模式：context 层只留永久记忆块，status_text 留空。
        """
        return await _recollection._build_layered_prompts(
            self, anything, models_summary, permanent_text, lean=lean,
        )

    @staticmethod
    def _apply_memory_budget(msgs: List[Dict]) -> List[Dict]:
        """按预算截断 memory 层（委托 recollection 模块）。"""
        return _recollection._apply_memory_budget(msgs)

    @staticmethod
    def _skills_enabled() -> bool:
        """技能系统总开关。"""
        from core.config import get_config_bool
        return get_config_bool("skills_enabled", True)

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
                  AI 自身输出用 "assistant"，用户消息用 "user"。
        """
        scope_type, scope_id = self._resolve_scope(anything)
        await self.conversation_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type=scope_type, scope_id=scope_id,
            role=role, content=content,
            adapter_key=getattr(anything, "adapter_key", "") or "",
        )

    @staticmethod
    def _resolve_scope(anything: Everything) -> tuple[str, str]:
        """从 anything 解析 scope_type 和 scope_id。"""
        return anything.scope_type, anything.scope_id

    @staticmethod
    def _resolve_entity_scope(anything: Optional[Everything]) -> str:
        """从消息对象解析实体 scope（如 user_123 / group_456 / user_123#chat_id）。

        使用 ``everything.entity_scope`` 属性（支持 session_id 多会话后缀），
        而不是手工拼接——保证与 webui 多 chat_id 隔离一致。
        """
        if not anything:
            return ""
        return anything.entity_scope

    _RELATED_UID_RE = re.compile(r"\[(?:uid|at_uid):([^\]]+)\]")

    def _extract_related_scopes(
        self, conversation_tail: List[Dict], primary_scope: str,
    ) -> List[str]:
        """从对话中提取涉及的用户 uid（委托 recollection 模块）。"""
        return _recollection._extract_related_scopes(self, conversation_tail, primary_scope)

    def _extract_scopes_from_anything(
        self, anything: Everything, primary_scope: str,
    ) -> List[str]:
        """从当前消息对象提取发送者 uid（委托 recollection 模块）。"""
        return _recollection._extract_scopes_from_anything(self, anything, primary_scope)

    # ==================================================================
    # 跨频道感知（委托 cross_channel 模块）
    # ==================================================================

    async def _recall_cross_channel(
        self,
        query_conversation: List[Dict],
        current_adapter_key: str,
        current_scope: str,
        query_vec: Optional[List[float]] = None,
    ) -> Tuple[List[Dict], Set[str]]:
        """搜索其他频道的语义相关对话，返回 (注入消息列表, 已召回 scope 集合)。"""
        return await _cc_recall(
            self, query_conversation, current_adapter_key, current_scope, query_vec=query_vec,
        )

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

