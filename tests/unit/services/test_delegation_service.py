"""DelegationService（全局子代理总览与面板操作）单元测试。"""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

import pytest

from agent.delegation import journal
from services import delegation as delegation_mod
from services.delegation import DelegationService


class _FakeManager:
    """最小 DelegationManager 替身：记录 steer/cancel 调用。"""

    def __init__(self) -> None:
        self.steered: List[Tuple[str, str, str]] = []
        self.cancelled: List[str] = []
        self.running_ids: Set[str] = {"d1"}

    def running_snapshot_all(self) -> List[Dict[str, Any]]:
        return [{"delegation_id": "d1", "goal": "g"}]

    def running_snapshot(self, scope: str) -> List[Dict[str, Any]]:
        return [{"delegation_id": "d1", "scope": scope}]

    def is_running(self, delegation_id: str) -> bool:
        return delegation_id in self.running_ids

    def steer(self, delegation_id: str, message: str, mode: str = "steer") -> Dict[str, Any]:
        self.steered.append((delegation_id, message, mode))
        return {"ok": True, "delegation_id": delegation_id}

    def cancel(self, delegation_id: str) -> bool:
        self.cancelled.append(delegation_id)
        return True


class _FakeRuntime:
    def __init__(self, manager: _FakeManager) -> None:
        self.mind = type("Mind", (), {"delegation_manager": manager})()


@pytest.fixture
def svc_down(monkeypatch: pytest.MonkeyPatch) -> DelegationService:
    """runtime 未就绪路径。"""
    monkeypatch.setattr(delegation_mod, "get_runtime", lambda: None)
    return DelegationService()


@pytest.fixture
def svc_up(monkeypatch: pytest.MonkeyPatch) -> Tuple[DelegationService, _FakeManager]:
    manager = _FakeManager()
    monkeypatch.setattr(delegation_mod, "get_runtime", lambda: _FakeRuntime(manager))
    return DelegationService(), manager


class TestRuntimeDown:
    def test_overview_empty(self, svc_down: DelegationService) -> None:
        assert svc_down.overview() == {"running": []}
        assert svc_down.running_for_scope("user_qq:1") == []

    def test_steer_error(self, svc_down: DelegationService) -> None:
        assert svc_down.steer("d1", "msg") == {"error": "runtime 未就绪"}

    def test_cancel_none(self, svc_down: DelegationService) -> None:
        assert svc_down.cancel("d1") is None

    def test_progress_not_running(self, svc_down: DelegationService) -> None:
        # 会话级共享 delegation 目录，用唯一 id 确保进度流不存在
        result = svc_down.progress("svc-ghost-down")
        assert result["running"] is False
        assert result["lines"] == []


class TestOverview:
    def test_overview_passthrough(self, svc_up: Tuple[DelegationService, _FakeManager]) -> None:
        svc, _manager = svc_up
        assert svc.overview()["running"][0]["delegation_id"] == "d1"
        assert svc.running_for_scope("user_qq:1")[0]["scope"] == "user_qq:1"

    def test_progress_marks_running(
        self, svc_up: Tuple[DelegationService, _FakeManager],
    ) -> None:
        svc, manager = svc_up
        # 会话级共享 delegation 目录，用唯一 id 避免污染其他用例的进度流
        manager.running_ids.add("svc-run-1")
        journal.append_progress("svc-run-1", "第 1 轮开始")
        result = svc.progress("svc-run-1")
        assert result["running"] is True
        assert result["delegation_id"] == "svc-run-1"
        assert any("第 1 轮开始" in line for line in result["lines"])
        assert svc.progress("svc-ghost-up")["running"] is False


class TestSteer:
    def test_message_prefixed_with_source(
        self, svc_up: Tuple[DelegationService, _FakeManager],
    ) -> None:
        svc, manager = svc_up
        result = svc.steer("d1", "顺便检查一下日志", "after")
        assert result.get("ok") is True
        delegation_id, message, mode = manager.steered[0]
        assert delegation_id == "d1" and mode == "after"
        # 来源标注：子代理能分辨面板指令与父 AI 指令
        assert message.startswith("（来自 Web 面板的指令）")
        assert "顺便检查一下日志" in message

    def test_empty_message_rejected(
        self, svc_up: Tuple[DelegationService, _FakeManager],
    ) -> None:
        svc, manager = svc_up
        assert svc.steer("d1", "   ") == {"error": "message 不能为空"}
        assert manager.steered == []


class TestCancel:
    def test_cancel_passthrough(self, svc_up: Tuple[DelegationService, _FakeManager]) -> None:
        svc, manager = svc_up
        assert svc.cancel("d1") is True
        assert manager.cancelled == ["d1"]
