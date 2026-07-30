"""scope 新格式下的目标/参数归一化测试。

回归场景（生产 bug）：
- AI 把 scope 字符串（user_qq:123 / qq:123）当发送目标 → QQ 原生 ID 非法，发送失败
- AI 从 [uid:123] 标签取裸 id 查询对话 → 新键 qq:123 miss，反复查询触发守卫
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.channel.output_tools import _normalize_target_id, _resolve_send_target
from agent.memory.tools import _normalize_scope_id


@pytest.fixture
def fake_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    """注册假频道表，使 adapter 前缀识别生效。"""
    from agent.channel import manager as cm

    monkeypatch.setattr(
        cm,
        "get_channel_manager",
        lambda: SimpleNamespace(list_channels=lambda: {"qq": object(), "webui": object()}),
    )


class TestNormalizeTargetId:
    def test_explicit_type_prefix(self) -> None:
        assert _normalize_target_id("user:123") == ("123", "private")
        assert _normalize_target_id("group:456") == ("456", "group")

    def test_bare_id_passthrough(self, fake_channels) -> None:
        assert _normalize_target_id("1292917512") == ("1292917512", None)

    def test_full_entity_scope_form(self, fake_channels) -> None:
        """完整 entity_scope（AI 从会话上下文复制）→ 剥离为原生 ID。"""
        assert _normalize_target_id("user_qq:1292917512") == ("1292917512", "private")
        assert _normalize_target_id("group_qq:390320020") == ("390320020", "group")

    def test_adapter_prefix_form(self, fake_channels) -> None:
        """scope_id 的 adapter 前缀形式泄漏 → 剥离前缀（本次生产 bug 的输入）。"""
        assert _normalize_target_id("qq:1292917512") == ("1292917512", None)
        assert _normalize_target_id("webui:web_user") == ("web_user", None)

    def test_unknown_colon_form_untouched(self, fake_channels) -> None:
        """非已知频道前缀原样保留（可能是合法的原生 ID 或错误输入，交由频道报错）。"""
        assert _normalize_target_id("unknown:123") == ("unknown:123", None)

    def test_resolve_send_target_end_to_end(self, fake_channels, monkeypatch) -> None:
        """_resolve_send_target 对泄漏形式给出可用的 (原生 ID, 类型)。"""
        from agent.channel import output_tools
        monkeypatch.setattr(output_tools, "_resolve_channel_type", lambda cid, tid: "private")
        target_id, channel_type = _resolve_send_target("qq", "qq:1292917512")
        assert target_id == "1292917512"
        assert channel_type == "private"


class TestNormalizeScopeId:
    def test_bare_id_prefixed_by_current_scope(self) -> None:
        """裸 id 按当前思维会话的 adapter 补全。"""
        from agent.mind.tool_activation import bind_scope, reset_scope

        token = bind_scope("user_qq:1292917512")
        try:
            assert _normalize_scope_id("1292917512") == "qq:1292917512"
        finally:
            reset_scope(token)

    def test_already_prefixed_untouched(self) -> None:
        assert _normalize_scope_id("qq:123") == "qq:123"
        assert _normalize_scope_id("webui:web_user#chat1") == "webui:web_user#chat1"

    def test_no_scope_passthrough(self) -> None:
        """无当前 scope（心跳/后台上下文）：原样返回。"""
        from agent.mind.tool_activation import bind_scope, reset_scope

        token = bind_scope("")
        try:
            assert _normalize_scope_id("123") == "123"
        finally:
            reset_scope(token)

    def test_empty_untouched(self) -> None:
        assert _normalize_scope_id("") == ""
