"""出站事实面（agent.channel.outbound_guard）单元测试。

覆盖：执行期安全边界（reply_active 硬拦截 / 本会话回复豁免 / 配置开关）/
出站事实注入渲染（render_outbound_facts：他链事实呈现、自身行过滤、窗口
过期、在飞回复提示、注入开关）。
"""

from __future__ import annotations

import json

import pytest

import agent.channel.outbound_guard as outbound_guard
from agent.channel.outbound_guard import (
    bind_reply_scopes,
    guard_outbound,
    note_outbound,
    render_outbound_facts,
)

_SCOPE = "user_qq:1292917512"
_OTHER_SCOPE = "group_qq:1104224649"
_REFLECT_A = "reflect:aaaa1111"
_REFLECT_B = "reflect:bbbb2222"
_REPLY = "user_qq:1292917512"  # 回复会话的 thinker 就是会话 scope 自身


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    """每个用例独立状态 + 默认配置（enabled=true, window=180s, inject=true）。"""
    outbound_guard._recent.clear()
    monkeypatch.setattr(outbound_guard, "_reply_scopes_provider", None)
    monkeypatch.setattr(outbound_guard, "get_config_bool", lambda key, default: True)
    monkeypatch.setattr(outbound_guard, "get_config_int", lambda key, default: 180)
    yield
    outbound_guard._recent.clear()


def _set_reply_active(active: bool) -> None:
    scopes = frozenset({_SCOPE}) if active else frozenset()
    bind_reply_scopes(lambda: scopes)


class TestSafetyBoundary:
    """执行期安全边界：仅 reply_active 硬拦截，其余一律放行（决策面在注入层）。"""

    def test_reflect_rejected_when_reply_in_flight(self) -> None:
        _set_reply_active(True)
        payload = json.loads(guard_outbound(_SCOPE, _REFLECT_A))
        assert payload["success"] is False
        assert payload["guard"] == "reply_active"
        assert payload["retryable"] is False

    def test_owner_reply_exempt(self) -> None:
        """本会话回复（thinker == target_scope）豁免——回复周期是会话所有者。"""
        _set_reply_active(True)
        assert guard_outbound(_SCOPE, _REPLY) == ""

    def test_recent_outbound_no_longer_blocks(self) -> None:
        """窗口内其他链刚出站不再拦截——决策面交给上下文注入。"""
        note_outbound(_SCOPE, _REFLECT_B, "刚已代答的内容")
        assert guard_outbound(_SCOPE, _REFLECT_A) == ""

    def test_cross_scope_reply_passes_without_active_reply(self) -> None:
        """跨会话投递在无 reply_active 时放行。"""
        assert guard_outbound(_OTHER_SCOPE, _REPLY) == ""

    def test_other_scope_not_blocked(self) -> None:
        _set_reply_active(True)
        assert guard_outbound(_OTHER_SCOPE, _REFLECT_A) == ""

    def test_disabled_config_allows_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(outbound_guard, "get_config_bool", lambda key, default: False)
        _set_reply_active(True)
        assert guard_outbound(_SCOPE, _REFLECT_A) == ""

    def test_provider_failure_fails_open(self) -> None:
        def _boom() -> frozenset[str]:
            raise RuntimeError("boom")

        bind_reply_scopes(_boom)
        assert guard_outbound(_SCOPE, _REFLECT_A) == ""


