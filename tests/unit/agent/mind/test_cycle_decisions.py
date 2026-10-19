"""主周期决策执行：所有主决策串行，心跳在决策边界向用户消息让路。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent.mind import cycle as cycle_mod
from agent.mind.autonomous import Decision, DecisionType, PendingMessage, SituationContext


class _PendingQueue:
    def __init__(self) -> None:
        self.empty = True

    def is_empty(self) -> bool:
        return self.empty


def _fake_mind() -> Any:
    """最小 Mind 替身：只覆盖 execute_decisions 触达的字段。"""
    return SimpleNamespace(
        _reflecting=False,
        _active_scopes=set(),
        _auto_cycle_retry=0,
        set_phase=lambda phase: None,
        _schedule_next_cycle=lambda reason: None,
        driver=SimpleNamespace(request_cycle=Mock()),
        heartbeat_engine=SimpleNamespace(reflection_pending=False),
        _reply_idle_event=asyncio.Event(),
        tool_assembly=SimpleNamespace(clear_when_idle=Mock()),
        pfc=SimpleNamespace(
            peek_general_tasks=lambda: [],
            clear_general_tasks_before=Mock(),
            has_pending_tasks=lambda: False,
            pending_user=_PendingQueue(),
            pending_group=_PendingQueue(),
        ),
    )


@pytest.fixture(autouse=True)
def _isolate_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cycle_mod.event_bus, "emit", AsyncMock())
    monkeypatch.setattr(cycle_mod, "_hb_write", lambda **kwargs: None)
    monkeypatch.setattr(cycle_mod, "execute_safely", AsyncMock(return_value=True))


@pytest.mark.parametrize("heartbeat", [False, True])
async def test_decisions_execute_inline_and_serially(monkeypatch, heartbeat: bool) -> None:
    mind = _fake_mind()
    events: list[str] = []
    active = 0
    max_active = 0

    async def _safe(_mind, decision: Decision) -> bool:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        events.append(f"start:{decision.type.value}")
        await asyncio.sleep(0)
        events.append(f"end:{decision.type.value}")
        active -= 1
        return True

    monkeypatch.setattr(cycle_mod, "execute_safely", _safe)
    decisions = [
        Decision(type=DecisionType.PLAN, content="规划", priority=5),
        Decision(type=DecisionType.REPLY, target="user_webui:u1", priority=10),
    ]
    await cycle_mod.execute_decisions(
        mind, {}, SituationContext(), decisions, is_heartbeat=heartbeat,
    )

    assert events == ["start:reply", "end:reply", "start:plan", "end:plan"]
    assert max_active == 1


async def test_heartbeat_yields_to_new_message_between_decisions(monkeypatch) -> None:
    mind = _fake_mind()
    executed: list[str] = []

    async def _safe(_mind, decision: Decision) -> bool:
        executed.append(decision.type.value)
        mind.pfc.pending_user.empty = False
        return True

    monkeypatch.setattr(cycle_mod, "execute_safely", _safe)
    decisions = [
        Decision(type=DecisionType.REPLY, target="user_webui:u1", priority=10),
        Decision(type=DecisionType.PLAN, content="自主规划", priority=5),
    ]
    await cycle_mod.execute_decisions(
        mind, {}, SituationContext(), decisions, is_heartbeat=True,
    )

    assert executed == [DecisionType.REPLY.value]
    mind.driver.request_cycle.assert_called_once_with()
    mind.pfc.clear_general_tasks_before.assert_not_called()


async def test_heartbeat_with_general_tasks_finishes_batch_before_ack(monkeypatch) -> None:
    mind = _fake_mind()
    mind.pfc.peek_general_tasks = lambda: ["existing"]
    executed: list[str] = []

    async def _safe(_mind, decision: Decision) -> bool:
        executed.append(decision.type.value)
        mind.pfc.pending_user.empty = False
        return True

    monkeypatch.setattr(cycle_mod, "execute_safely", _safe)
    decisions = [
        Decision(type=DecisionType.REPLY, target="user_webui:u1", priority=10),
        Decision(type=DecisionType.PLAN, content="自主规划", priority=5),
    ]
    await cycle_mod.execute_decisions(
        mind, {}, SituationContext(), decisions, is_heartbeat=True,
    )

    assert executed == [DecisionType.REPLY.value, DecisionType.PLAN.value]
    mind.driver.request_cycle.assert_not_called()
    mind.pfc.clear_general_tasks_before.assert_called_once_with(1)


async def test_heartbeat_still_filters_reflect_when_pending(monkeypatch) -> None:
    mind = _fake_mind()
    mind.heartbeat_engine.reflection_pending = True
    scheduled: list[str] = []

    async def _safe(_mind, decision: Decision) -> bool:
        scheduled.append(decision.type.value)
        return True

    monkeypatch.setattr(cycle_mod, "execute_safely", _safe)
    decisions = [
        Decision(type=DecisionType.REFLECT, reason="重复反思", priority=5),
        Decision(type=DecisionType.REPLY, target="user_webui:u1", priority=10),
    ]
    await cycle_mod.execute_decisions(
        mind, {}, SituationContext(), decisions, is_heartbeat=True,
    )
    assert scheduled == [DecisionType.REPLY.value]


class TestMessageCycleToolActionGuard:
    async def test_message_cycle_never_runs_parallel_tool_action(self) -> None:
        """同一用户指令不得同时进入 REPLY 与 TOOL_ACTION 两条工具循环。"""
        mind = _fake_mind()
        situation = SituationContext(pending_messages=[
            PendingMessage(scope="group_wcauto:g1", preview="测试全部消息类型",
                           adapter_key="wcauto"),
        ])
        decisions = [
            Decision(type=DecisionType.REPLY, target="group_wcauto:g1", priority=10),
            Decision(type=DecisionType.TOOL_ACTION, target="group_wcauto:g1",
                     content="测试全部消息类型", priority=9),
        ]
        await cycle_mod.execute_decisions(
            mind, {}, situation, decisions, is_heartbeat=False,
        )
        assert cycle_mod.execute_safely.await_count == 1
        decision = cycle_mod.execute_safely.await_args.args[1]
        assert decision.type == DecisionType.REPLY

    async def test_heartbeat_keeps_autonomous_tool_action(self) -> None:
        """心跳周期不受聊天守卫影响，自主 TOOL_ACTION 仍在主 Driver 内执行。"""
        mind = _fake_mind()
        situation = SituationContext(pending_messages=[
            PendingMessage(scope="user_qq:u1", preview="帮我查一下", adapter_key="qq"),
        ])
        decisions = [
            Decision(type=DecisionType.REPLY, target="user_qq:u1", priority=10),
            Decision(type=DecisionType.TOOL_ACTION, content="自主维护", priority=5),
        ]
        await cycle_mod.execute_decisions(
            mind, {}, situation, decisions, is_heartbeat=True,
        )
        assert cycle_mod.execute_safely.await_count == 2
        executed = [call.args[1].type for call in cycle_mod.execute_safely.await_args_list]
        assert executed == [DecisionType.REPLY, DecisionType.TOOL_ACTION]


@pytest.mark.parametrize("heartbeat", [False, True])
async def test_direct_channel_owns_request_and_preserves_background_work(monkeypatch, heartbeat: bool) -> None:
    from agent.channel.reply_policy import ReplyPolicy

    monkeypatch.setattr("agent.channel.reply_policy.get_reply_policy",
                        lambda adapter: ReplyPolicy(direct_reply=adapter == "game"))
    mind = _fake_mind()
    monkeypatch.setattr(cycle_mod, "gather_situation", AsyncMock(side_effect=AssertionError("must not decide the same game request again")))
    monkeypatch.setattr(cycle_mod, "active_goal_lines", AsyncMock(side_effect=AssertionError("unrelated goals must not delay the request")))
    mind.pfc.peek_all_tasks = lambda: [("group_game:local", "player", "local", "制作工具"),
                                     ("user_webui:other", "other", "", "其他请求")]
    mind.pfc.get_adapter_key = lambda scope: "game" if scope.startswith("group_game:") else "webui"
    mind.pfc.peek_general_tasks = lambda: ["unrelated task"]
    mind.pfc.pending_analysis = ["unrelated profile"]
    mind.pfc.clear_general_tasks_before = Mock()
    await cycle_mod.run_cycle(mind, {}, is_heartbeat=heartbeat)
    assert cycle_mod.execute_safely.await_count == 1
    decision = cycle_mod.execute_safely.await_args.args[1]
    assert decision.type == DecisionType.REPLY and decision.target == "group_game:local"
    mind.pfc.clear_general_tasks_before.assert_called_once_with(0)


class TestSituationSummary:
    def test_pending_message_renders_to_me_and_kind_markers(self) -> None:
        """态势摘要携带对话性质标记：@我 / 平台推送类别，元决策据此判优先级。"""
        ctx = SituationContext(pending_messages=[
            PendingMessage(scope="group_qq:1", preview="帮我查下天气", group_id="1",
                           adapter_key="qq", to_me=True),
            PendingMessage(scope="group_qq:1", preview="大家周末去哪玩", adapter_key="qq"),
            PendingMessage(scope="user_qq:2", preview="张三赞了你的消息", adapter_key="qq",
                           kind="notification"),
        ])
        summary = ctx.to_summary()
        assert "[@我]" in summary
        assert "[notification]" in summary
        assert "大家周末去哪玩" in summary
