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
from agent.core.llm import ChatModel, ChatResult, ImageContent, ToolCall
from agent.core.llm.llm_client import LLMClient
from agent.core.llm.llm_manager import LLMManager
from agent.core.messages import (
    CharacterAgent,
    Everything,
    EverythingGroup,
    MessageAssistant,
)
from agent.core.mind.autonomous import (
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
from agent.core.mind.heartbeat import (
    load_recent as _hb_load_recent,
    append_entry as _hb_append,
    write_log as _hb_write,
)
from agent.core.mind.introspection import Introspection
from agent.core.mind.memory.embedder import Embedder
from agent.core.mind.memory.memory_retriever import MemoryRetriever
from agent.core.mind.memory.memory_store import MemoryStore
from agent.core.mind.memory.notes import build_notes_system_message
from agent.core.mind.prefrontal_cortex import PrefrontalCortex
from agent.core.mind.media_pipeline import MediaPipeline
from agent.core.mind.cross_channel import (
    ChannelSnapshot,
    update_channel_snapshot as _cc_update_snapshot,
    collect_channel_info as _cc_collect_channel_info,
    recall_cross_channel as _cc_recall,
    build_cross_channel_narrative as _cc_build_narrative,
)
from agent.core.mind.decision_executor import (
    execute_decision as _de_execute,
    execute_reply as _de_execute_reply,
    execute_reflect as _de_reflect,
    build_proactive_target as _de_build_proactive,
    resolve_reply_target as _de_resolve_target,
    pop_next_reply_target as _de_pop_target,
)
from agent.core.mind.think_loop import (
    ThinkMode,
    reply_entry as _tl_reply,
    reply_loop as _tl_reply_loop,
    think_loop as _tl_think_loop,
    collect_pending_images as _tl_collect_images,
    apply_vision as _tl_apply_vision,
    save_base64_image as _tl_save_b64_image,
    complete_reply as _tl_complete_reply,
)
from agent.core.channel.manager import ChannelManager
from agent.core.storage.data_center import ConversationData, EverythingData
from agent.core.storage.storage_router import StorageDomain
from core.entity import EntityRegistry

from core.log import log

if TYPE_CHECKING:
    from agent.core.storage.storage_router import StorageRouter

_END_REPLY_TOOL_NAME = "end_reply"


from entities._sdk import deferred_tool, activate_group


@deferred_tool(
    name=_END_REPLY_TOOL_NAME,
    group="thinking", tags=["always"], source="mind.core",
    description="结束本轮操作，回复也是操作。当你已完成所有操作,不再需要继续时,调用此工具。",
)
def _end_reply_tool(reason: str = "") -> str:
    """结束本轮对话。

    Args:
        reason: 结束原因（仅日志记录，用户不可见）
    """
    log(f"AI 主动结束对话: {reason or '无原因'}", tag="思维")
    return json.dumps({"ok": True, "action": "end_reply", "reason": reason}, ensure_ascii=False)


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
        self.intro = Introspection(self)

        self.memory_store = memory_store
        self.embedder = Embedder()
        self.retriever: Optional[MemoryRetriever] = None
        if self.memory_store:
            self.retriever = MemoryRetriever(self.memory_store, self.embedder)

        self.media_pipeline = MediaPipeline()

        self._active_scopes: set[str] = set()
        self._reply_idle_event = asyncio.Event()
        self._reply_idle_event.set()
        self._reply_adapter_key: str = ""
        self.phase: MindPhase = MindPhase.IDLE
        self._last_reflect_time: float = self._load_last_reflect_time()
        self._session_llm_params: dict = {}

        self._reflecting: bool = False

        self._channel_snapshots: dict[str, ChannelSnapshot] = {}

        self._register_core_tools()

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
            return self._reply_adapter_key
        tasks = self.pfc.peek_all_tasks()
        if tasks:
            scope = tasks[0][0]
            return self.pfc.get_adapter_key(scope)
        return ""

    @property
    def tool_executor(self) -> Optional[Callable[[ToolCall], Awaitable[str]]]:
        return EntityRegistry.execute_tool_call

    @staticmethod
    def _get_mind_config():
        from agent.core.config import get_mind_config
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
        """接收外部消息：写入对话历史；仅在 trigger_mind 时加入 PFC 任务队列。"""
        self._set_phase(MindPhase.ACCEPTING)
        preview = str(anything)[:80] if anything else ""
        log(f"感知输入: {preview}", tag="思维")
        await self.add_conversation(anything)
        if anything.trigger_mind:
            await self.pfc.add_task(anything)
            self._update_channel_snapshot(anything)

    def _update_channel_snapshot(self, anything: Everything) -> None:
        """记录频道活动快照，供跨频道感知使用。"""
        _cc_update_snapshot(self, anything)

    @property
    def is_reply(self) -> bool:
        return bool(self._active_scopes)

    async def execute_mind(self, *, is_heartbeat: bool = False) -> None:
        await self._autonomous_cycle(is_heartbeat=is_heartbeat)

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
            await self._heartbeat_maintenance()

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

        # 心跳且超过 reflect_max_hours → 若元决策未选 reflect，强制注入
        if is_heartbeat:
            intro_cfg = self.intro.config
            has_reflect = any(d.type == DecisionType.REFLECT for d in deferred)
            if not has_reflect and situation.hours_since_reflect >= intro_cfg.reflect_max_hours:
                deferred.insert(0, Decision(
                    type=DecisionType.REFLECT, priority=8,
                    reason=(
                        f"强制反思: 距上次反思 {situation.hours_since_reflect:.1f}h"
                        f" >= {intro_cfg.reflect_max_hours}h"
                    ),
                ))
                log(
                    f"强制注入反思决策: {situation.hours_since_reflect:.1f}h 未反思",
                    tag="思维",
                )
            # 心跳阶段已通过 check_memory_thresholds() 完成记忆预检，标记到 REFLECT 决策
            for d in deferred:
                if d.type == DecisionType.REFLECT:
                    d.params["memory_warnings_checked"] = True

            # 检查到期目标，强制注入 PLAN 决策
            due_goal_ids = await self._check_due_goals()
            if due_goal_ids:
                has_plan = any(d.type == DecisionType.PLAN for d in immediate + deferred)
                if not has_plan:
                    deferred.append(Decision(
                        type=DecisionType.PLAN, priority=7,
                        content=f"到期目标需推进: {', '.join(due_goal_ids)}",
                        reason=f"{len(due_goal_ids)} 个目标已到期",
                    ))
                    log(f"强制注入 PLAN 决策: {len(due_goal_ids)} 个目标到期", tag="思维")

        # 反思进行中时跳过新的 REFLECT 决策，避免重入
        if self._reflecting:
            deferred = [d for d in deferred if d.type != DecisionType.REFLECT]

        # 后台决策立即并行启动（不等待 REPLY）
        for d in deferred:
            asyncio.create_task(
                self._safe_execute(d),
                name=f"agent.mind.bg.{d.type.value}",
            )

        # 即时决策保持顺序执行
        for decision in immediate:
            await self._safe_execute(decision)
            await asyncio.sleep(0)

        self.pfc.clear_general_tasks()

        exec_results: List[str] = [f"{d.type.value} 已执行" for d in immediate]
        if is_heartbeat or decisions:
            _hb_write(situation, sorted_decisions, exec_results)

        self.pfc.clear_dynamic_tools()

        await event_bus.emit(EVENT_THINKING_SESSION_END, {
            "reason": "completed",
            "decisions_executed": [d.type.value for d in immediate],
            "decisions_deferred": [d.type.value for d in deferred],
        })

        # SESSION_END 之后检查：如果还有待处理任务（如后台任务完成注入的），自动触发新一轮
        if self.pfc.has_pending_tasks():
            log("自主循环结束后仍有待处理任务，自动触发新一轮", tag="思维")
            asyncio.create_task(self.execute_mind())

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

    async def _heartbeat_maintenance(self) -> None:
        """心跳维护：日志合并 + 实体计数持久化（纯写操作，与态势收集分离）。"""
        try:
            from agent.core.mind.memory.notes import consolidate_heartbeat
            consolidate_heartbeat()
        except Exception as e:
            log(f"心跳日志合并失败: {e}", "DEBUG", tag="思维")
        try:
            saved = await self.everything_data.save_all_entity_counters()
            if saved:
                log(f"心跳持久化实体计数: {saved} 个", "DEBUG", tag="思维")
        except Exception as e:
            log(f"实体计数持久化失败: {e}", "DEBUG", tag="思维")

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

        if is_heartbeat:
            try:
                memory_warnings = await self.intro.check_memory_thresholds()
                for warn in memory_warnings:
                    recent_mem_lines.append(f"[⚠️ 记忆预警] {warn}")
            except Exception as e:
                log(f"记忆阈值检查失败: {e}", "DEBUG", tag="思维")

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
        from agent.core.mind.planning.tools import collect_active_goals
        return await collect_active_goals(self.memory_store)

    async def _check_due_goals(self) -> List[str]:
        """检查已到期的活跃目标，返回到期目标的 goal_id 列表。"""
        if not self.memory_store:
            return []
        from agent.core.mind.planning.tools import check_due_goals
        return await check_due_goals(self.memory_store)

    @staticmethod
    def _load_last_reflect_time() -> float:
        try:
            from agent.core.mind.introspection.config import get_introspection_config
            return get_introspection_config().last_reflect_time
        except Exception:
            return 0.0

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
        result = await self._llm_chat_with_retry(messages, tools, tool_choice=tool_choice, options=options)
        elapsed_ms = (time.time() - t0) * 1000
        mc = self._get_mind_config()
        if mc.log_ai_output:
            if result.reasoning_content:
                log(f"AI 推理: {result.reasoning_content[:300]}", "DEBUG", tag="思维")
            if result.content:
                log(f"AI 输出: {result.content[:500]}", tag="思维")
        await event_bus.emit(EVENT_THINKING_LLM_END, {
            "model": model_name,
            "duration_ms": round(elapsed_ms),
            "has_content": bool(result.content),
            "content_preview": (result.content or "")[:200],
            "tool_calls": [tc.name for tc in result.tool_calls] if result.tool_calls else [],
            "has_reasoning": bool(result.reasoning_content),
            "reasoning_preview": (result.reasoning_content or "")[:800],
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
        # 合并会话临时参数（set_session_params 写入的覆盖值）
        merged_options = dict(options or {})
        if self._session_llm_params:
            merged_options.update(self._session_llm_params)
        final_options = merged_options or None

        if self.llm_manager:
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

    async def reflect(
            self,
            messages: List[Dict],
            *,
            adapter_key: str = "",
            max_iterations: int = 0,
            options: Optional[dict] = None,
            tool_tags: Optional[List[str]] = None,
    ) -> str:
        """反思/任务循环：与对话共享统一思维流程，但不发送消息给用户。

        tool_tags 非空时按指定标签加载工具集（替代默认的 "reflect" 标签），
        用于任务单元的专属工具配置。

        Returns:
            LLM 产出的文本内容（所有轮次输出的合并）。
        """
        mc = self._get_mind_config()
        safety_limit = max_iterations or mc.max_tool_iterations
        active_tools = list(self.pfc.get_active_tool_schemas(adapter_key))

        extra_tags = tool_tags if tool_tags else ["reflect"]
        existing_names = {s.get("function", {}).get("name", "") for s in active_tools}
        for schema in EntityRegistry.get_tool_schema_by_tags(extra_tags):
            name = schema.get("function", {}).get("name", "")
            if name and name not in existing_names:
                active_tools.append(schema)
                existing_names.add(name)

        collected_text: List[str] = []
        log(f"反思循环开始: {len(active_tools)} 个工具可用, 上限 {safety_limit} 轮", tag="思维")

        try:
            await self._think_loop(
                mode=ThinkMode.REFLECT,
                tool_chain=[],
                execution_steps=[],
                start_time=time.time(),
                safety_limit=safety_limit,
                collected_text=collected_text,
                active_tools=active_tools,
                anything=None,
                base_messages=messages,
                options=options,
            )
        finally:
            self.pfc.clear_dynamic_tools()

        total = "\n".join(collected_text)
        log(f"反思循环结束: 产出 {len(total)} 字", tag="思维")
        return total

    async def execute_task(self, task_name: str) -> Optional[str]:
        """按名称执行指定任务单元，返回任务产出文本或 None。"""
        log(f"执行任务: {task_name}", tag="思维")
        result = await self.intro.run_task(task_name)
        return result.content if result else None

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
        if self.memory_store and self.embedder.available:
            try:
                await self.memory_store.backfill_embeddings(self.embedder, batch_size=2)
            except Exception as e:
                log(f"Embedding 回填失败: {e}", "DEBUG", tag="思维")

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

        # 人设 + 便签
        system_parts: List[str] = []
        for msg in self.char.get_personality_msg():
            if msg.get("content"):
                system_parts.append(msg["content"])
        for msg in build_notes_system_message():
            if msg.get("content"):
                system_parts.append(msg["content"])

        await event_bus.emit(EVENT_THINKING_CONTEXT_BUILD, {
            "system_parts_count": len(system_parts),
            "memory_msgs_count": len(memory_msgs),
            "has_persona": bool(system_parts),
        })

        return await self.pfc.build_llm_context(
            system_parts=system_parts,
            memory_msgs=memory_msgs,
            anything=anything,
            adapter_key=getattr(anything, "adapter_key", ""),
            target_id=self._resolve_target_id(anything),
            models_summary=self._get_models_summary(),
        )

    async def get_conversation(self, anything: Everything) -> List[Dict]:
        """从 DB 获取指定对象的对话历史。"""
        return await self.conversation_data.get_conversation_record_by_everything(anything)

    async def add_conversation(self, anything: Everything) -> None:
        """将消息写入对话历史。"""
        await self.conversation_data.add_conversation_record_by_everything(anything)

    async def _add_system_context(self, anything: Everything, content: str, role: str = "assistant") -> None:
        """向对话存储追加一条系统上下文消息。

        Args:
            role: 存储角色。工具摘要等结束性记录应使用 "user"，避免对话末尾残留
                  assistant 消息导致 Anthropic 等模型报 assistant prefill 错误。
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

