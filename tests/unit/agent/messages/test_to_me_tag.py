"""[to_me:1] 标记渲染（agent/messages）单元测试。

群消息仅在 @ 机器人时渲染 [to_me:1] 标记，AI 据此区分"对她说的话"与群员闲聊；
私聊消息默认都是对机器人说，不渲染该标记。
"""

from __future__ import annotations

from agent.messages.presets import MessageGroupUser, MessageUser


class TestToMeTagRendering:
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
