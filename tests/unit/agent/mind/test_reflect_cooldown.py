"""按需反思的异常收束与历史回执去重。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.mind import cycle as cycle_mod
from agent.mind import decisions as de
from agent.mind.autonomous import Decision, DecisionType


@pytest.mark.parametrize("outcome", ["done", RuntimeError("failed"), asyncio.CancelledError()])
async def test_reflect_runs_inline_with_reason_and_releases_state(outcome):
    run = AsyncMock(side_effect=outcome if isinstance(outcome, BaseException) else None,
                    return_value="done")
    mind = SimpleNamespace(heartbeat_engine=SimpleNamespace(run_task=run), _reflecting=False,
                           set_phase=lambda phase: None, pfc=SimpleNamespace(has_pending_tasks=lambda: False))
    decision = Decision(type=DecisionType.REFLECT, reason="有新证据")
    if isinstance(outcome, BaseException):
        with pytest.raises(type(outcome)):
            await de.execute_reflect(mind, decision)
    else:
        assert await de.execute_reflect(mind, decision) == 1
    assert not mind._reflecting
    assert run.call_args.kwargs == {"trigger": "autonomous", "extra_note": "\n\n[反思触发原因]\n有新证据"}
    mind._reflecting = True
    assert await de.execute_reflect(mind, decision) == 0
    assert run.await_count == 1


class TestDecisionEchoStrip:
    def test_reflect_echo_lines_stripped(self) -> None:
        """反思域回执行行被剥离，维护议程行保留。"""
        text = "\n".join([
            "### 2026-09-14 22:21:05 心跳",
            "- 态势：0 条消息, 0 个活跃目标",
            "- 反思已登记，待空闲心跳执行: 对话质量下滑",
            "- 反思完成: 有产出",
            "- 反思登记被冷却拒绝: 距上次反思 5 分钟 < 冷却 30 分钟，原因丢弃 - 对话质量下滑",
            "- [技能治理议程] 库容 58/100",
        ])
        stripped = cycle_mod._strip_decision_echo(text)
        assert "反思已登记" not in stripped
        assert "反思完成" not in stripped
        assert "反思登记被冷却拒绝" not in stripped
        assert "[技能治理议程]" in stripped
        assert "### 2026-09-14 22:21:05 心跳" in stripped

    def test_no_echo_lines_unchanged(self) -> None:
        text = "- 态势：0 条消息\n- [图谱治理议程] 歧义 10"
        assert cycle_mod._strip_decision_echo(text) == text
