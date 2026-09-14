"""出站哨兵（agent.channel.outbound_guard）单元测试。

覆盖：反思上下文拦截规则 / 回复与系统上下文豁免 / 近期窗口判定 /
同一思维链放行 / 配置开关与窗口热调。
"""

from __future__ import annotations

import json

import pytest

import agent.channel.outbound_guard as outbound_guard
from agent.channel.outbound_guard import (
    bind_reply_scopes,
    guard_outbound,
    note_outbound,
)

_SCOPE = "user_qq:1292917512"
_REFLECT_A = "reflect:aaaa1111"
_REFLECT_B = "reflect:bbbb2222"
_REPLY = "user_qq:1292917512"  # 回复会话的 thinker 就是会话 scope 自身


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    """每个用例独立状态 + 默认配置（enabled=true, window=180s）。"""
    outbound_guard._recent.clear()
    monkeypatch.setattr(outbound_guard, "_reply_scopes_provider", None)
    monkeypatch.setattr(outbound_guard, "get_config_bool", lambda key, default: True)
    monkeypatch.setattr(outbound_guard, "get_config_int", lambda key, default: 180)
    yield
    outbound_guard._recent.clear()


def _set_reply_active(active: bool) -> None:
    scopes = frozenset({_SCOPE}) if active else frozenset()
    bind_reply_scopes(lambda: scopes)


class TestExemptions:
    def test_reply_context_exempt_even_with_active_reply(self) -> None:
        _set_reply_active(True)
        note_outbound(_SCOPE, _REFLECT_A, "刚已代答")
        assert guard_outbound(_SCOPE, _REPLY) == ""

    def test_system_context_exempt(self) -> None:
        _set_reply_active(True)
        note_outbound(_SCOPE, _REFLECT_A, "刚已代答")
        assert guard_outbound(_SCOPE, "_global") == ""
        assert guard_outbound(_SCOPE, "") == ""

    def test_disabled_config_allows_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(outbound_guard, "get_config_bool", lambda key, default: False)
        _set_reply_active(True)
        assert guard_outbound(_SCOPE, _REFLECT_A) == ""


class TestReplyActive:
    def test_reflect_rejected_when_reply_in_flight(self) -> None:
        _set_reply_active(True)
        rejection = guard_outbound(_SCOPE, _REFLECT_A)
        payload = json.loads(rejection)
        assert payload["success"] is False
        assert payload["guard"] == "reply_active"
        assert payload["retryable"] is False

    def test_other_scope_not_blocked(self) -> None:
        _set_reply_active(True)
        assert guard_outbound("group_qq:1104224649", _REFLECT_A) == ""


class TestRecentOutbound:
    def test_recent_other_thinker_rejected_with_preview(self) -> None:
        note_outbound(_SCOPE, _REFLECT_B, "查清楚了，答案是 SenseVoice 本机服务")
        rejection = guard_outbound(_SCOPE, _REFLECT_A)
        payload = json.loads(rejection)
        assert payload["guard"] == "recent_outbound"
        assert "SenseVoice" in payload["recent_preview"]

    def test_same_thinker_repeat_allowed(self) -> None:
        note_outbound(_SCOPE, _REFLECT_A, "第一段")
        assert guard_outbound(_SCOPE, _REFLECT_A) == ""

    def test_stale_record_outside_window(self) -> None:
        import time

        note_outbound(_SCOPE, _REFLECT_B, "旧消息")
        # 人工把记录时间拨回窗口之外
        queue = outbound_guard._recent[_SCOPE]
        queue.append(queue.pop()._replace(ts=time.time() - 999))
        assert guard_outbound(_SCOPE, _REFLECT_A) == ""

    def test_window_zero_disables_recent_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(outbound_guard, "get_config_int", lambda key, default: 0)
        note_outbound(_SCOPE, _REFLECT_B, "刚发的")
        assert guard_outbound(_SCOPE, _REFLECT_A) == ""

    def test_reply_active_still_blocks_with_window_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(outbound_guard, "get_config_int", lambda key, default: 0)
        _set_reply_active(True)
        payload = json.loads(guard_outbound(_SCOPE, _REFLECT_A))
        assert payload["guard"] == "reply_active"


class TestScopeTracking:
    def test_scopes_are_isolated(self) -> None:
        note_outbound(_SCOPE, _REFLECT_B, "私聊已答")
        assert guard_outbound("group_qq:1104224649", _REFLECT_A) == ""

    def test_provider_failure_fails_open(self) -> None:
        def _boom() -> frozenset[str]:
            raise RuntimeError("boom")

        bind_reply_scopes(_boom)
        assert guard_outbound(_SCOPE, _REFLECT_A) == ""
