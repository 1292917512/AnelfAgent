"""agent/messages 单元测试：scope 格式（adapter 维度与兼容解析）+ [to_me:1] 标记渲染。"""

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


class TestToMeTagRendering:
    """群消息仅 @ 机器人才渲染 [to_me:1]（AI 据此区分"对她说的话"与群员闲聊）；私聊默认即对机器人说。"""

    def test_group_message_at_bot_renders_marker(self) -> None:
        msg = MessageGroupUser(uid="1", group_id="456", adapter_key="qq", to_me=True)
        assert "[to_me:1]" in str(msg)

    def test_group_message_not_at_bot_no_marker(self) -> None:
        msg = MessageGroupUser(uid="1", group_id="456", adapter_key="qq", to_me=False)
        assert "to_me" not in str(msg)

    def test_private_message_no_marker(self) -> None:
        """私聊无 to_me 字段诉求：默认即对机器人说话，无需标记。"""
        msg = MessageUser(uid="123", adapter_key="qq")
        assert "to_me" not in str(msg)

    def test_marker_coexists_with_arrival_tags(self) -> None:
        """标记与到达标签共存于元数据前缀，不影响真用户消息判定所依赖的标签。"""
        msg = MessageGroupUser(uid="1", group_id="456", adapter_key="qq", to_me=True, user_name="小明")
        text = str(msg)
        assert text.startswith("[time:")
        assert "[to_me:1]" in text and "[uid:1]" in text and "[group_id:456]" in text