class TestFactsInjection:
    """render_outbound_facts：跨会话出站事实的动态注入渲染。"""

    def test_renders_other_scope_facts(self) -> None:
        note_outbound(_SCOPE, _REFLECT_B, "查清楚了，答案是 SenseVoice 本机服务")
        block = render_outbound_facts(_REFLECT_A)
        assert "[出站事实]" in block
        assert _SCOPE in block
        assert "SenseVoice" in block

    def test_own_scope_lines_filtered(self) -> None:
        """本会话（target_scope == thinker）的投递已固化历史，注入是冗余——不呈现。"""
        note_outbound(_SCOPE, _REPLY, "我刚向本会话投递的答复")
        assert render_outbound_facts(_REPLY) == ""

    def test_own_scope_still_shows_other_scopes(self) -> None:
        """本会话行被过滤，但其他会话的他链事实仍呈现。"""
        note_outbound(_SCOPE, _REPLY, "我向本会话投的")
        note_outbound(_OTHER_SCOPE, _REFLECT_B, "反思链向群里投的")
        block = render_outbound_facts(_REPLY)
        assert _SCOPE not in block
        assert _OTHER_SCOPE in block

    def test_filters_self_thinker_lines(self) -> None:
        """自身发出的行不呈现（自己知道自己在做什么）。"""
        note_outbound(_OTHER_SCOPE, _REFLECT_A, "我自己刚发的内容")
        assert render_outbound_facts(_REFLECT_A) == ""

    def test_presents_cross_scope_facts(self) -> None:
        """跨会话投递的事实对其他链可见。"""
        note_outbound(_OTHER_SCOPE, _REFLECT_B, "群里刚交付的答复")
        block = render_outbound_facts(_REFLECT_A)
        assert _OTHER_SCOPE in block
        assert "群里刚交付的答复" in block

    def test_window_expired_records_disappear(self) -> None:
        import time

        note_outbound(_SCOPE, _REFLECT_B, "旧消息")
        queue = outbound_guard._recent[_SCOPE]
        queue.append(queue.pop()._replace(ts=time.time() - 999))
        assert render_outbound_facts(_REFLECT_A) == ""

    def test_window_zero_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(outbound_guard, "get_config_int", lambda key, default: 0)
        note_outbound(_SCOPE, _REFLECT_B, "刚发的")
        assert render_outbound_facts(_REFLECT_A) == ""

    def test_inject_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            outbound_guard, "get_config_bool",
            lambda key, default: key != "outbound_facts_inject",
        )
        note_outbound(_SCOPE, _REFLECT_B, "刚发的")
        assert render_outbound_facts(_REFLECT_A) == ""

    def test_empty_registry_returns_empty(self) -> None:
        assert render_outbound_facts(_REFLECT_A) == ""

    def test_active_reply_hint_appended(self) -> None:
        note_outbound(_SCOPE, _REFLECT_B, "刚发的内容")
        _set_reply_active(True)
        block = render_outbound_facts(_REFLECT_A)
        assert "[回复在飞]" in block
        assert _SCOPE in block

    def test_active_reply_excludes_own_scope(self) -> None:
        """在飞提示不含 thinker 自身会话（自己就是所有者，无需提示）。"""
        note_outbound(_OTHER_SCOPE, _REFLECT_B, "群里的内容")
        bind_reply_scopes(lambda: frozenset({_SCOPE}))
        block = render_outbound_facts(_REPLY)
        assert "[回复在飞]" not in block

    def test_active_reply_alone_renders_without_lines(self) -> None:
        """无登记行但有在飞回复时仍呈现（在飞提示本身即事实）。"""
        bind_reply_scopes(lambda: frozenset({_SCOPE}))
        block = render_outbound_facts(_REFLECT_A)
        assert "[回复在飞]" in block
        assert _SCOPE in block

    def test_reply_context_sees_cross_scope_facts(self) -> None:
        """回复周期（thinker 为会话 scope）看到其他会话的他链事实。"""
        note_outbound(_OTHER_SCOPE, _REFLECT_B, "反思链刚向群里发的内容")
        block = render_outbound_facts(_REPLY)
        assert _OTHER_SCOPE in block


class TestProviderRegistration:
    """context provider 注册：outbound_facts 挂入注册表供 think_loop 每轮收集。"""

    def test_provider_registered(self) -> None:
        from core.context_provider import ContextProviderRegistry

        meta = ContextProviderRegistry._providers.get("outbound_facts")
        assert meta is not None
        assert meta.provide_fn is render_outbound_facts
        assert meta.inject_key == "outbound_facts_inject"

    def test_provider_reachable_via_registry(self) -> None:
        from core.context_provider import ContextProviderRegistry

        note_outbound(_SCOPE, _REFLECT_B, "刚投递的内容")
        meta = ContextProviderRegistry._providers["outbound_facts"]
        block = meta.provide_fn(_REFLECT_A)
        assert "刚投递的内容" in block
