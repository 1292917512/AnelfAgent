"""Mind — 思维核心。

统一处理所有消息输入，通过 LLM 实现自主决策和多轮原生工具调用。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from agent.channel.manager import ChannelManager
from agent.llm import ChatModel, ChatResult, ImageContent, ToolCall
from agent.llm.llm_client import LLMClient
from agent.llm.llm_manager import LLMManager
from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_store import MemoryStore
from agent.messages import (
    CharacterAgent,
    Everything,
)
from agent.mind import background_routing as _background_routing
from agent.mind import cycle as _cycle
from agent.mind import llm_invoker as _llm_invoker
from agent.mind import perception as _perception
from agent.mind import tool_activation as _tool_activation  # noqa: F401  # 注册 activate_tool_group 延迟工具
from agent.mind.autonomous import (
    DecisionType,
    MindPhase,
)
from agent.mind.background_tasks import BackgroundTaskRegistry
from agent.mind.cross_channel import (
    ChannelSnapshot,
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
from agent.mind.decisions import (
    execute_decision as _de_execute,
)
from agent.mind.decisions import (
    execute_reflect as _de_reflect,
)
from agent.mind.decisions import (
    pop_next_reply_target as _de_pop_target,
)
from agent.mind.decisions import (
    resolve_reply_target as _de_resolve_target,
)
from agent.mind.execution import reflection as _reflection
from agent.mind.execution.loop import think_loop as _tl_think_loop
from agent.mind.execution.media_pipeline import MediaPipeline
from agent.mind.execution.reply import reply_entry as _tl_reply
from agent.mind.execution.vision import collect_pending_images as _tl_collect_images
from agent.mind.interrupt import (
    InterruptRegistry,
)
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.mind.recall import coordinator as _recollection
from agent.mind.recall import scopes as _recall_scopes
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
    from agent.context.assembly import ContextAssembly
    from agent.context.compressor import ContextCompressor
    from agent.delegation.delegation_manager import DelegationManager
    from agent.heartbeat.engine import HeartbeatEngine
    from agent.memory.auto_capture import AutoCapturePipeline
    from agent.memory.embedding import Embedder
    from agent.skills import SkillCurator, SkillMatcher, SkillReviewer
    from agent.skills.skill_store import SkillStore
    from agent.storage.storage_router import StorageRouter
    from agent.workflow.engine import WorkflowEngine


class Mind:
    """统一思维核心：自主决策、LLM 对话、工具编排。"""

    heartbeat_engine: HeartbeatEngine
    compressor: ContextCompressor
    skill_matcher: SkillMatcher
    skill_curator: SkillCurator
    skill_reviewer: SkillReviewer
    delegation_manager: DelegationManager
    workflow_engine: WorkflowEngine
    auto_capture_pipeline: AutoCapturePipeline

    def __init__(
            self,
            *,
            char: CharacterAgent,
            llm: ChatModel,
            llm_manager: Optional[LLMManager] = None,
            channel_manager: ChannelManager,
            everything_data: EverythingData,
            conversation_data: ConversationData,
            prefrontal_cortex: PrefrontalCortex,
            context: ContextAssembly,
            skill_store: SkillStore,
            embedder: Embedder,
            retriever: Optional[MemoryRetriever],
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

        self.skill_store = skill_store
        self.pfc = prefrontal_cortex
        self.context = context
        self.memory_store = memory_store
        self.embedder = embedder
        self.retriever = retriever

        self.media_pipeline = MediaPipeline()

        self._active_scopes: set[str] = set()
        # scope → 本次回复的激活时刻（中断请求的新旧分界，见 InterruptRegistry.clear_before）
        self._reply_activated_at: Dict[str, float] = {}
        self._reply_idle_event = asyncio.Event()
        self._reply_idle_event.set()
        self.phase: MindPhase = MindPhase.IDLE
        self._last_reflect_time: float = 0.0
        # 最近一次真实思考活动时间戳（idle 空闲计数锚点，见 last_activity_ts）
        self._last_activity_ts: float = 0.0
        # 会话级 LLM 参数覆盖（model_control 工具经此下发 temperature 等，随会话存活）
        self.session_llm_params: dict = {}

        self._reflecting: bool = False
        # 进行中的 reflect 调用深度（并发子代理/任务/反思共用唯一入口，
        # phase 空闲判定的活跃事实源之一，asyncio 单线程计数无竞态）
        self._reflect_depth: int = 0
        self._cycle_lock = asyncio.Lock()
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
        # 超时提醒回调：任务超过预期时长仍在运行时向 AI 报告进度（去留由 AI 决策）
        self.background_tasks.set_alert_callback(self._on_bg_task_alert)

        # 实体推送中枢（[push:] 系统通知：短期记忆 + 入队唤醒 + 轮内弹窗）
        from agent.mind.push import PushHub
        self.push_hub = PushHub(self)
        try:
            self.push_hub.bind_loop(asyncio.get_running_loop())
        except RuntimeError:
            pass  # 非异步上下文（测试等）：跨线程推送退化为直接调用

        # 当前模型上下文窗口缓存（tokens，0 = 未知）
        self._cached_context_length: int = 0
        self._cached_max_output: int = 0
        self._cached_model_name: str = ""


    # 初始化与配置

    @property
    def tool_executor(self) -> Optional[Callable[[ToolCall], Awaitable[str]]]:
        return EntityRegistry.execute_tool_call

    get_model_context_length = _llm_invoker.get_model_context_length

    get_model_max_output = _llm_invoker.get_model_max_output

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

    def _reset_phase_if_idle(self) -> None:
        """无活跃思维工作时把阶段归零 IDLE（phase 的唯一复位点）。

        活跃事实源三元组：回复会话（_active_scopes）/ 进行中 reflect
        （_reflect_depth）/ 自主循环（_cycle_lock）——任一存活即不动，
        由该工作自己的完成边界再次调用本方法兜底归零。
        """
        if self.phase is MindPhase.IDLE:
            return
        if self._active_scopes or self._reflect_depth > 0 or self._cycle_lock.locked():
            return
        self._set_phase(MindPhase.IDLE)

    # 消息感知入口

    accept_feel = _perception.accept_feel

    _detect_skill_gesture = _perception._detect_skill_gesture

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

    _update_channel_snapshot = _cc_update_snapshot

    _on_bg_task_unclaimed = _background_routing._on_bg_task_unclaimed

    _on_bg_task_alert = _background_routing._on_bg_task_alert

    @property
    def is_reply(self) -> bool:
        return bool(self._active_scopes)

    @property
    def active_reply_scopes(self) -> frozenset[str]:
        """在途回复会话的 scope 集合（出站哨兵经 wiring 读取，单一事实源）。"""
        return frozenset(self._active_scopes)

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

    should_enqueue_external_message = _perception.should_enqueue_external_message

    async def execute_mind(self, *, is_heartbeat: bool = False) -> None:
        """触发自主循环。通过 _cycle_lock 防止多个循环并发执行。"""
        try:
            async with self._cycle_lock:
                await self._autonomous_cycle(is_heartbeat=is_heartbeat)
        finally:
            # 周期工作完成边界：锁已释放，无派生活跃（后台回复/反思）时归零；
            # 周期异常穿透（assistant 捕获续跑）同样收口，不留 DECIDING 卡死
            self._reset_phase_if_idle()

    async def try_execute_mind(self) -> None:
        """尝试触发自主循环；已有循环在执行时直接跳过（用于 fire-and-forget 场景）。"""
        if self._cycle_lock.locked():
            return
        try:
            async with self._cycle_lock:
                await self._autonomous_cycle()
        finally:
            self._reset_phase_if_idle()

    # 自主循环：态势收集 → 元决策 → 分发执行

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

    _cycle_body = _cycle._cycle_body

    _execute_decisions_and_finalize = _cycle._execute_decisions_and_finalize

    _clear_dynamic_tools_when_idle = _cycle._clear_dynamic_tools_when_idle

    _run_heartbeat_tick_bg = _cycle._run_heartbeat_tick_bg

    _safe_execute = _cycle._safe_execute

    _gather_situation = _cycle._gather_situation

    _collect_channel_info = _cc_collect_channel_info

    async def _collect_active_goals(self) -> List[str]:
        """收集活跃目标摘要（规划态势快照单一数据源，见 agent.planning.situation）。"""
        from agent.planning import situation as _plan_situation
        return await _plan_situation.active_goal_lines()


    # 元决策

    _think_and_decide = _cycle._think_and_decide

    _fallback_decisions = _cycle._fallback_decisions

    # 决策分发

    _execute_decision = _de_execute

    _execute_reflect = _de_reflect

    _resolve_reply_target = _de_resolve_target

    _pop_next_reply_target = _de_pop_target

    # 对话执行入口

    async def reply(
            self,
            anything: Everything,
            images: Optional[List[ImageContent]] = None,
            *,
            adapter_key: str = "",
    ) -> None:
        """执行回复，异常时发送错误提示。

        创建 completion 容器贯穿本次回复：think_loop 结束处写入完整消息链
        （base + tool_chain），complete_reply 据此在 EVENT_AFTER_REPLY 附带
        messages 快照，供 hooks_llm 钩子面以 transcript 档位消费。
        """
        self.note_activity()
        completion: Dict[str, Any] = {}
        try:
            await _tl_reply(self, anything, images, adapter_key=adapter_key,
                            completion=completion)
        finally:
            # 回复工作完成边界：execute_reply 路径此时尚未注销 scope（不收口），
            # PROACTIVE 直调路径无登记（无其他活跃即归零）
            self._reset_phase_if_idle()

    _collect_pending_images = _tl_collect_images

    _think_loop = _tl_think_loop

    # LLM 调用与重试

    _invoke_llm_unified = _llm_invoker._invoke_llm_unified

    _llm_chat_with_retry = _llm_invoker._llm_chat_with_retry

    _llm_chat_stream_once = _llm_invoker._llm_chat_stream_once

    async def llm_chat(self, request_messages: List[Dict], options: Optional[dict] = None) -> ChatResult:
        """简单 LLM 调用封装（无工具，纯文本生成）。"""
        return await self.llm.chat(request_messages, options=options)

    summarize_text = _llm_invoker.summarize_text

    async def prewarm_scope_cache(self, scope_type: str, scope_id: str) -> None:
        """请求预热会话折叠后的上下文前缀。"""
        from agent.context.prewarm import prewarm_scope_cache
        await prewarm_scope_cache(
            scope_type, scope_id, context=self.context, char=self.char,
            models_summary=self._get_models_summary(), direct_vision=self._direct_vision(),
            retriever=self.retriever, conversation_data=self.conversation_data, client=self.llm,
            get_tools=self.pfc.get_active_tool_schemas,
            timeout=self._get_mind_config().llm_timeout * 20,
        )

    reflect = _reflection.reflect

    async def execute_task(self, task_name: str) -> Optional[str]:
        """按名称执行指定任务，返回任务产出文本或 None。"""
        log(f"执行任务: {task_name}", tag="思维")
        return await self.heartbeat_engine.run_task(task_name)

    # 召回调度与会话数据访问

    get_recollection = _recollection.get_recollection

    _match_skills = _recollection._match_skills


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

    async def add_conversation(self, anything: Everything) -> bool:
        """写入对话历史，返回是否为新接收的消息。"""
        return await self.conversation_data.add_conversation_record_by_everything(anything)

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


    _extract_related_scopes = staticmethod(_recall_scopes._extract_related_scopes)

    _extract_scopes_from_anything = staticmethod(_recall_scopes._extract_scopes_from_anything)

    # 跨频道感知

    _recall_cross_channel = _cc_recall

    def _get_models_summary(self) -> str:
        """读取模型目录，交由上下文层呈现。"""
        from agent.context.preparation import format_models_summary
        if not self.llm_manager:
            return ""
        return format_models_summary(self.llm_manager.get_models_summary())
