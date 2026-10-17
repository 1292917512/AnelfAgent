"""wcauto 入站解析测试：群/私判定、自己消息过滤、@我识别、引用提取、媒体占位。"""

from __future__ import annotations

from agent.channel.schemas import ChannelType, MessageKind, SegmentType
from channels.wcauto.parser import (
    collect_self_names,
    detect_at_me,
    extract_quote,
    is_self_message,
    parse_message_event,
)


def _msg(**over) -> dict:
    base = {
        "username": "wxid_friend",
        "chat_name": "朋友甲",
        "is_group": False,
        "local_id": 101,
        "shard": "message_0.db",
        "type": "文本",
        "sender_id": 5,
        "sender_username": "wxid_friend",
        "sender_name": "朋友甲",
        "content": "你好",
        "create_time": 1700000000,
        "sort_seq": 999,
    }
    base.update(over)
    return base


class TestSelfFilter:
    def test_sender_id_2_is_self(self) -> None:
        assert is_self_message(_msg(sender_id=2), self_wxid="") is True
        assert parse_message_event(_msg(sender_id=2), self_wxid="") is None

    def test_self_wxid_match(self) -> None:
        assert is_self_message(_msg(sender_username="wxid_me"), "wxid_me") is True
        assert parse_message_event(_msg(sender_username="wxid_me"),
                                   self_wxid="wxid_me") is None

    def test_other_message_passes(self) -> None:
        assert parse_message_event(_msg(), self_wxid="wxid_me") is not None


class TestPrivateMessage:
    def test_private_text(self) -> None:
        msg = parse_message_event(_msg(), self_wxid="wxid_me")
        assert msg is not None
        assert msg.channel.channel_type == ChannelType.PRIVATE
        assert msg.channel.channel_id == "wxid_friend"
        assert msg.channel.channel_name == "朋友甲"
        assert msg.sender.user_id == "wxid_friend"
        assert msg.sender.user_name == "朋友甲"
        assert msg.content == "你好"
        assert msg.is_to_me is True
        assert msg.message_id == "wxid_friend:message_0.db:101"


class TestGroupMessage:
    def _group_msg(self, content: str) -> dict:
        return _msg(username="12345@chatroom", chat_name="测试群", is_group=True,
                    content=content)

    def test_group_channel(self) -> None:
        msg = parse_message_event(self._group_msg("大家好"), self_wxid="me",
                                  require_mention=False)
        assert msg is not None
        assert msg.channel.channel_type == ChannelType.GROUP
        assert msg.is_to_me is True

    def test_require_mention(self) -> None:
        msg = parse_message_event(self._group_msg("大家好"), self_wxid="me",
                                  self_names=["梦璃"], require_mention=True)
        assert msg is not None
        assert msg.is_to_me is False  # 未 @我
        msg2 = parse_message_event(self._group_msg("@梦璃 在吗"), self_wxid="me",
                                   self_names=["梦璃"], require_mention=True)
        assert msg2 is not None and msg2.is_to_me is True

    def test_sender_prefix_stripped(self) -> None:
        raw = self._group_msg("wxid_tom:\n今天天气不错")
        raw["sender_username"] = "wxid_tom"
        msg = parse_message_event(raw, self_wxid="me", require_mention=False)
        assert msg is not None
        assert msg.content == "今天天气不错"


class TestAtDetection:
    def test_detect_at_me(self) -> None:
        assert detect_at_me("@梦璃 看下这个", ["梦璃"]) is True
        assert detect_at_me("@别人 看下", ["梦璃"]) is False
        assert detect_at_me("没有at", ["梦璃"]) is False
        assert detect_at_me("@梦璃", ["", "梦璃"]) is True

    def test_collect_self_names(self) -> None:
        names = collect_self_names("小梦, 梦璃", "梦璃")
        assert names == ["小梦", "梦璃"]  # 桥昵称去重


class TestQuote:
    def test_extract_quote(self) -> None:
        body, quoted = extract_quote("引用 朋友甲 的消息：「昨天的方案」\n我同意")
        assert quoted == "朋友甲: 昨天的方案"
        assert body == "我同意"

    def test_no_quote(self) -> None:
        body, quoted = extract_quote("普通消息")
        assert body == "普通消息" and quoted == ""

    def test_quote_in_message(self) -> None:
        msg = parse_message_event(
            _msg(content="引用 朋友甲 的消息：「方案」\n收到"),
            self_wxid="me")
        assert msg is not None
        assert msg.reply_content == "朋友甲: 方案"
        assert msg.content == "收到"


class TestMediaMessages:
    def test_system_notice_keeps_private_peer_without_claiming_speech(self) -> None:
        msg = parse_message_event(_msg(type="系统消息", sender_username="system", content="你拍了拍朋友甲"))
        assert msg is not None
        assert msg.kind == MessageKind.SYSTEM
        assert msg.sender.user_id == "wxid_friend"
        assert msg.sender.user_name == "微信系统"
        assert msg.channel.channel_id == "wxid_friend"
        assert msg.content == "你拍了拍朋友甲"

    def test_image_placeholder(self) -> None:
        msg = parse_message_event(_msg(type="图片", content=""), self_wxid="me")
        assert msg is not None
        assert msg.segments[0].type == SegmentType.IMAGE
        assert msg.segments[0].file_id == "101"
        assert "[图片]" in msg.content

    def test_voice_placeholder(self) -> None:
        msg = parse_message_event(_msg(type="语音", content=""), self_wxid="me")
        assert msg is not None
        assert msg.segments[0].type == SegmentType.VOICE

    def test_file_placeholder(self) -> None:
        msg = parse_message_event(_msg(type="文件", content=""), self_wxid="me")
        assert msg is not None
        assert msg.segments[0].type == SegmentType.FILE

    def test_unknown_type_placeholder(self) -> None:
        msg = parse_message_event(_msg(type="红包", content=""), self_wxid="me")
        assert msg is not None
        assert "[红包]" in msg.content

    def test_empty_system_dropped(self) -> None:
        assert parse_message_event(_msg(type="系统消息", content=""),
                                   self_wxid="me") is None
