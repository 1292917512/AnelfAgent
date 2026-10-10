"""运维操作的互斥、状态和失败路径。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from entities.devops import service


@pytest.fixture(autouse=True)
def isolated_operations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(service, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(service, "_restart_pending", False)
    monkeypatch.setattr(service, "_build_state", {
        "building": False, "phase": "idle", "last": None, "result": None, "operation_id": None,
    })
    monkeypatch.setattr(service, "_is_supervised", lambda: True)
    monkeypatch.setattr(service, "schedule_restart", Mock())


async def successful_build() -> None:
    service._build_state["last"] = {"ok": True, "log_tail": "done"}


def test_background_claim_precedes_scheduling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_run_build", successful_build)

    async def run() -> None:
        first = service.start_build_and_restart()
        second = service.start_build_and_restart(update=True)
        assert first["ok"] and first["runtime_id"] and first["operation_id"]
        assert second == {"ok": False, "error": "build_in_progress"}
        assert service.request_restart()["error"] == "build_in_progress"
        assert service.git_pull()["error"] == "build_in_progress"
        await asyncio.gather(*service._background_tasks)
        state = service.get_build_state()
        assert state["phase"] == "restarting"
        assert state["result"]["ok"] and not state["building"]

    asyncio.run(run())


def test_build_success_preserves_restart_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_run_build", successful_build)
    monkeypatch.setattr(service, "_is_supervised", lambda: False)
    result = asyncio.run(service.build_and_restart())
    assert result["error"] == "no_supervisor"
    state = service.get_build_state()
    assert state["last"]["ok"] and not state["result"]["ok"]
    assert not state["building"] and state["phase"] == "failed"


def test_update_failure_does_not_build_or_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    build = AsyncMock()
    monkeypatch.setattr(service, "_run_build", build)
    monkeypatch.setattr(service, "_git_pull", lambda: {"ok": False, "error": "dirty_workspace"})
    result = asyncio.run(service.build_and_restart(update=True))
    assert result["error"] == "dirty_workspace"
    build.assert_not_called()
    assert not service._restart_pending
    assert service.get_build_state()["result"] == result


def test_build_cancellation_releases_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cancel() -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(service, "_run_build", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.build_and_restart())
    assert not service.get_build_state()["building"]
    assert service.get_build_state()["result"]["error"] == "cancelled"
    assert not service._restart_pending


def test_git_status_failure_never_pulls(monkeypatch: pytest.MonkeyPatch) -> None:
    git = Mock(return_value={"ok": False, "stdout": "", "stderr": "unavailable"})
    monkeypatch.setattr(service, "_git", git)
    assert service.git_pull()["error"] == "status_failed"
    git.assert_called_once_with(["status", "--porcelain"], timeout=15)
    assert not service.get_build_state()["building"]


def test_pull_network_failure_is_not_a_merge_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    results: list[dict[str, Any]] = [
        {"ok": True, "stdout": "", "stderr": ""},
        {"ok": False, "stdout": "", "stderr": "network timeout"},
    ]
    monkeypatch.setattr(service, "_git", Mock(side_effect=results))
    result = service.git_pull()
    assert result["error"] == "pull_failed" and not result["conflict"]
    assert result["detail"] == "network timeout"
