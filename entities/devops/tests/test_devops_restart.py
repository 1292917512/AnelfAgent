"""devops 重启请求守卫单元测试（守护检测 / 重复去重 / 来源透传）。"""
from __future__ import annotations

import pytest

from entities.devops import service


@pytest.fixture(autouse=True)
def _reset_restart_state(monkeypatch: pytest.MonkeyPatch):
    """用例间复位重启挂起旗标，避免全局状态串扰。"""
    monkeypatch.setattr(service, "_restart_pending", False)


def test_request_restart_refused_without_supervisor(monkeypatch: pytest.MonkeyPatch):
    """进程非 start.sh 守护拉起时拒绝重启（防止"重启变关机"）。"""
    monkeypatch.setattr(service, "_is_supervised", lambda: False)
    scheduled: list[float] = []
    monkeypatch.setattr(service, "schedule_restart", lambda delay=1.0: scheduled.append(delay))

    result = service.request_restart(source="tool:restart_app")
    assert result["ok"] is False
    assert result["error"] == "no_supervisor"
    assert scheduled == []


def test_request_restart_dedupes_pending(monkeypatch: pytest.MonkeyPatch):
    """重复请求只调度一次，后续返回 already_pending。"""
    monkeypatch.setattr(service, "_is_supervised", lambda: True)
    scheduled: list[float] = []
    monkeypatch.setattr(service, "schedule_restart", lambda delay=1.0: scheduled.append(delay))

    first = service.request_restart(source="http:127.0.0.1")
    second = service.request_restart(source="tool:restart_app")
    assert first["ok"] and first["restarting"]
    assert second["ok"] and second.get("already_pending") is True
    assert len(scheduled) == 1
