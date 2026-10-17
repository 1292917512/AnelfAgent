"""思维链路追踪器（core.tracer）单元测试。

覆盖：子代理会话的标签与摘要标记、单会话节点数上限（截断标记）、
关闭开关时在途会话的收束（防前端永久显示运行中）。
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext

import pytest

from core.event_bus import event_bus
from core.trace_session import thinking_session
from core.tracer import _MAX_SESSION_NODES, NodeStatus, NodeType, TraceNode, Tracer


@pytest.fixture
def tracer():
    t = Tracer()
    t.set_enabled(True)
    queue = t.subscribe()
    try:
        yield t
    finally:
        t.unsubscribe(queue)
        t.set_enabled(False)


def _only_session(t: Tracer):
    assert len(t._sessions) == 1
    return next(iter(t._sessions.values()))


@pytest.mark.parametrize("error,status", [(None, NodeStatus.COMPLETED), (ValueError("failed"), NodeStatus.ERROR),
                                          (asyncio.CancelledError("stop"), NodeStatus.WARNING)])
async def test_span_terminal_state_and_original_exception(tracer, error, status):
    expected = pytest.raises(type(error)) if error is not None else nullcontext()
    with expected as caught:
        async with tracer.span("operation") as node:
            node.data["result"] = "evidence"
            if error is not None:
                raise error
    if error is not None:
        assert caught.value is error
    assert node.status is status
    assert node.data == {"result": "evidence"}
    assert node.duration_ms >= 0


async def test_cancelled_tool_is_not_shown_as_completed_or_failed(tracer):
    async with thinking_session({"scope": "user_qq:1"}):
        await event_bus.emit("thinking_tool_start", {"tool_id": "cancelled", "tool_name": "inspect"})
        await event_bus.emit("thinking_tool_end", {"tool_id": "cancelled", "tool_name": "inspect",
                                                   "success": False, "cancelled": True, "duration_ms": 10})
    tool = next(node for node in _only_session(tracer).nodes if node.type == NodeType.TOOL_CALL)
    assert tool.status is NodeStatus.WARNING
    assert tool.duration_ms == 10


class TestDelegationSession:
    async def test_label_and_summary_flag(self, tracer: Tracer) -> None:
        """子代理会话：is_delegation 标记 + 「子代理」标签，与主 AI 会话区分。"""
        async with thinking_session({
            "is_delegation": True, "goal": "计算 17×23", "agent": "",
            "role": "leaf", "delegation_id": "d1",
        }):
            pass
        session = _only_session(tracer)
        assert session.is_delegation is True
        assert session.ended is True
        assert session.nodes[0].label.startswith("子代理 @leaf")
        assert session.nodes[-1].label.startswith("子代理结束")
        assert session.to_summary()["is_delegation"] is True
        assert session.to_summary()["delegation_id"] == "d1"

    async def test_reply_session_unaffected(self, tracer: Tracer) -> None:
        """主 AI 会话：不带 is_delegation 标记（默认 False，标签不变）。"""
        async with thinking_session({"scope": "user_webui:web_user"}):
            pass
        session = _only_session(tracer)
        assert session.is_delegation is False
        assert session.nodes[0].label.startswith("思维会话")


class TestSessionNodeCap:
    async def test_overflow_truncates_with_marker(self, tracer: Tracer) -> None:
        """节点数触顶：后续事件丢弃并留一次截断标记（会话仍可正常结束）。"""
        async with thinking_session({"scope": "s"}):
            for i in range(_MAX_SESSION_NODES + 20):
                tracer._add_node(TraceNode(
                    id=f"n{i}", type=NodeType.SYSTEM_EVENT, label="x",
                ))
        session = _only_session(tracer)
        assert session.nodes_truncated is True
        assert session.ended is True
        ids = [n.id for n in session.nodes]
        assert f"{session.id}_truncated" in ids
        # 触顶后的事件未进入（n0..n498 + start + marker + end）
        assert f"n{_MAX_SESSION_NODES}" not in ids
        assert len(session.nodes) <= _MAX_SESSION_NODES + 2


class TestDisableClosesSessions:
    async def test_inflight_sessions_ended_on_disable(self, tracer: Tracer) -> None:
        """关闭开关时在途会话收束（不再永久显示运行中）。"""
        cm = thinking_session({"scope": "s"})
        await cm.__aenter__()
        session = _only_session(tracer)
        assert session.ended is False
        tracer.set_enabled(False)
        assert session.ended is True
        assert session.nodes[-1].data.get("reason") == "tracer_disabled"
        # 事后退出上下文管理器不重复关闭（幂等）
        await cm.__aexit__(None, None, None)
        assert len([n for n in session.nodes if n.type == NodeType.SESSION_END]) == 1


class TestRecordingLifecycle:
    async def test_disconnected_session_still_finishes(self, tracer: Tracer) -> None:
        async with thinking_session({"scope": "offline"}):
            for queue in list(tracer._sse_subscribers):
                tracer.unsubscribe(queue)
        session = _only_session(tracer)
        assert session.ended is True
        assert session.outcome == "completed"

    async def test_failure_and_cancel_are_distinct(self, tracer: Tracer) -> None:
        import asyncio
        with pytest.raises(ValueError):
            async with thinking_session():
                raise ValueError("failure")
        assert _only_session(tracer).outcome == "failed"
        with pytest.raises(asyncio.CancelledError):
            async with thinking_session():
                raise asyncio.CancelledError()
        assert list(tracer._sessions.values())[-1].outcome == "cancelled"

    async def test_subscriber_overflow_requests_snapshot(self, tracer: Tracer) -> None:
        queue = tracer._sse_subscribers[0]
        for _ in range(queue.maxsize):
            queue.put_nowait({"event": "node_added", "data": {}})
        tracer._push_to_subscribers({"event": "session_end", "data": {}})
        assert queue.get_nowait()["event"] == "resync"
