"""子代理全局总览与日志归因单元测试。

覆盖：running_snapshot_all 全 scope 快照字段、事件驱动的实时进度计数
（iteration / current_tool）、委托执行期日志 actor 前缀归因与复位。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, List, Optional

from agent.delegation.delegation_manager import DelegationManager
from agent.delegation.sub_agent import bind_delegation_id, reset_delegation_id
from core import log as log_mod
from core.event_bus import (
    EVENT_THINKING_REPLY_ROUND,
    EVENT_THINKING_TOOL_END,
    EVENT_THINKING_TOOL_START,
    event_bus,
)
from core.log import current_log_actor, query_log_buffer


class _FakeMind:
    """最小 Mind 替身：reflect 可挂门闩阻塞（制造运行中窗口）。"""

    def __init__(self, output: str = "完成", gate: Optional[asyncio.Event] = None) -> None:
        self.output = output
        self.gate = gate
        self.pfc = type("PFC", (), {"add_temporary": lambda self, clip, scope="": None})()

    async def reflect(self, *args: Any, **kwargs: Any) -> str:
        if self.gate is not None:
            await self.gate.wait()
        return self.output

    def get_model_context_length(self) -> int:
        return 128_000


def _buffer_messages(limit: int = 50) -> List[str]:
    return [str(r["message"]) for r in query_log_buffer(limit=limit)]


class TestRunningSnapshotAll:
    async def test_snapshot_fields(self) -> None:
        gate = asyncio.Event()
        manager = DelegationManager(_FakeMind(gate=gate))
        run = asyncio.create_task(
            manager.delegate(
                "总览目标", scope_hint="user_webui:web_user", agent_name="researcher",
            ),
        )
        try:
            await asyncio.sleep(0.05)
            snap = manager.running_snapshot_all()
            assert len(snap) == 1
            item = snap[0]
            assert item["goal"] == "总览目标"
            assert item["scope"] == "user_webui:web_user"
            assert item["agent"] == "researcher"
            assert item["background"] is True
            assert item["started_at"] > 0
            assert item["iteration"] == 0 and item["current_tool"] == ""
            assert item["usage"]["turns"] == 0
            assert manager.is_running(item["delegation_id"])
        finally:
            gate.set()
        result = await run
        assert result.success
        assert manager.running_snapshot_all() == []
        assert not manager.is_running("不存在的id")

    async def test_scope_snapshot_still_filtered(self) -> None:
        gate = asyncio.Event()
        manager = DelegationManager(_FakeMind(gate=gate))
        run = asyncio.create_task(manager.delegate("任务", scope_hint="user_qq:42"))
        try:
            await asyncio.sleep(0.05)
            assert len(manager.running_snapshot("user_qq:42")) == 1
            assert manager.running_snapshot("user_webui:web_user") == []
            assert len(manager.running_snapshot_all()) == 1
        finally:
            gate.set()
        await run


class TestProgressCounters:
    async def test_events_update_running_entry(self) -> None:
        manager = DelegationManager(_FakeMind())
        manager._running["ov-d1"] = {
            "goal": "g", "scope": "user_qq:1", "chat_id": "", "role": "leaf",
            "task_index": 0, "background": False, "model": "", "agent": "",
            "started_at": time.time(),
        }
        token = bind_delegation_id("ov-d1")
        try:
            await event_bus.emit(EVENT_THINKING_REPLY_ROUND, {"iteration": 0})
            await event_bus.emit(EVENT_THINKING_TOOL_START, {"tool_name": "search"})
            info = manager._running["ov-d1"]
            # 事件从 0 起，快照存展示轮次（从 1 起，对齐前端口径）
            assert info["iteration"] == 1
            assert info["current_tool"] == "search"
            await event_bus.emit(EVENT_THINKING_TOOL_END, {"tool_name": "search", "success": True})
            assert manager._running["ov-d1"]["current_tool"] == ""
        finally:
            reset_delegation_id(token)
        snap = manager.running_snapshot_all()
        assert snap[0]["iteration"] == 1
        assert snap[0]["current_tool"] == ""

    async def test_main_ai_events_ignored(self) -> None:
        """未绑定委托上下文（主 AI）的事件不产生进度归属。"""
        manager = DelegationManager(_FakeMind())
        manager._running["ov-d2"] = {
            "goal": "g", "scope": "user_qq:1", "chat_id": "", "role": "leaf",
            "task_index": 0, "background": False, "model": "", "agent": "",
            "started_at": time.time(),
        }
        await event_bus.emit(EVENT_THINKING_REPLY_ROUND, {"iteration": 3})
        assert "iteration" not in manager._running["ov-d2"]


class TestLogActorAttribution:
    async def test_delegate_logs_carry_actor_prefix(self) -> None:
        class _LoggingMind(_FakeMind):
            async def reflect(self, *args: Any, **kwargs: Any) -> str:
                log_mod.log("actor归因-子代理内部")
                return "done"

        manager = DelegationManager(_LoggingMind())
        result = await manager.delegate("日志归因任务")
        assert result.success
        matched = [m for m in _buffer_messages() if "actor归因-子代理内部" in m]
        assert matched, "子代理内部日志应进入环形缓冲区"
        assert matched[0].startswith("[子代理@")
        # 委托结束后主上下文 actor 已复位
        assert current_log_actor() == ""

    async def test_actor_label_contains_agent_and_id(self) -> None:
        observed: List[str] = []

        class _CaptureMind(_FakeMind):
            async def reflect(self, *args: Any, **kwargs: Any) -> str:
                observed.append(current_log_actor())
                return "done"

        manager = DelegationManager(_CaptureMind())
        result = await manager.delegate("标签检查", agent_name="researcher")
        assert result.success
        assert observed and observed[0].startswith("子代理@researcher#")
