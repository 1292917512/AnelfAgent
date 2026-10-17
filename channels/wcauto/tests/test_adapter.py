"""wcauto 频道适配器事件流测试：消息分发/策略过滤/撤回事件/去重（替身频道，不触配置中心）。"""

from __future__ import annotations

from agent.channel.schemas import ChannelType, MessageKind
from channels.wcauto.adapter import WcautoChannel
from channels.wcauto.bridge.wcauto_bridge import protocol
from channels.wcauto.config import WcautoConfig
from channels.wcauto.state import make_dedup
from channels.wcauto.transport import WcautoRpcError


class _FakeStateStore:
    def load_seq(self) -> int:
        return 0

    def save_seq(self, seq: int) -> None:
        pass

    def flush(self) -> None:
        pass


class _FakeTransport:
    def __init__(self, nick: str = "梦璃") -> None:
        self.self_info = {"username": "wxid_me", "nick_name": nick}
        self.connected = True
        self.rpc_calls: list = []

    async def call_rpc(self, method, params=None, timeout=None):
        self.rpc_calls.append((method, params or {}))
        raise WcautoRpcError("测试环境不下载媒体")


def _channel(**cfg_over):
    """__new__ 构造替身频道：绕过 ConfigManager/EntityRegistry，属性按需补齐。"""
    ch = WcautoChannel.__new__(WcautoChannel)
    ch._config = WcautoConfig(**cfg_over)
    ch._dedup = make_dedup()
    ch._transport = _FakeTransport()
    ch._state_store = _FakeStateStore()
    ch._known_chats = {}
    ch._captured = []

    async def _capture(message):
        ch._captured.append(message)

    ch.on_message = _capture
    return ch


def _msg_event(**over) -> dict:
    data = {
        "username": "wxid_friend",
        "chat_name": "朋友甲",
        "is_group": False,
        "local_id": 1,
        "shard": "message_0.db",
        "type": "文本",
        "sender_id": 5,
        "sender_username": "wxid_friend",
        "sender_name": "朋友甲",
        "content": "你好",
        "create_time": 1700000000,
        "sort_seq": 10,
    }
    data.update(over)
    return {"event": protocol.EVENT_MESSAGE, "data": data, "seq": 1}


class TestMessageFlow:
    async def test_private_message_dispatched(self) -> None:
        ch = _channel()
        await ch._on_bridge_event(_msg_event())
        assert len(ch._captured) == 1
        msg = ch._captured[0]
        assert msg.content == "你好"
        assert msg.channel.channel_type == ChannelType.PRIVATE
        assert msg.trigger_mind is True
        # 会话登记
        assert "wxid_friend" in ch._known_chats

    async def test_self_echo_dropped(self) -> None:
        ch = _channel()
        await ch._on_bridge_event(_msg_event(sender_id=2))
        await ch._on_bridge_event(_msg_event(sender_username="wxid_me", local_id=2))
        assert not ch._captured

    async def test_dedup(self) -> None:
        ch = _channel()
        await ch._on_bridge_event(_msg_event())
        await ch._on_bridge_event(_msg_event())  # 同 username+shard+local_id
        assert len(ch._captured) == 1

    async def test_group_unmentioned_not_trigger_mind(self) -> None:
        ch = _channel(require_mention=True)
        await ch._on_bridge_event(_msg_event(
            username="g@chatroom", chat_name="群", is_group=True, content="闲聊"))
        assert len(ch._captured) == 1
        msg = ch._captured[0]
        assert msg.channel.channel_type == ChannelType.GROUP
        assert msg.trigger_mind is False  # 入库不唤醒

    async def test_group_mentioned_triggers(self) -> None:
        ch = _channel(require_mention=True)
        await ch._on_bridge_event(_msg_event(
            username="g@chatroom", chat_name="群", is_group=True,
            content="@梦璃 帮忙看看"))
        assert ch._captured[0].trigger_mind is True

    async def test_policy_dm_disabled(self) -> None:
        ch = _channel(dm_policy="disabled")
        await ch._on_bridge_event(_msg_event())
        assert not ch._captured

    async def test_policy_group_allowlist_by_name(self) -> None:
        ch = _channel(group_policy="allowlist", group_allow_from="家人群")
        await ch._on_bridge_event(_msg_event(
            username="g1@chatroom", chat_name="其他群", is_group=True))
        assert not ch._captured
        await ch._on_bridge_event(_msg_event(
            username="g2@chatroom", chat_name="家人群", is_group=True, local_id=2))
        assert len(ch._captured) == 1

    async def test_ignore_chats(self) -> None:
        ch = _channel(ignore_chats="wxid_spam,广告群")
        await ch._on_bridge_event(_msg_event(username="wxid_spam"))
        await ch._on_bridge_event(_msg_event(
            username="g@chatroom", chat_name="广告群", is_group=True, local_id=2))
        assert not ch._captured


class TestRecallEvent:
    async def test_recall_writes_system_message(self) -> None:
        ch = _channel()
        await ch._on_bridge_event({
            "event": protocol.EVENT_RECALL,
            "data": {"chat": "wxid_friend", "revoker": "朋友甲",
                     "original_content": "那条消息内容", "create_time": 1700000001},
            "seq": 2,
        })
        assert len(ch._captured) == 1
        msg = ch._captured[0]
        assert msg.kind == MessageKind.SYSTEM
        assert msg.sender.user_id == msg.channel.channel_id == "wxid_friend"
        assert msg.trigger_mind is False
        assert "朋友甲" in msg.content and "那条消息内容" in msg.content

    async def test_group_recall_does_not_invent_an_actor(self) -> None:
        ch = _channel()
        await ch._handle_recall_event({"chat": "room@chatroom"})
        msg = ch._captured[0]
        assert msg.channel.channel_type == ChannelType.GROUP
        assert msg.channel.channel_id == "room@chatroom"
        assert msg.sender.user_id == "wcauto_system"
        assert "身份未知的操作者" in msg.content

    async def test_recall_notify_off(self) -> None:
        ch = _channel(recall_notify=False)
        await ch._on_bridge_event({
            "event": protocol.EVENT_RECALL,
            "data": {"chat": "wxid_friend", "revoker": "朋友甲"},
            "seq": 2,
        })
        assert not ch._captured


class TestStatusEvent:
    async def test_status_event_no_message(self) -> None:
        ch = _channel()
        await ch._on_bridge_event({
            "event": protocol.EVENT_STATUS,
            "data": {"state": "listener_started", "account": "wxid_me"},
            "seq": 3,
        })
        assert not ch._captured
