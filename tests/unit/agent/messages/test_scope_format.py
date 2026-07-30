"""scope 格式（agent/messages/everything.py）单元测试：adapter 维度的新格式与兼容解析。"""

from __future__ import annotations

from agent.messages import (
    Everything,
    EverythingGroup,
    build_entity_scope,
    build_scope_id,
    parse_entity_scope,
)
from agent.messages.presets import MessageGroupUser, MessageUser


class TestScopeFormat:
    def test_user_scope_with_adapter(self) -> None:
        msg = MessageUser(uid="123", adapter_key="qq")
        assert msg.scope_id == "qq:123"
        assert msg.entity_scope == "user_qq:123"

    def test_group_scope_with_adapter(self) -> None:
        msg = MessageGroupUser(uid="1", group_id="456", adapter_key="qq")
        assert msg.scope_id == "qq:456"
        assert msg.entity_scope == "group_qq:456"

    def test_sub_session_with_adapter(self) -> None:
        msg = MessageUser(uid="web_user", adapter_key="webui", session_id="chat1")
        assert msg.entity_scope == "user_webui:web_user#chat1"

    def test_no_adapter_fallback(self) -> None:
        """adapter_key 缺失时退化为裸 base id（旧格式，兜底路径）。"""
        assert Everything(uid=7).entity_scope == "user_7"
        assert EverythingGroup(uid=1, group_id=8).entity_scope == "group_8"

    def test_cross_channel_same_uid_isolated(self) -> None:
        """跨频道同号实体产生不同 scope（核心隔离目标）。"""
        qq = MessageUser(uid="123", adapter_key="qq")
        web = MessageUser(uid="123", adapter_key="webui")
        assert qq.entity_scope != web.entity_scope


class TestBuildAndParse:
    def test_build_entity_scope(self) -> None:
        assert build_entity_scope("user", "qq", "123") == "user_qq:123"
        assert build_entity_scope("group", "qq", "456") == "group_qq:456"
        assert build_entity_scope("user", "webui", "web_user", "chat1") == "user_webui:web_user#chat1"
        # session 与 base 相同（自然会话）不拼后缀
        assert build_entity_scope("user", "qq", "123", "123") == "user_qq:123"

    def test_build_scope_id(self) -> None:
        assert build_scope_id("qq", "123") == "qq:123"
        assert build_scope_id("", "123") == "123"
        assert build_scope_id("webui", "web_user", "#c1") == "webui:web_user#c1"

    def test_parse_new_format(self) -> None:
        assert parse_entity_scope("user_qq:123") == ("user", "qq", "123", "")
        assert parse_entity_scope("group_qq:456") == ("group", "qq", "456", "")
        assert parse_entity_scope("user_webui:web_user#chat1") == ("user", "webui", "web_user", "chat1")

    def test_parse_legacy_format(self) -> None:
        """旧格式（无 adapter 段）兼容解析，adapter 返回空串。"""
        assert parse_entity_scope("user_123") == ("user", "", "123", "")
        assert parse_entity_scope("group_456#s") == ("group", "", "456", "s")

    def test_parse_invalid(self) -> None:
        assert parse_entity_scope("") == ("", "", "", "")
        assert parse_entity_scope("invalid") == ("", "", "", "")
        assert parse_entity_scope("admin_123") == ("", "", "", "")

    def test_build_parse_roundtrip(self) -> None:
        scope = build_entity_scope("group", "telegram", "789", "topic1")
        assert parse_entity_scope(scope) == ("group", "telegram", "789", "topic1")
