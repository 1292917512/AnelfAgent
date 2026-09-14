"""REFLECT 决策登记冷却与态势回执行剥离。

回归背景（2026-09「对话质量下滑」连爆事故）：元决策对同一态势逐拍重复
判定 REFLECT，登记链无冷却、软提示依赖 LLM 自觉被无视；心跳日志中的
反思域决策回执行行回喂元决策态势形成自指循环。本文件锁定两道机械防线。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock

import pytest

from agent.mind import cycle as cycle_mod
from agent.mind.autonomous import Decision, DecisionType
from agent.mind.tools import decision_executor as de


def _fake_mind(*, last_reflect_ts: float, has_idle: bool = True) -> tuple[Any, List[str]]:
    pending: List[str] = []

    def _mark(reason: str) -> None:
        pending.append(reason)

    return SimpleNamespace(
        _last_reflect_time=last_reflect_ts,
        heartbeat_engine=SimpleNamespace(
            has_idle_schedule=lambda: has_idle,
            mark_reflection_pending=_mark,
            reflection_pending=bool(pending),
            run_task=AsyncMock(return_value="反思产出"),
        ),
        _reflecting=False,
        _set_phase=lambda phase: None,
        pfc=SimpleNamespace(has_pending_tasks=lambda: False),
    ), pending


@pytest.fixture(autouse=True)
def _isolate_heartbeat_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(de, "_hb_append", lambda text: None)


class TestReflectCooldown:
    async def test_cooldown_blocks_registration(self) -> None:
        """距上次反思不足冷却窗口：拒绝登记，pending 不置位。"""
        mind, pending = _fake_mind(last_reflect_ts=time.time())
        count = await de.execute_reflect(mind, Decision(type=DecisionType.REFLECT, reason="对话质量下滑"))
        assert count == 0
        assert pending == []

    async def test_cooldown_blocks_immediate_path_too(self) -> None:
        """无 idle 调度的立即执行路径同样受冷却门控（不到达 run_task）。"""
        mind, _ = _fake_mind(last_reflect_ts=time.time(), has_idle=False)
        count = await de.execute_reflect(mind, Decision(type=DecisionType.REFLECT, reason="再次反思"))
        assert count == 0
        mind.heartbeat_engine.run_task.assert_not_awaited()

    async def test_expired_cooldown_registers(self) -> None:
        """冷却窗口已过：正常登记待执行标记。"""
        mind, pending = _fake_mind(last_reflect_ts=time.time() - 40 * 60)
        count = await de.execute_reflect(mind, Decision(type=DecisionType.REFLECT, reason="阶段性回顾"))
        assert count == 0
        assert pending == ["阶段性回顾"]

    async def test_sentinel_anchor_registers(self) -> None:
        """本进程尚未反思过（锚点为 0）：放行一次，对齐哨兵语义。"""
        mind, pending = _fake_mind(last_reflect_ts=0.0)
        await de.execute_reflect(mind, Decision(type=DecisionType.REFLECT, reason="开机首思"))
        assert pending == ["开机首思"]

    async def test_cooldown_disabled_via_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """配置为 0 关闭冷却：冷却窗口内也放行。"""
        mind, pending = _fake_mind(last_reflect_ts=time.time())
        monkeypatch.setattr(de, "_reflection_cooldown_minutes", lambda: 0)
        await de.execute_reflect(mind, Decision(type=DecisionType.REFLECT, reason="无冷却"))
        assert pending == ["无冷却"]


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
