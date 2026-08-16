"""entities/_sdk 桥接层单元测试：工具参数提取 + push_notify 推送。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from entities import _sdk
from entities._sdk import _extract_params

# ------------------------------------------------------------------
# _extract_params（复现历史 bug：**kwargs 容错参数曾生成虚假 schema 字段）
# ------------------------------------------------------------------

def _tool_with_kwargs(image_path: str = "", prompt: str = "", **kwargs: str) -> str:
    """示例工具。

    Args:
        image_path: 图片路径
        prompt: 提示词
    """
    return ""


def _tool_with_args(first: str, *args: str, flag: bool = False) -> str:
    """示例工具。

    Args:
        first: 首个参数
        flag: 开关
    """
    return ""


class TestExtractParams:
    def test_skips_var_keyword(self) -> None:
        params = _extract_params(_tool_with_kwargs)
        names = [p.name for p in params]
        assert names == ["image_path", "prompt"]
        assert all(not p.required for p in params)

    def test_skips_var_positional(self) -> None:
        params = _extract_params(_tool_with_args)
        names = [p.name for p in params]
        assert names == ["first", "flag"]
        assert params[0].required is True
        assert params[1].required is False


# ------------------------------------------------------------------
# push_notify 桥接
# ------------------------------------------------------------------

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


class TestPushNotify:
    def test_delegates_to_hub(self, hub: _FakeHub) -> None:
        ok = _sdk.push_notify("声纹库更新完成", "voiceprint", scope="user_webui:u1", channel="webui")
        assert ok is True
        assert hub.calls == [("user_webui:u1", "voiceprint", "声纹库更新完成", "webui", True)]

    def test_auto_scope_from_context(self, hub: _FakeHub, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_sdk, "get_current_scope", lambda: "user_qq:42")
        ok = _sdk.push_notify("内容", "devops", trigger=False)
        assert ok is True
        assert hub.calls[0][0] == "user_qq:42"
        assert hub.calls[0][4] is False

    def test_global_scope_becomes_empty(self, hub: _FakeHub, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_sdk, "get_current_scope", lambda: "_global")
        assert _sdk.push_notify("内容", "watcher") is True
        assert hub.calls[0][0] == ""

    def test_empty_content_rejected(self, hub: _FakeHub) -> None:
        assert _sdk.push_notify("  ", "voiceprint", scope="user_webui:u1") is False
        assert hub.calls == []

    def test_system_down_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent.runtime import singleton

        def _raise():
            raise RuntimeError("AgentRuntime 尚未初始化")

        monkeypatch.setattr(singleton, "get_runtime", _raise)
        assert _sdk.push_notify("内容", "voiceprint", scope="user_webui:u1") is False
