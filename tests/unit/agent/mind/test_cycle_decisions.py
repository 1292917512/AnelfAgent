"""自主周期决策执行：心跳周期即时决策全部转后台，用户消息周期保持内联执行。

回归背景：心跳周期的 REPLY/PROACTIVE/TOOL_ACTION 均为分钟级 think loop，
内联执行会让 _cycle_lock 全程被占，期间到达的用户消息被序列化在长决策之后。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock

import pytest

from agent.mind import cycle as cycle_mod
from agent.mind.autonomous import Decision, DecisionType, SituationContext
from agent.mind.mind import Mind


def _fake_mind() -> Any:
    """最小 Mind 替身：只覆盖 _execute_decisions_and_finalize 触达的字段。"""
    return SimpleNamespace(
        _DEFERRED_DECISIONS=Mind._DEFERRED_DECISIONS,
        _reflecting=False,
        _active_scopes=set(),
        _auto_cycle_retry=0,
        _set_phase=lambda phase: None,
        _safe_execute=AsyncMock(return_value=True),
        _clear_dynamic_tools_when_idle=AsyncMock(),
        _schedule_next_cycle=lambda reason: None,
        heartbeat_engine=SimpleNamespace(reflection_pending=False),
        pfc=SimpleNamespace(
            peek_general_tasks=lambda: [],
            clear_general_tasks_before=lambda n: None,
            has_pending_tasks=lambda: False,
        ),
    )


@pytest.fixture(autouse=True)
def _isolate_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cycle_mod.event_bus, "emit", AsyncMock())
    monkeypatch.setattr(cycle_mod, "_hb_write", lambda **kwargs: None)


class TestHeartbeatDecisionDeferral:
    async def test_heartbeat_defers_all_decisions(self) -> None:
        """心跳周期：即时决策转后台执行，函数返回不等决策完成。"""
        mind = _fake_mind()
        release = asyncio.Event()
        done: List[str] = []

        async def _safe(decision: Decision) -> bool:
            await release.wait()
            done.append(decision.type.value)
            return True

        mind._safe_execute = _safe
        decisions = [Decision(type=DecisionType.REPLY, target="user_webui:u1", priority=10)]

        # 决策被门控永不完成，函数仍须正常返回（后台化）
        await asyncio.wait_for(
            cycle_mod._execute_decisions_and_finalize(
                mind, {}, SituationContext(), decisions, is_heartbeat=True,
            ),
            timeout=1.0,
        )
        assert done == []

        release.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if done:
                break
        assert done == [DecisionType.REPLY.value]

    async def test_message_cycle_executes_immediate_inline(self) -> None:
        """用户消息周期：即时决策保持内联执行（函数等待决策完成）。"""
        mind = _fake_mind()
        release = asyncio.Event()
        done: List[str] = []

        async def _safe(decision: Decision) -> bool:
            await release.wait()
            done.append(decision.type.value)
            return True

        mind._safe_execute = _safe
        decisions = [Decision(type=DecisionType.REPLY, target="user_webui:u1", priority=10)]

        runner = asyncio.create_task(
            cycle_mod._execute_decisions_and_finalize(
                mind, {}, SituationContext(), decisions, is_heartbeat=False,
            )
        )
        for _ in range(5):
            await asyncio.sleep(0)
        assert not runner.done()

        release.set()
        await asyncio.wait_for(runner, timeout=1.0)
        assert done == [DecisionType.REPLY.value]
        # 内联执行成功重置续轮退避
        assert mind._auto_cycle_retry == 0

    async def test_heartbeat_still_filters_reflect_when_pending(self) -> None:
        """心跳周期全量转后台后，待反思标记仍过滤重复的 REFLECT 决策。"""
        mind = _fake_mind()
        mind.heartbeat_engine.reflection_pending = True
        scheduled: List[str] = []

        async def _safe(decision: Decision) -> bool:
            scheduled.append(decision.type.value)
            return True

        mind._safe_execute = _safe
        decisions = [
            Decision(type=DecisionType.REFLECT, reason="重复反思", priority=5),
            Decision(type=DecisionType.REPLY, target="user_webui:u1", priority=10),
        ]
        await cycle_mod._execute_decisions_and_finalize(
            mind, {}, SituationContext(), decisions, is_heartbeat=True,
        )
        for _ in range(10):
            await asyncio.sleep(0)
            if len(scheduled) >= 1:
                break
        assert scheduled == [DecisionType.REPLY.value]
