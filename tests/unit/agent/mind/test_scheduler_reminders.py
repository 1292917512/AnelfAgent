"""定时提醒（schedule_reminder）单元测试。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import pytest

from agent.mind.tools import scheduler
from agent.mind.tools.ports import mind_port


def _wire_mind(mind: object) -> None:
    """将 fake mind 施绑到端口（端口类型为 Mind，测试替身经此注入）。"""
    mind_port.set(mind)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reminders_file(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "reminders.json"
    monkeypatch.setattr(scheduler, "_reminders_path", lambda: path)
    return path


def test_parse_run_at_full_datetime() -> None:
    ts = scheduler._parse_run_at("2026-07-21 08:00")
    assert ts == datetime(2026, 7, 21, 8, 0).timestamp()


def test_parse_run_at_hm_only_rolls_to_tomorrow() -> None:
    past = (datetime.now() - timedelta(hours=1)).strftime("%H:%M")
    ts = scheduler._parse_run_at(past)
    assert ts is not None
    assert ts > time.time()
    assert 80000 < ts - time.time() < 86400  # 约为 23 小时后


def test_parse_run_at_invalid() -> None:
    assert scheduler._parse_run_at("明天早上") is None
    assert scheduler._parse_run_at("") is None


@pytest.mark.asyncio
async def test_schedule_reminder_persists_and_lists() -> None:
    class FakePFC:
        pass

    class FakeMind:
        pfc = FakePFC()
        _active_scopes = {"user_qq:123"}
        _reply_adapter_key = "qq"

    _wire_mind(FakeMind())
    try:
        result = json.loads(await scheduler.schedule_reminder(
            note="搜索比分并告诉主人", run_at="2099-01-01 08:00",
        ))
        assert result["ok"] is True
        assert result["scope"] == "user_qq:123"

        listed = json.loads(await scheduler.list_reminders())
        assert listed["total"] == 1
        assert listed["reminders"][0]["note"] == "搜索比分并告诉主人"

        rid = result["reminder_id"]
        cancelled = json.loads(await scheduler.cancel_reminder(rid))
        assert cancelled["ok"] is True
        assert json.loads(await scheduler.list_reminders())["total"] == 0
    finally:
        mind_port.unbind()


@pytest.mark.asyncio
async def test_schedule_reminder_rejects_past_time() -> None:
    class FakePFC:
        pass

    class FakeMind:
        pfc = FakePFC()
        _active_scopes = {"user_qq:123"}
        _reply_adapter_key = ""

    _wire_mind(FakeMind())
    try:
        result = json.loads(await scheduler.schedule_reminder(
            note="过去的时间", run_at="2020-01-01 08:00",
        ))
        assert "error" in result

        result = json.loads(await scheduler.schedule_reminder(note="无时间"))
        assert "error" in result
    finally:
        mind_port.unbind()


@pytest.mark.asyncio
async def test_schedule_reminder_explicit_scope_without_conversation() -> None:
    """任务/反思上下文（无活跃会话）经显式 scope 设定提醒。

    回归 2026-09-15 晨报事故：旧实现回退遍历 _active_scopes（仅回复周期
    进行中非空），深夜无会话时非确定性地报「无法确定回复目标」。
    """
    class FakePFC:
        def get_adapter_key(self, scope: str) -> str:
            return ""

    class FakeMind:
        pfc = FakePFC()
        _active_scopes: set = set()

    _wire_mind(FakeMind())
    try:
        result = json.loads(await scheduler.schedule_reminder(
            note="晨报", run_at="2099-01-01 08:00", scope="user_qq:1292917512",
        ))
        assert result["ok"] is True
        assert result["scope"] == "user_qq:1292917512"
        # 无 PFC 登记时投递频道从 scope 的 adapter 段派生
        assert result["channel"] == "qq"

        listed = json.loads(await scheduler.list_reminders())
        assert listed["reminders"][0]["scope"] == "user_qq:1292917512"
    finally:
        mind_port.unbind()


@pytest.mark.asyncio
async def test_schedule_reminder_invalid_scope_rejected() -> None:
    """显式 scope 非法（非会话域）报 PARAM 错误，不静默回退上下文推断。"""
    class FakePFC:
        pass

    class FakeMind:
        pfc = FakePFC()
        _active_scopes = {"user_123"}

    _wire_mind(FakeMind())
    try:
        for bad in ("reflect:task001", "_global", "随便写的"):
            result = json.loads(await scheduler.schedule_reminder(
                note="x", run_at="2099-01-01 08:00", scope=bad,
            ))
            assert result["cause"] == "param", bad
    finally:
        mind_port.unbind()


@pytest.mark.asyncio
async def test_schedule_reminder_no_target_reports_clearly() -> None:
    """无显式 scope 且上下文推不出目标：明确报错并提示显式传参。"""
    class FakePFC:
        pass

    class FakeMind:
        pfc = FakePFC()
        _active_scopes: set = set()

    _wire_mind(FakeMind())
    try:
        result = json.loads(await scheduler.schedule_reminder(
            note="x", run_at="2099-01-01 08:00",
        ))
        assert result["cause"] == "state"
        assert "scope" in result["hint"]
    finally:
        mind_port.unbind()
