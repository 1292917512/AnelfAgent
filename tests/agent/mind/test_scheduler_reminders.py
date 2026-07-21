"""定时提醒（schedule_reminder）单元测试。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import pytest

from agent.mind.tools import scheduler


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
        _active_scopes = {"user_123"}
        _reply_adapter_key = "qq"

    scheduler.set_mind(FakeMind())
    try:
        result = json.loads(await scheduler.schedule_reminder(
            note="搜索比分并告诉主人", run_at="2099-01-01 08:00",
        ))
        assert result["ok"] is True
        assert result["scope"] == "user_123"

        listed = json.loads(await scheduler.list_reminders())
        assert listed["total"] == 1
        assert listed["reminders"][0]["note"] == "搜索比分并告诉主人"

        rid = result["reminder_id"]
        cancelled = json.loads(await scheduler.cancel_reminder(rid))
        assert cancelled["ok"] is True
        assert json.loads(await scheduler.list_reminders())["total"] == 0
    finally:
        scheduler.set_mind(None)


@pytest.mark.asyncio
async def test_schedule_reminder_rejects_past_time() -> None:
    class FakePFC:
        pass

    class FakeMind:
        pfc = FakePFC()
        _active_scopes = {"user_123"}
        _reply_adapter_key = ""

    scheduler.set_mind(FakeMind())
    try:
        result = json.loads(await scheduler.schedule_reminder(
            note="过去的时间", run_at="2020-01-01 08:00",
        ))
        assert "error" in result

        result = json.loads(await scheduler.schedule_reminder(note="无时间"))
        assert "error" in result
    finally:
        scheduler.set_mind(None)
