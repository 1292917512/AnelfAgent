"""entities._sdk.push_notify 桥接单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from entities import _sdk


class _FakeHub:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def push(self, scope, source, content, channel="", trigger=True):
        self.calls.append((scope, source, content, channel, trigger))
        return True


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> _FakeHub:
    fake = _FakeHub()
    runtime = SimpleNamespace(mind=SimpleNamespace(push_hub=fake))
    from agent.runtime import singleton
    monkeypatch.setattr(singleton, "get_runtime", lambda: runtime)
    return fake


def test_push_notify_delegates_to_hub(hub: _FakeHub) -> None:
    ok = _sdk.push_notify("声纹库更新完成", "voiceprint", scope="user_webui:u1", channel="webui")
    assert ok is True
    assert hub.calls == [("user_webui:u1", "voiceprint", "声纹库更新完成", "webui", True)]


def test_push_notify_auto_scope_from_context(
        hub: _FakeHub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_sdk, "get_current_scope", lambda: "user_qq:42")
    ok = _sdk.push_notify("内容", "devops", trigger=False)
    assert ok is True
    assert hub.calls[0][0] == "user_qq:42"
    assert hub.calls[0][4] is False


def test_push_notify_global_scope_becomes_empty(
        hub: _FakeHub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_sdk, "get_current_scope", lambda: "_global")
    assert _sdk.push_notify("内容", "watcher") is True
    assert hub.calls[0][0] == ""


def test_push_notify_empty_content_rejected(hub: _FakeHub) -> None:
    assert _sdk.push_notify("  ", "voiceprint", scope="user_webui:u1") is False
    assert hub.calls == []


def test_push_notify_system_down_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.runtime import singleton

    def _raise():
        raise RuntimeError("AgentRuntime 尚未初始化")

    monkeypatch.setattr(singleton, "get_runtime", _raise)
    assert _sdk.push_notify("内容", "voiceprint", scope="user_webui:u1") is False
