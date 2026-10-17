"""Mind — 思维核心。

统一处理所有消息输入，通过 LLM 实现自主决策和多轮原生工具调用。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from functools import partial
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Set

from agent.channel.manager import ChannelManager
from agent.context.observation import emit_context_summary
from agent.context.preparation import model_context
from agent.llm import ChatModel, ImageContent, ToolCall
from agent.llm.llm_manager import LLMManager
from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_store import MemoryStore
from agent.messages import (
    CharacterAgent,
    Everything,
    EverythingGroup,
)
from agent.mind import tool_activation as _tool_activation  # noqa: F401  # 注册 activate_tool_group 延迟工具
from agent.mind.autonomous import MindPhase
from agent.mind.background_routing import on_task_alert, on_task_unclaimed
from agent.mind.background_tasks import BackgroundTaskRegistry
from agent.mind.cross_channel import (
    ChannelSnapshot,
    update_channel_snapshot,
)
from agent.mind.cycle import run_cycle
from agent.mind.execution.loop import think_loop
from agent.mind.execution.media_pipeline import MediaPipeline
from agent.mind.execution.reply import reply_entry
from agent.mind.execution.state import ThinkMode
from agent.mind.interrupt import (
    InterruptRegistry,
    is_interrupt_enabled,
    match_interrupt_keyword,
)
from agent.mind.llm_invoker import ModelInvoker
from agent.mind.mind_tools import build_reflect_blocklist, select_reflect_tools
from agent.mind.perception import detect_skill_gesture, should_enqueue_external_message
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.mind.recall.coordinator import prepare_recollection
from agent.storage.data_center import ConversationData, EverythingData
from agent.storage.storage_router import StorageDomain
from core.activity import EVENT_ACTIVITY_CONTEXT
from core.entity import EntityRegistry
from core.event_bus import (
    EVENT_THINKING_PHASE_CHANGE,
    event_bus,
)
from core.log import log
from core.trace_session import thinking_session

if TYPE_CHECKING:
    from agent.config import MindConfig
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
            config: MindConfig,
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
        self.config = config
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
        self.model_invoker = ModelInvoker(self)

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
        # 动态工具后台清理去重标记
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
        self.background_tasks.set_unclaimed_callback(partial(on_task_unclaimed, self))
        # 超时提醒回调：任务超过预期时长仍在运行时向 AI 报告进度（去留由 AI 决策）
        self.background_tasks.set_alert_callback(partial(on_task_alert, self))

        # 实体推送中枢（[push:] 系统通知：短期记忆 + 入队唤醒 + 轮内弹窗）
        from agent.mind.push import PushHub
        self.push_hub = PushHub(self)
        try:
            self.push_hub.bind_loop(asyncio.get_running_loop())
        except RuntimeError:
            pass  # 非异步上下文（测试等）：跨线程推送退化为直接调用

    # 初始化与配置

    @property
    def tool_executor(self) -> Optional[Callable[[ToolCall], Awaitable[str]]]:
        return EntityRegistry.execute_tool_call

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

    async def accept_feel(self, anything: Everything) -> None:
        """接收外部消息：到达即写入对话历史（保证时序）；仅在 trigger_mind 时加入 PFC 任务队列。"""
        try:
            # 思维工作运行中不覆盖当前阶段，避免 UI 阶段抖动
            if (not self._active_scopes and not self._cycle_lock.locked()
                    and self._reflect_depth == 0):
                self._set_phase(MindPhase.ACCEPTING)
            preview = str(anything)[:80] if anything else ""
            log(f"感知输入: {preview}", tag="思维")
            if not await self.add_conversation(anything):
                return
            update_channel_snapshot(self, anything)

            # 用户话题指令抽取（ban-topic）：所有用户消息必经点，纯正则零 LLM，
            # 失败不影响消息主流程
            try:
                from agent.messages import CharType
                if getattr(anything, "char_type", None) in (None, CharType.USER):
                    from agent.memory.user_directives import observe_message
                    await observe_message(
                        anything.entity_scope, anything.get_text_content() or "",
                    )
            except Exception:
                pass

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

            should_enqueue = should_enqueue_external_message(self, anything)
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
                # 后自动唤醒链路重新合法）
                self._auto_cycle_retry = 0
                self.wake_budget.reset(scope)
                # /name 技能手势：真实外部消息正文以 /技能名 开头 → 确定性触发
                # （防伪造：仅此路径检测，工具结果/子代理输出里的 "/x" 不构成手势）
                detect_skill_gesture(self, anything, scope)
                await self.pfc.add_task(anything)
        finally:
            # 感知处理完成边界：未入队/中断/异常路径不会有后续周期覆盖
            # ACCEPTING；已入队时周期未抢锁则先归零、周期启动再覆盖
            self._reset_phase_if_idle()

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

    async def _autonomous_cycle(self, *, is_heartbeat: bool = False) -> None:
        """自主循环：收集态势 → AI 决策 → 分发执行 → 写入心跳日志。

        会话生命周期由 thinking_session 管理：退出（含异常）保证发射 SESSION_END，
        会话内所有链路事件经 ContextVar 归属本会话。
        """
        async with thinking_session({"is_heartbeat": is_heartbeat}) as session:
            await run_cycle(self, session.end, is_heartbeat=is_heartbeat)

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
            await reply_entry(
                self, anything, images, adapter_key=adapter_key, completion=completion,
            )
        finally:
            # 回复工作完成边界：execute_reply 路径此时尚未注销 scope（不收口），
            # PROACTIVE 直调路径无登记（无其他活跃即归零）
            self._reset_phase_if_idle()

    async def prewarm_scope_cache(self, scope_type: str, scope_id: str) -> None:
        """请求预热会话折叠后的上下文前缀。"""
        from agent.context.prewarm import prewarm_scope_cache
        models_summary, direct_vision = model_context(self.llm, self.llm_manager)
        await prewarm_scope_cache(
            scope_type, scope_id, context=self.context, char=self.char,
            models_summary=models_summary, direct_vision=direct_vision,
            retriever=self.retriever, conversation_data=self.conversation_data, client=self.llm,
            get_tools=self.pfc.get_active_tool_schemas,
            timeout=self.config.llm_timeout * 20,
        )

    async def reflect(
            self,
            messages: List[Dict],
            *,
            adapter_key: str = "",
            max_iterations: int = 0,
            options: Optional[dict] = None,
            tool_tags: Optional[List[str]] = None,
            allow_output_tools: bool = True,
            extra_blocked_tools: Optional[Set[str]] = None,
            completion: Optional[Dict] = None,
            require_output: bool = False,
            inherited_tools: Optional[List[Dict]] = None,
    ) -> str:
        """内部任务循环：正文仅内部可见，显式发送遵循任务的输出权限。

        completion 非 None 时，循环结束把结束原因写入 completion["reason"]：
        completed（正常收束）/ budget_exhausted（轮次预算用尽，产出可能只是
        中途状态）/ interrupted（协作中断）——调用方据此决策是否需要续委托。

        tool_tags 非空时按选择器加载工具集（替代默认的 "heartbeat" 标签）。
        选择器优先按 tag 匹配，同时兼容按 group 名匹配（如 mcp:web-fetch）。
        工具目录为精简常态集（always + 选择器），更多分组由模型经
        list_entity_methods / activate_tool_group 按需唤醒（自服务扩展）。
        默认允许 output 类工具（send_message/send_file 等）；任务可显式禁用。
        extra_blocked_tools 可追加屏蔽特定工具（如子代理 leaf 角色屏蔽 delegate_task）。
        require_output 要求结束前提交总结：空结束只在剩余预算内补一个禁止操作的总结轮。
        inherited_tools 保留触发轮的工具顺序，缺少的选择器工具追加到尾部；
        相同目录有利于复用缓存，执行权限仍由 blocked_tools 与统一门控约束。

        Returns:
            LLM 产出的文本内容（所有轮次输出的合并）。
        """
        self.note_activity()
        # 所有反思入口共用计时，供态势查询计算真实间隔。
        self._last_reflect_time = time.time()
        # 进行中思考计数：phase 空闲判定的活跃事实源（并发 reflect 时仅最后
        # 一个收尾的调用触发归零），异常也必须复位
        self._reflect_depth += 1
        try:
            mc = self.config
            safety_limit = max_iterations or mc.max_tool_iterations
            blocked_tools = build_reflect_blocklist(allow_output_tools)
            if extra_blocked_tools:
                blocked_tools = blocked_tools | set(extra_blocked_tools)

            # 每次反思会话使用唯一 scope：并行子代理/心跳 reflect 的 plan 与
            # 工具激活状态按 scope 隔离，共享字面量会互相串扰
            reflect_scope = f"reflect:{uuid.uuid4().hex[:8]}"
            active_tools, extra_selectors = await select_reflect_tools(
                self.pfc, adapter_key=adapter_key, scope=reflect_scope,
                tool_tags=tool_tags, inherited_tools=inherited_tools,
            )

            collected_text: List[str] = []
            execution_steps: List[str] = []
            output_policy = "放开外发" if allow_output_tools else "禁用外发"
            log(f"反思循环开始: {len(active_tools)} 个工具可用, 策略={output_policy}, 上限 {safety_limit} 轮", tag="思维")

            from agent.mind.think_session import think_session
            from core.tool_context import tool_request

            with think_session(self, reflect_scope, with_token=False), tool_request(reflect_scope, inherit=True):
                await think_loop(
                    self,
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
                    completion=completion,
                    reflect_tool_selectors=extra_selectors,
                    require_output=require_output,
                )

            total = "\n".join(collected_text)
            log(f"反思循环结束: 产出 {len(total)} 字", tag="思维")
            return total
        finally:
            self._reflect_depth -= 1
            self._reset_phase_if_idle()

    async def execute_task(self, task_name: str) -> Optional[str]:
        """按名称执行指定任务，返回任务产出文本或 None。"""
        log(f"执行任务: {task_name}", tag="思维")
        return await self.heartbeat_engine.run_task(task_name)

    # 召回调度与会话数据访问

    async def get_recollection(
            self,
            conversation_list: Optional[List[Dict]] = None,
            anything: Optional["Everything"] = None,
            *, lean: bool = False,
    ) -> List[Dict]:
        """观测上下文准备过程，成功后展示实际装配结果，失败仍交给调用方处理。"""
        started = time.monotonic()
        await event_bus.emit(EVENT_ACTIVITY_CONTEXT, {"status": "running"})
        try:
            messages = await prepare_recollection(self, conversation_list, anything, lean=lean)
        except BaseException as exc:
            await event_bus.emit(EVENT_ACTIVITY_CONTEXT, {
                "status": "interrupted" if isinstance(exc, asyncio.CancelledError) else "error",
                "duration_ms": round((time.monotonic() - started) * 1000),
                "error": f"{type(exc).__name__}: {exc}",
            })
            raise
        await emit_context_summary(messages, round((time.monotonic() - started) * 1000))
        return messages

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
        await self.conversation_data.router.append(
            StorageDomain.CONVERSATION,
            scope_type=anything.scope_type, scope_id=anything.scope_id,
            role=role, content=content,
            adapter_key=getattr(anything, "adapter_key", "") or "",
        )
