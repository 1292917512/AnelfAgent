"""系统级链路追踪器。

订阅 EventBus 中的事件，实时构建链路树并推送给 SSE 订阅者。
覆盖两类节点：
  - 会话节点：依附于 Mind 思维会话（EVENT_THINKING_SESSION_START/END 期间）
  - 系统节点：Mind 会话外的系统级事件（代理生命周期、实体调用、适配器、错误等）

当无 SSE 订阅者时自动注销所有事件处理器，实现零开销。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional

from core.event_bus import (
    event_bus,
    EVENT_AGENT_STARTED,
    EVENT_AGENT_STOPPED,
    EVENT_ADAPTER_STARTED,
    EVENT_ADAPTER_STOPPED,
    EVENT_ERROR_OCCURRED,
    EVENT_THINKING_SESSION_START,
    EVENT_THINKING_SESSION_END,
    EVENT_THINKING_PHASE_CHANGE,
    EVENT_THINKING_SITUATION,
    EVENT_THINKING_DECISION,
    EVENT_THINKING_CONTEXT_BUILD,
    EVENT_THINKING_LLM_START,
    EVENT_THINKING_LLM_END,
    EVENT_THINKING_TOOL_START,
    EVENT_THINKING_TOOL_END,
    EVENT_THINKING_REPLY_ROUND,
    EVENT_THINKING_INTROSPECTION,
    EVENT_THINKING_FAKE_TOOL_CALL,
    EVENT_PLUGIN_LOADED,
    EVENT_PLUGIN_UNLOADED,
    EVENT_TRACE_CALL_START,
    EVENT_TRACE_CALL_END,
    EVENT_MULTI_TOOL_PROGRESS,
    EVENT_MULTI_TOOL_COMPLETE,
)
from core.log import log

_OWNER = "thinking_tracer"
_MAX_SYSTEM_NODES = 200


class NodeType(str, Enum):
    # Mind 思维节点（原有，保持不变）
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    PHASE_CHANGE = "phase_change"
    SITUATION = "situation"
    DECISION = "decision"
    CONTEXT_BUILD = "context_build"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    REPLY_ROUND = "reply_round"
    INTROSPECTION = "introspection"
    FAKE_TOOL_CALL = "fake_tool_call"
    TOOLS_CHANGED = "tools_changed"
    # 系统级节点（新增）
    ENTITY_CALL = "entity_call"       # EntityRegistry 工具调用（无 Mind 会话时）
    AGENT_LIFECYCLE = "agent_lifecycle"  # 代理启停
    ADAPTER_EVENT = "adapter_event"   # 适配器启停
    PLUGIN_EVENT = "plugin_event"     # 插件加载/卸载
    SYSTEM_EVENT = "system_event"     # span() 通用系统事件
    ERROR = "error"                   # 系统错误
    MULTI_TOOL_TASK = "multi_tool_task"    # 多工具子任务
    MULTI_TOOL_COMPLETE = "multi_tool_complete"  # 多工具组完成


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    WARNING = "warning"


@dataclass
class TraceNode:
    id: str
    type: NodeType
    label: str
    status: NodeStatus = NodeStatus.COMPLETED
    timestamp: float = field(default_factory=time.time)
    duration_ms: Optional[float] = None
    data: Dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["status"] = self.status.value
        return d


@dataclass
class TraceSession:
    id: str
    start_time: float
    nodes: List[TraceNode] = field(default_factory=list)
    is_heartbeat: bool = False
    is_introspection: bool = False
    ended: bool = False
    end_time: Optional[float] = None
    available_tools: List[str] = field(default_factory=list)

    def to_summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "is_heartbeat": self.is_heartbeat,
            "is_introspection": self.is_introspection,
            "node_count": len(self.nodes),
            "ended": self.ended,
            "duration_ms": round((self.end_time - self.start_time) * 1000) if self.end_time else None,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.to_summary(),
            "nodes": [n.to_dict() for n in self.nodes],
            "available_tools": self.available_tools,
        }


ThinkingSession = TraceSession


class Tracer:
    """系统级链路追踪器。

    - enabled 控制全局开关
    - 仅当有 SSE 订阅者时才注册 EventBus 处理器
    - 会话节点依附于 Mind 思维会话；系统节点独立存储并广播
    - span() 上下文管理器可由任意模块调用
    """

    def __init__(self, *, max_sessions: int = 50) -> None:
        self.enabled: bool = False
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, TraceSession] = OrderedDict()
        self._current_session_id: Optional[str] = None
        self._sse_subscribers: List[asyncio.Queue[Dict[str, Any]]] = []
        self._registered: bool = False
        self._active_llm_nodes: Dict[str, str] = {}
        self._active_tool_nodes: Dict[str, str] = {}
        self._active_entity_nodes: Dict[str, str] = {}
        self._active_multi_tool_nodes: Dict[str, str] = {}
        self._current_round_id: Optional[str] = None
        self._current_llm_id: Optional[str] = None
        self._current_intro_id: Optional[str] = None
        self._current_intro_unit_id: Optional[str] = None
        self._system_nodes: List[TraceNode] = []
        self._pending_broadcasts: List[Dict[str, Any]] = []
        self._flush_task: Optional[asyncio.TimerHandle] = None

    # ==================================================================
    # SSE 订阅管理
    # ==================================================================

    def subscribe(self) -> asyncio.Queue[Dict[str, Any]]:
        """新增 SSE 订阅者，首个订阅者触发处理器注册。"""
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=512)
        self._sse_subscribers.append(queue)
        if len(self._sse_subscribers) == 1 and self.enabled and not self._registered:
            self._register_handlers()
        log(f"思维追踪 SSE 订阅者 +1 (共 {len(self._sse_subscribers)})", "DEBUG", tag="思维追踪")
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Dict[str, Any]]) -> None:
        """移除 SSE 订阅者，最后一个订阅者离开时注销处理器。"""
        if queue in self._sse_subscribers:
            self._sse_subscribers.remove(queue)
        if len(self._sse_subscribers) == 0 and self._registered:
            self._unregister_handlers()
        log(f"思维追踪 SSE 订阅者 -1 (共 {len(self._sse_subscribers)})", "DEBUG", tag="思维追踪")

    def _broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """向所有 SSE 订阅者推送事件，自动批量合并高频事件。

        关键事件（会话开始/结束）立即推送；其他事件缓冲后批量推送。
        """
        payload = {"event": event_type, "data": data}
        _IMMEDIATE_EVENTS = {"session_start", "session_end", "session_update"}
        if event_type in _IMMEDIATE_EVENTS:
            self._flush_pending()
            self._push_to_subscribers(payload)
        else:
            self._pending_broadcasts.append(payload)
            self._schedule_flush()

    def _push_to_subscribers(self, payload: Dict[str, Any]) -> None:
        for q in self._sse_subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def _flush_pending(self) -> None:
        """将缓冲中的事件逐个推送。"""
        self._flush_task = None
        if not self._pending_broadcasts:
            return
        for payload in self._pending_broadcasts:
            self._push_to_subscribers(payload)
        self._pending_broadcasts.clear()

    def _schedule_flush(self) -> None:
        """安排一次延迟 flush（50ms debounce），避免高频事件逐个推送。"""
        if self._flush_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
            self._flush_task = loop.call_later(0.05, self._flush_pending)
        except RuntimeError:
            self._flush_pending()

    # ==================================================================
    # 开关控制
    # ==================================================================

    def set_enabled(self, enabled: bool) -> None:
        """设置追踪开关。

        - 开启时：如果有订阅者，立即注册处理器
        - 关闭时：立即注销处理器并断开所有订阅者
        """
        self.enabled = enabled
        if enabled:
            if self._sse_subscribers and not self._registered:
                self._register_handlers()
        else:
            if self._registered:
                self._unregister_handlers()
        log(f"思维追踪 {'开启' if enabled else '关闭'}", tag="思维追踪")

    # ==================================================================
    # 节点操作：会话内 & 系统级
    # ==================================================================

    def _current_session(self) -> Optional[TraceSession]:
        if self._current_session_id:
            return self._sessions.get(self._current_session_id)
        return None

    def _add_node(self, node: TraceNode) -> None:
        """向当前思维会话追加节点。无会话时静默丢弃（Mind 专属路径使用）。"""
        session = self._current_session()
        if not session:
            return
        session.nodes.append(node)
        self._broadcast("node_added", {
            "session_id": session.id,
            "node": node.to_dict(),
        })

    def _add_system_node(self, node: TraceNode) -> None:
        """追加系统节点：有会话时进入会话，无会话时进入系统节点列表。"""
        session = self._current_session()
        if session:
            session.nodes.append(node)
            self._broadcast("node_added", {
                "session_id": session.id,
                "node": node.to_dict(),
            })
        else:
            self._system_nodes.append(node)
            if len(self._system_nodes) > _MAX_SYSTEM_NODES:
                self._system_nodes = self._system_nodes[-_MAX_SYSTEM_NODES:]
            self._broadcast("system_node", {"node": node.to_dict()})

    def _update_node(self, node_id: str, **updates: Any) -> None:
        """更新当前思维会话内的节点。"""
        session = self._current_session()
        if not session:
            return
        for node in session.nodes:
            if node.id == node_id:
                for k, v in updates.items():
                    setattr(node, k, v)
                self._broadcast("node_updated", {
                    "session_id": session.id,
                    "node_id": node_id,
                    "updates": {k: v.value if isinstance(v, Enum) else v for k, v in updates.items()},
                })
                break

    def _update_system_node(self, node_id: str, **updates: Any) -> None:
        """更新系统节点列表中的节点（无会话路径使用）。"""
        for node in reversed(self._system_nodes):
            if node.id == node_id:
                for k, v in updates.items():
                    setattr(node, k, v)
                self._broadcast("system_node_updated", {
                    "node_id": node_id,
                    "updates": {k: v.value if isinstance(v, Enum) else v for k, v in updates.items()},
                })
                break

    def _elapsed_ms(self, node_id: str) -> Optional[float]:
        """计算节点从创建到现在的耗时（毫秒）。"""
        session = self._current_session()
        if not session:
            return None
        for node in session.nodes:
            if node.id == node_id:
                return round((time.time() - node.timestamp) * 1000)
        return None

    def _trim_sessions(self) -> None:
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)

    # ==================================================================
    # span() — 通用追踪上下文管理器
    # ==================================================================

    @contextlib.asynccontextmanager
    async def span(
        self,
        label: str,
        node_type: NodeType = NodeType.SYSTEM_EVENT,
        parent_id: Optional[str] = None,
        **data: Any,
    ) -> AsyncIterator[TraceNode]:
        """在当前追踪上下文中创建一个有生命周期的节点。

        有 Mind 会话时进入会话；无会话时进入系统节点列表。
        节点从 RUNNING 状态开始，退出时自动更新为 COMPLETED 或 ERROR。

        用法::

            async with thinking_tracer.span("初始化存储", NodeType.SYSTEM_EVENT) as node:
                node.data["backend"] = "sqlite"
                await storage.init()
        """
        node = TraceNode(
            id=f"span_{uuid.uuid4().hex[:8]}",
            type=node_type,
            label=label,
            status=NodeStatus.RUNNING,
            data=dict(data),
            parent_id=parent_id,
        )
        in_session = self._current_session_id is not None
        self._add_system_node(node)
        try:
            yield node
            node.status = NodeStatus.COMPLETED
            node.duration_ms = round((time.time() - node.timestamp) * 1000)
            updates = {
                "status": NodeStatus.COMPLETED,
                "duration_ms": node.duration_ms,
                "label": node.label,
                "data": node.data,
            }
            if in_session:
                self._update_node(node.id, **updates)
            else:
                self._update_system_node(node.id, **updates)
        except Exception:
            node.status = NodeStatus.ERROR
            node.duration_ms = round((time.time() - node.timestamp) * 1000)
            updates = {
                "status": NodeStatus.ERROR,
                "duration_ms": node.duration_ms,
                "label": node.label,
                "data": node.data,
            }
            if in_session:
                self._update_node(node.id, **updates)
            else:
                self._update_system_node(node.id, **updates)
            raise

    # ==================================================================
    # 会话 / 节点查询
    # ==================================================================

    def get_sessions_list(self) -> List[Dict[str, Any]]:
        return [s.to_summary() for s in reversed(self._sessions.values())]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        s = self._sessions.get(session_id)
        return s.to_dict() if s else None

    def get_system_nodes(self, limit: int = 50) -> List[Dict[str, Any]]:
        """返回最近 N 条系统节点（无会话时产生的节点）。"""
        return [n.to_dict() for n in self._system_nodes[-limit:]]

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "subscriber_count": len(self._sse_subscribers),
            "session_count": len(self._sessions),
            "current_session_id": self._current_session_id,
            "max_sessions": self.max_sessions,
            "system_node_count": len(self._system_nodes),
        }

    # ==================================================================
    # EventBus 处理器注册
    # ==================================================================

    def _register_handlers(self) -> None:
        if self._registered:
            return
        # Mind 思维事件
        event_bus.on(EVENT_THINKING_SESSION_START, self._on_session_start, owner=_OWNER)
        event_bus.on(EVENT_THINKING_SESSION_END, self._on_session_end, owner=_OWNER)
        event_bus.on(EVENT_THINKING_PHASE_CHANGE, self._on_phase_change, owner=_OWNER)
        event_bus.on(EVENT_THINKING_SITUATION, self._on_situation, owner=_OWNER)
        event_bus.on(EVENT_THINKING_DECISION, self._on_decision, owner=_OWNER)
        event_bus.on(EVENT_THINKING_CONTEXT_BUILD, self._on_context_build, owner=_OWNER)
        event_bus.on(EVENT_THINKING_LLM_START, self._on_llm_start, owner=_OWNER)
        event_bus.on(EVENT_THINKING_LLM_END, self._on_llm_end, owner=_OWNER)
        event_bus.on(EVENT_THINKING_TOOL_START, self._on_tool_start, owner=_OWNER)
        event_bus.on(EVENT_THINKING_TOOL_END, self._on_tool_end, owner=_OWNER)
        event_bus.on(EVENT_THINKING_REPLY_ROUND, self._on_reply_round, owner=_OWNER)
        event_bus.on(EVENT_THINKING_INTROSPECTION, self._on_introspection, owner=_OWNER)
        event_bus.on(EVENT_THINKING_FAKE_TOOL_CALL, self._on_fake_tool_call, owner=_OWNER)
        # 插件事件
        event_bus.on(EVENT_PLUGIN_LOADED, self._on_plugin_loaded, owner=_OWNER)
        event_bus.on(EVENT_PLUGIN_UNLOADED, self._on_plugin_unloaded, owner=_OWNER)
        # 系统级事件（新增）
        event_bus.on(EVENT_AGENT_STARTED, self._on_agent_started, owner=_OWNER)
        event_bus.on(EVENT_AGENT_STOPPED, self._on_agent_stopped, owner=_OWNER)
        event_bus.on(EVENT_ADAPTER_STARTED, self._on_adapter_started, owner=_OWNER)
        event_bus.on(EVENT_ADAPTER_STOPPED, self._on_adapter_stopped, owner=_OWNER)
        event_bus.on(EVENT_ERROR_OCCURRED, self._on_error, owner=_OWNER)
        event_bus.on(EVENT_TRACE_CALL_START, self._on_entity_call_start, owner=_OWNER)
        event_bus.on(EVENT_TRACE_CALL_END, self._on_entity_call_end, owner=_OWNER)
        # 多工具批量调用事件
        event_bus.on(EVENT_MULTI_TOOL_PROGRESS, self._on_multi_tool_progress, owner=_OWNER)
        event_bus.on(EVENT_MULTI_TOOL_COMPLETE, self._on_multi_tool_complete, owner=_OWNER)
        self._registered = True
        log("思维追踪事件处理器已注册", "DEBUG", tag="思维追踪")

    def _unregister_handlers(self) -> None:
        if not self._registered:
            return
        event_bus.off_by_owner(_OWNER)
        self._registered = False
        log("思维追踪事件处理器已注销", "DEBUG", tag="思维追踪")

    # ==================================================================
    # Mind 思维事件处理器（原有，保持不变）
    # ==================================================================

    async def _on_session_start(self, payload: Dict[str, Any]) -> None:
        sid = str(uuid.uuid4())[:12]
        is_intro = bool(payload.get("is_introspection", False))
        session = TraceSession(
            id=sid,
            start_time=time.time(),
            is_heartbeat=payload.get("is_heartbeat", False),
            is_introspection=is_intro,
        )
        self._sessions[sid] = session
        self._current_session_id = sid
        self._active_llm_nodes.clear()
        self._active_tool_nodes.clear()
        self._current_round_id = None
        self._current_llm_id = None
        self._current_intro_id = None
        self._current_intro_unit_id = None
        self._trim_sessions()

        if is_intro:
            entity = payload.get("entity", "全局")
            label = f"内省: {entity}"
        elif session.is_heartbeat:
            label = "心跳"
        else:
            label = f"思维会话 {payload.get('scope', '')}".strip()

        node = TraceNode(
            id=f"{sid}_start",
            type=NodeType.SESSION_START,
            label=label,
            data=payload,
        )
        session.nodes.append(node)
        self._broadcast("session_start", {
            "session": session.to_summary(),
            "node": node.to_dict(),
        })

    async def _on_session_end(self, payload: Dict[str, Any]) -> None:
        session = self._current_session()
        if not session:
            return
        session.ended = True
        session.end_time = time.time()
        reason = payload.get("reason", "")
        if session.is_introspection:
            label = f"内省完成" + (f": {reason}" if reason and reason != "introspection_completed" else "")
        else:
            label = f"会话结束: {reason}"
        node = TraceNode(
            id=f"{session.id}_end",
            type=NodeType.SESSION_END,
            label=label,
            data=payload,
        )
        session.nodes.append(node)
        self._broadcast("session_end", {
            "session_id": session.id,
            "node": node.to_dict(),
            "summary": session.to_summary(),
        })
        self._current_session_id = None

    async def _on_phase_change(self, payload: Dict[str, Any]) -> None:
        phase = payload.get("phase", "")
        prev = payload.get("prev_phase", "")
        if phase == prev:
            return
        self._add_node(TraceNode(
            id=f"phase_{uuid.uuid4().hex[:8]}",
            type=NodeType.PHASE_CHANGE,
            label=f"{prev} → {phase}",
            data=payload,
        ))

    async def _on_situation(self, payload: Dict[str, Any]) -> None:
        msg_count = payload.get("message_count", 0)
        task_count = payload.get("task_count", 0)
        self._add_node(TraceNode(
            id=f"sit_{uuid.uuid4().hex[:8]}",
            type=NodeType.SITUATION,
            label=f"态势: {msg_count} 消息, {task_count} 任务",
            data=payload,
        ))

    async def _on_decision(self, payload: Dict[str, Any]) -> None:
        decisions = payload.get("decisions", [])
        types = [d.get("type", "?") for d in decisions]
        self._add_node(TraceNode(
            id=f"dec_{uuid.uuid4().hex[:8]}",
            type=NodeType.DECISION,
            label=f"决策: {', '.join(types)}",
            data=payload,
        ))

    async def _on_context_build(self, payload: Dict[str, Any]) -> None:
        self._add_node(TraceNode(
            id=f"ctx_{uuid.uuid4().hex[:8]}",
            type=NodeType.CONTEXT_BUILD,
            label=f"上下文构建 (记忆:{payload.get('memory_msgs_count', 0)})",
            data=payload,
        ))

    async def _on_llm_start(self, payload: Dict[str, Any]) -> None:
        nid = f"llm_{uuid.uuid4().hex[:8]}"
        model = payload.get("model", "?")
        self._active_llm_nodes[model] = nid
        self._current_llm_id = nid
        tool_count = payload.get("tool_count", 0)
        tool_names: List[str] = payload.get("tool_names", [])

        session = self._current_session()
        if session and tool_names:
            session.available_tools = tool_names
            self._broadcast("tools_updated", {
                "session_id": session.id,
                "tools": tool_names,
            })

        self._add_node(TraceNode(
            id=nid,
            type=NodeType.LLM_CALL,
            label=f"LLM: {model} ({tool_count} 工具)",
            status=NodeStatus.RUNNING,
            data=payload,
            parent_id=self._current_round_id,
        ))

    async def _on_llm_end(self, payload: Dict[str, Any]) -> None:
        model = payload.get("model", "?")
        nid = self._active_llm_nodes.pop(model, None)
        if nid:
            tool_calls = payload.get("tool_calls", [])
            has_reasoning = payload.get("has_reasoning", False)
            label_suffix = f" → {', '.join(tool_calls)}" if tool_calls else ""
            reasoning_badge = " [推理]" if has_reasoning else ""
            self._update_node(
                nid,
                status=NodeStatus.COMPLETED,
                duration_ms=payload.get("duration_ms"),
                label=f"LLM: {model} ({payload.get('duration_ms', 0)}ms){label_suffix}{reasoning_badge}",
                data=payload,
            )

    async def _on_tool_start(self, payload: Dict[str, Any]) -> None:
        tid = payload.get("tool_id", uuid.uuid4().hex[:8])
        nid = f"tool_{tid}"
        tool_name = payload.get("tool_name", "?")
        self._active_tool_nodes[tid] = nid
        self._add_node(TraceNode(
            id=nid,
            type=NodeType.TOOL_CALL,
            label=f"工具: {tool_name}",
            status=NodeStatus.RUNNING,
            data=payload,
            parent_id=self._current_llm_id,
        ))

    async def _on_tool_end(self, payload: Dict[str, Any]) -> None:
        tid = payload.get("tool_id", "")
        nid = self._active_tool_nodes.pop(tid, None)
        if nid:
            success = payload.get("success", True)
            tool_name = payload.get("tool_name", "?")
            dur = payload.get("duration_ms", 0)
            self._update_node(
                nid,
                status=NodeStatus.COMPLETED if success else NodeStatus.ERROR,
                duration_ms=dur,
                label=f"工具: {tool_name} ({dur}ms)",
                data=payload,
            )

    async def _on_reply_round(self, payload: Dict[str, Any]) -> None:
        iteration = payload.get("iteration", 0)
        rid = f"round_{uuid.uuid4().hex[:8]}"
        self._current_round_id = rid
        self._current_llm_id = None
        self._add_node(TraceNode(
            id=rid,
            type=NodeType.REPLY_ROUND,
            label=f"回复循环 #{iteration + 1}",
            data=payload,
        ))

    async def _on_introspection(self, payload: Dict[str, Any]) -> None:
        stage = payload.get("stage", "")

        if stage == "start":
            nid = f"intro_{uuid.uuid4().hex[:8]}"
            self._current_intro_id = nid
            self._add_node(TraceNode(
                id=nid,
                type=NodeType.INTROSPECTION,
                label=f"内省: {payload.get('entity', '')}",
                status=NodeStatus.RUNNING,
                data=payload,
            ))

        elif stage == "unit_start":
            unit = payload.get("unit", "")
            nid = f"intro_u_{uuid.uuid4().hex[:8]}"
            self._current_intro_unit_id = nid
            self._add_node(TraceNode(
                id=nid,
                type=NodeType.INTROSPECTION,
                label=f"[{payload.get('scope', '')}] {unit}",
                status=NodeStatus.RUNNING,
                data=payload,
                parent_id=self._current_intro_id,
            ))

        elif stage == "unit_phase":
            uid = self._current_intro_unit_id
            phase = payload.get("phase", "")
            _PHASE_LABELS: Dict[str, str] = {
                "context_build": "构建上下文",
                "llm_start": "LLM 反思中…",
                "llm_end": "反思完成",
                "storing": "存储结果",
            }
            label = _PHASE_LABELS.get(phase, phase)
            preview = payload.get("content_preview", "")
            entity_hint = payload.get("entity", "")
            if preview:
                label = f"{label} — {preview[:80]}"
            elif entity_hint:
                label = f"{label} ({entity_hint})"
            phase_status = (
                NodeStatus.COMPLETED
                if phase in ("llm_end", "storing")
                else NodeStatus.RUNNING
            )
            self._add_node(TraceNode(
                id=f"intro_ph_{uuid.uuid4().hex[:8]}",
                type=NodeType.INTROSPECTION,
                label=label,
                status=phase_status,
                data=payload,
                parent_id=uid,
            ))

        elif stage == "unit_end":
            uid = self._current_intro_unit_id
            if uid:
                has_output = payload.get("has_output", False)
                unit = payload.get("unit", "")
                desc = payload.get("description", "")
                mem_type = payload.get("memory_type", "")
                preview = payload.get("content_preview", "")
                if has_output:
                    type_hint = f" [{mem_type}]" if mem_type else ""
                    label = f"[✓] {unit}{type_hint}"
                    if preview:
                        label = f"{label}: {preview[:60]}"
                else:
                    label = f"[—] {unit}"
                    if desc:
                        label = f"{label} ({desc})"
                self._update_node(
                    uid,
                    status=NodeStatus.COMPLETED,
                    label=label,
                    data=payload,
                    duration_ms=self._elapsed_ms(uid),
                )
                self._current_intro_unit_id = None

        elif stage == "unit_error":
            uid = self._current_intro_unit_id
            if uid:
                self._update_node(
                    uid,
                    status=NodeStatus.ERROR,
                    label=f"[✗] {payload.get('unit', '')}",
                    data=payload,
                    duration_ms=self._elapsed_ms(uid),
                )
                self._current_intro_unit_id = None

        elif stage == "unit_skip":
            self._add_node(TraceNode(
                id=f"intro_s_{uuid.uuid4().hex[:8]}",
                type=NodeType.INTROSPECTION,
                label=f"[跳过] {payload.get('unit', '')}",
                status=NodeStatus.WARNING,
                data=payload,
                parent_id=self._current_intro_id,
            ))

        elif stage == "end":
            iid = self._current_intro_id
            if iid:
                module_count = payload.get("module_count", 0)
                self._update_node(
                    iid,
                    status=NodeStatus.COMPLETED,
                    label=f"内省完成: {payload.get('entity', '')} ({module_count} 产出)",
                    data=payload,
                    duration_ms=self._elapsed_ms(iid),
                )
                self._current_intro_id = None

    async def _on_fake_tool_call(self, payload: Dict[str, Any]) -> None:
        """将产生工具幻觉的 LLM 节点直接标红。"""
        if self._current_llm_id:
            consecutive = payload.get("consecutive", 1)
            session = self._current_session()
            if not session:
                return
            for node in session.nodes:
                if node.id == self._current_llm_id:
                    original_label = node.label
                    break
            else:
                original_label = "LLM"
            self._update_node(
                self._current_llm_id,
                status=NodeStatus.ERROR,
                label=f"{original_label} [工具幻觉×{consecutive}]",
                data=payload,
            )

    # ==================================================================
    # 系统级事件处理器（新增）
    # ==================================================================

    async def _on_plugin_loaded(self, payload: Dict[str, Any]) -> None:
        """插件加载：创建 PLUGIN_EVENT 节点 + 广播工具变更。"""
        plugin_name = payload.get("plugin_name", "?")
        self._add_system_node(TraceNode(
            id=f"plugin_{uuid.uuid4().hex[:8]}",
            type=NodeType.PLUGIN_EVENT,
            label=f"插件加载: {plugin_name}",
            data=payload,
        ))
        self._broadcast("tools_changed", payload)

    async def _on_plugin_unloaded(self, payload: Dict[str, Any]) -> None:
        """插件卸载：创建 PLUGIN_EVENT 节点 + 广播工具变更。"""
        plugin_name = payload.get("plugin_name", "?")
        self._add_system_node(TraceNode(
            id=f"plugin_{uuid.uuid4().hex[:8]}",
            type=NodeType.PLUGIN_EVENT,
            label=f"插件卸载: {plugin_name}",
            data=payload,
        ))
        self._broadcast("tools_changed", payload)

    async def _on_agent_started(self, payload: Dict[str, Any]) -> None:
        """代理启动事件。"""
        name = payload.get("name", "")
        self._add_system_node(TraceNode(
            id=f"agent_{uuid.uuid4().hex[:8]}",
            type=NodeType.AGENT_LIFECYCLE,
            label=f"代理启动{': ' + name if name else ''}",
            data=payload,
        ))

    async def _on_agent_stopped(self, payload: Dict[str, Any]) -> None:
        """代理停止事件。"""
        name = payload.get("name", "")
        self._add_system_node(TraceNode(
            id=f"agent_{uuid.uuid4().hex[:8]}",
            type=NodeType.AGENT_LIFECYCLE,
            label=f"代理停止{': ' + name if name else ''}",
            data=payload,
        ))

    async def _on_adapter_started(self, payload: Dict[str, Any]) -> None:
        """适配器启动事件。"""
        key = payload.get("key", "?")
        self._add_system_node(TraceNode(
            id=f"adapter_{uuid.uuid4().hex[:8]}",
            type=NodeType.ADAPTER_EVENT,
            label=f"适配器启动: {key}",
            data=payload,
        ))

    async def _on_adapter_stopped(self, payload: Dict[str, Any]) -> None:
        """适配器停止事件。"""
        key = payload.get("key", "?")
        self._add_system_node(TraceNode(
            id=f"adapter_{uuid.uuid4().hex[:8]}",
            type=NodeType.ADAPTER_EVENT,
            label=f"适配器停止: {key}",
            data=payload,
        ))

    async def _on_error(self, payload: Dict[str, Any]) -> None:
        """系统错误事件。"""
        error_msg = str(payload.get("error", ""))[:80]
        self._add_system_node(TraceNode(
            id=f"err_{uuid.uuid4().hex[:8]}",
            type=NodeType.ERROR,
            label=f"错误: {error_msg}",
            status=NodeStatus.ERROR,
            data=payload,
        ))

    async def _on_entity_call_start(self, payload: Dict[str, Any]) -> None:
        """EntityRegistry 工具调用开始。

        Mind 思维会话进行中时跳过（Mind 已通过 EVENT_THINKING_TOOL_START 追踪）。
        """
        if self._current_session_id:
            return
        call_id = payload.get("call_id", uuid.uuid4().hex[:8])
        nid = f"ec_{uuid.uuid4().hex[:8]}"
        self._active_entity_nodes[call_id] = nid
        name = payload.get("name", "?")
        group = payload.get("group", "")
        label = f"调用: {name}" if not group else f"调用: {group}/{name}"
        self._add_system_node(TraceNode(
            id=nid,
            type=NodeType.ENTITY_CALL,
            label=label,
            status=NodeStatus.RUNNING,
            data=payload,
        ))

    async def _on_entity_call_end(self, payload: Dict[str, Any]) -> None:
        """EntityRegistry 工具调用结束。"""
        if self._current_session_id:
            return
        call_id = payload.get("call_id", "")
        nid = self._active_entity_nodes.pop(call_id, None)
        if not nid:
            return
        success = payload.get("success", True)
        name = payload.get("name", "?")
        dur = payload.get("duration_ms", 0)
        self._update_system_node(
            nid,
            status=NodeStatus.COMPLETED if success else NodeStatus.ERROR,
            duration_ms=dur,
            label=f"调用: {name} ({dur}ms)" + ("" if success else " [失败]"),
            data=payload,
        )

    # ==================================================================
    # 多工具批量调用事件处理器
    # ==================================================================

    def _update_any_node(self, node_id: str, **updates: Any) -> None:
        """更新节点：优先在当前会话中查找，找不到则更新系统节点。"""
        session = self._current_session()
        if session:
            for node in session.nodes:
                if node.id == node_id:
                    for k, v in updates.items():
                        setattr(node, k, v)
                    self._broadcast("node_updated", {
                        "session_id": session.id,
                        "node_id": node_id,
                        "updates": {k: v.value if isinstance(v, Enum) else v for k, v in updates.items()},
                    })
                    return
        self._update_system_node(node_id, **updates)

    async def _on_multi_tool_progress(self, payload: Dict[str, Any]) -> None:
        """多工具子任务进度更新。"""
        task_id = payload.get("task_id", "")
        group_id = payload.get("group_id", "")
        tool = payload.get("tool", "?")
        event_type = payload.get("event", "")

        nid = f"mt_{group_id}_{task_id}"

        if event_type == "start":
            self._active_multi_tool_nodes[nid] = nid
            step = payload.get("step", 1)
            self._add_system_node(TraceNode(
                id=nid,
                type=NodeType.MULTI_TOOL_TASK,
                label=f"[{group_id}] step{step}: {tool}",
                status=NodeStatus.RUNNING,
                data=payload,
            ))
        elif event_type == "done":
            self._active_multi_tool_nodes.pop(nid, None)
            success = payload.get("success", True)
            dur = payload.get("duration_ms", 0)
            step = payload.get("step", 1)
            status = NodeStatus.COMPLETED if success else NodeStatus.ERROR
            label = f"[{group_id}] step{step}: {tool} ({dur}ms)"
            if not success:
                label += " [失败]"
            self._update_any_node(nid, status=status, duration_ms=dur, label=label, data=payload)

    async def _on_multi_tool_complete(self, payload: Dict[str, Any]) -> None:
        """多工具任务组全部完成。"""
        group_id = payload.get("group_id", "")
        total = payload.get("total", 0)
        completed = payload.get("completed", 0)
        failed = payload.get("failed", 0)
        label = f"多工具完成: {group_id} ({completed}/{total})"
        if failed:
            label += f" [{failed} 失败]"
        self._add_system_node(TraceNode(
            id=f"mt_done_{group_id}",
            type=NodeType.MULTI_TOOL_COMPLETE,
            label=label,
            status=NodeStatus.COMPLETED if not failed else NodeStatus.WARNING,
            data=payload,
        ))


ThinkingTracer = Tracer

# 全局单例（保持变量名不变，避免影响现有 router）
thinking_tracer = Tracer()
