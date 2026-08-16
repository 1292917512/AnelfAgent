"""NoneBot 桥接线协议父进程侧转换测试（纯函数，不依赖 nonebot）。"""

from __future__ import annotations

from agent.channel.schemas import ChannelType, SegmentType
from channels.nonebot_bridge.wire_in import wire_event_to_adapter_message


def _group_event() -> dict:
    return {
        "type": "event",
        "bot_id": "10001",
        "adapter": "onebot_v11",
        "event_kind": "message",
        "message_id": "67890",
        "session": {"id": "70001", "type": "group"},
        "sender": {"user_id": "20002", "user_name": "小明", "platform": "nb_onebot_v11"},
        "text": "你好 [at_uid:10001]",
        "segments": [
            {"seg": "text", "text": "你好 "},
            {"seg": "at", "user_id": "10001"},
        ],
        "is_to_me": True,
        "reply_to": "12345",
        "timestamp": 1755000000.0,
    }


class TestWireEventConversion:
    """wire_event_to_adapter_message 字段映射。"""

    def test_group_event_fields(self) -> None:
        msg = wire_event_to_adapter_message(_group_event())
        assert msg is not None
        assert msg.message_id == "67890"
        assert msg.channel.channel_id == "70001"
        assert msg.channel.channel_type == ChannelType.GROUP
        assert msg.sender.user_id == "20002"
        assert msg.sender.user_name == "小明"
        assert msg.sender.platform == "nb_onebot_v11"
        assert msg.is_to_me is True
        assert msg.reply_to_id == "12345"
        assert msg.timestamp == 1755000000.0

    def test_private_session_uses_user_id(self) -> None:
        payload = _group_event()
        payload["session"] = {"id": "irrelevant", "type": "private"}
        msg = wire_event_to_adapter_message(payload)
        assert msg is not None
        assert msg.channel.channel_id == "20002"
        assert msg.channel.channel_type == ChannelType.PRIVATE

    def test_at_segment_content(self) -> None:
        msg = wire_event_to_adapter_message(_group_event())
        assert msg is not None
        at_segments = [s for s in msg.segments if s.type == SegmentType.AT]
        assert len(at_segments) == 1
        assert at_segments[0].at_user_id == "10001"
        assert at_segments[0].content == "[at_uid:10001]"

    def test_media_segments_passthrough(self) -> None:
        payload = _group_event()
        payload["segments"] = [
            {"seg": "image", "url": "https://example.com/a.jpg", "file": "", "name": ""},
            {"seg": "file", "url": "", "name": "doc.pdf"},
            {"seg": "voice", "url": "https://example.com/v.silk", "file": ""},
        ]
        msg = wire_event_to_adapter_message(payload)
        assert msg is not None
        types = [s.type for s in msg.segments]
        assert types == [SegmentType.IMAGE, SegmentType.FILE, SegmentType.VOICE]
        assert msg.segments[0].url == "https://example.com/a.jpg"
        assert msg.segments[1].file_name == "doc.pdf"
        assert msg.segments[2].url == "https://example.com/v.silk"

    def test_guild_composite_session_id(self) -> None:
        payload = _group_event()
        payload["session"] = {"id": "90001:90002", "type": "group"}
        msg = wire_event_to_adapter_message(payload)
        assert msg is not None
        assert msg.channel.channel_id == "90001:90002"

    def test_unknown_segment_degrades_to_text(self) -> None:
        payload = _group_event()
        payload["segments"] = [{"seg": "dice", "text": "[dice]"}]
        msg = wire_event_to_adapter_message(payload)
        assert msg is not None
        assert msg.segments[0].type == SegmentType.TEXT
        assert msg.segments[0].content == "[dice]"

    def test_missing_user_returns_none(self) -> None:
        payload = _group_event()
        payload["sender"] = {"user_id": "", "user_name": ""}
        assert wire_event_to_adapter_message(payload) is None

    def test_notice_event(self) -> None:
        payload = {
            "type": "event",
            "bot_id": "10001",
            "adapter": "telegram",
            "event_kind": "notice",
            "message_id": "",
            "session": {"id": "20002", "type": "private"},
            "sender": {"user_id": "system", "user_name": "", "platform": "nb_telegram"},
            "text": "(poke)",
            "segments": [{"seg": "text", "text": "(poke)"}],
            "is_to_me": True,
            "reply_to": "",
            "timestamp": 0,
        }
        msg = wire_event_to_adapter_message(payload)
        assert msg is not None
        assert msg.content == "(poke)"
        assert msg.is_to_me is True
        assert msg.timestamp > 0  # 0 视为无效回退当前时间
