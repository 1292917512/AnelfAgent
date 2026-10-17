"""Mind — 思维核心。

统一处理所有消息输入，通过 LLM 实现自主决策和多轮原生工具调用。

模型可见：聊天正文按会话历史传入，不解析为核心控制命令或话题禁令。
Token 影响：不从消息关键词派生额外纪律提示。
缓存影响：正文只追加到会话历史，不生成话题纪律前缀。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from agent.messages import EverythingGroup
from agent.mind.autonomous import MindPhase
from agent.mind.cycle import run_cycle
from agent.mind.execution.reflect import reflect_entry
from agent.mind.execution.reply import reply_entry
from agent.mind.interrupt import InterruptRegistry
from agent.mind.perception import should_enqueue_external_message
from core.event_bus import (
    EVENT_THINKING_PHASE_CHANGE,
    event_bus,
)
from core.log import log
from core.trace_session import thinking_session

if TYPE_CHECKING:
    from agent.channel.manager import ChannelManager
    from agent.config import MindConfig
    from agent.context.assembly import ContextAssembly
    from agent.context.compressor import ContextCompressor
    from agent.delegation.delegation_manager import DelegationManager
    from agent.heartbeat.engine import HeartbeatEngine
    from agent.llm import ChatModel, ImageContent
    from agent.llm.llm_manager import LLMManager
    from agent.memory.embedding import Embedder
    from agent.memory.memory_retriever import MemoryRetriever
    from agent.memory.memory_store import MemoryStore
    from agent.messages import CharacterAgent, Everything
    from agent.mind.background.tasks import BackgroundTaskRegistry
    from agent.mind.llm_invoker import ModelInvoker
    from agent.mind.prefrontal_cortex import PrefrontalCortex
    from agent.mind.push import PushHub
    from agent.mind.tool_assembly import ToolAssembly
    from agent.skills.runtime import SkillRuntime
    from agent.storage.data_center import ConversationData


class Mind:
    """统一思维核心：自主决策、LLM 对话、工具编排。"""

    heartbeat_engine: HeartbeatEngine
    compressor: ContextCompressor
    delegation_manager: DelegationManager
    push_hub: PushHub
    model_invoker: ModelInvoker

    def __init__(
            self,
            *,
            config: MindConfig,
            char: CharacterAgent,
            llm: ChatModel,
            llm_manager: Optional[LLMManager] = None,
            channel_manager: ChannelManager,
            conversation_data: ConversationData,
            pfc: PrefrontalCortex,
            tool_assembly: ToolAssembly,
            context: ContextAssembly,
            skills: SkillRuntime,
            background_tasks: BackgroundTaskRegistry,
            embedder: Embedder,
            retriever: Optional[MemoryRetriever],
            memory_store: Optional[MemoryStore] = None,
    ) -> None:
        self.config = config
        self.char = char
        self.llm = llm
        self.llm_manager = llm_manager
        self.channel_manager = channel_manager
        self.conversation_data = conversation_data

        self.skills = skills
        self.background_tasks = background_tasks
        self.pfc = pfc
        self.tool_assembly = tool_assembly
        self.context = context
        self.memory_store = memory_store
        self.embedder = embedder
        self.retriever = retriever

        self._active_scopes: set[str] = set()
        # scope → 本次回复的激活时刻（中断请求的新旧分界，见 InterruptRegistry.clear_before）
        self._reply_activated_at: Dict[str, float] = {}
        self._reply_idle_event = asyncio.Event()
        self._reply_idle_event.set()
        self.phase: MindPhase = MindPhase.IDLE
        self._last_reflect_time: float = 0.0
        # 最近一次真实思考活动时间戳（idle 空闲计数锚点，见 last_activity_ts）
        self._last_activity_ts: float = 0.0

        self._reflecting: bool = False
        # 进行中的 reflect 调用深度（并发子代理/任务/反思共用唯一入口，
        # phase 空闲判定的活跃事实源之一，asyncio 单线程计数无竞态）
        self._reflect_depth: int = 0
        self._cycle_lock = asyncio.Lock()
        # 自动续轮退避计数（周期内无实质进展时递增，防紧凑重试烧 token）
        self._auto_cycle_retry: int = 0

        # scope 级中断信号（think_loop 每轮检查，用户可刹车失控回复）
        self.interrupts = InterruptRegistry()

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
        """接收外部消息：到达即写入对话历史（保证时序）；仅在 trigger_mind 时加入 PFC 队列。"""
        try:
            # 思维工作运行中不覆盖当前阶段，避免 UI 阶段抖动
            if (not self._active_scopes and not self._cycle_lock.locked()
                    and self._reflect_depth == 0):
                self._set_phase(MindPhase.ACCEPTING)
            preview = str(anything)[:80] if anything else ""
            log(f"感知输入: {preview}", tag="思维")
            if not await self.conversation_data.add_conversation_record_by_everything(anything):
                return
            self.pfc.observe_channel(
                anything, window_minutes=self.config.cross_channel_window_minutes,
            )

            should_enqueue = should_enqueue_external_message(anything, reflecting=self._reflecting)
            if (
                not should_enqueue
                and bool(getattr(anything, "trigger_mind", True))
                and self._reflecting
                and isinstance(anything, EverythingGroup)
                and not bool(getattr(anything, "to_me", False))
            ):
                log(f"反思中忽略非 @ 群消息: {anything.entity_scope}", "DEBUG", tag="思维")
            if should_enqueue:
                # 外部消息入队时重置自动续轮退避。
                self._auto_cycle_retry = 0
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
        """通过显式控制入口中断指定会话，唤醒在途等待并在检查点收束。

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
            return await reflect_entry(
                self, messages, adapter_key=adapter_key, max_iterations=max_iterations,
                options=options, tool_tags=tool_tags, allow_output_tools=allow_output_tools,
                extra_blocked_tools=extra_blocked_tools, completion=completion,
                require_output=require_output, inherited_tools=inherited_tools,
            )
        finally:
            self._reflect_depth -= 1
            self._reset_phase_if_idle()
