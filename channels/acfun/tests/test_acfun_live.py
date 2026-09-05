"""AcFun 直播子系统单元测试 — 协议编解码 / 信号解释 / 事件路由 / 上下文注入（纯离线）。"""

from __future__ import annotations

import base64
import gzip
import json
import os
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from channels.acfun import context as live_context
from channels.acfun.live import signals as live_signals
from channels.acfun.live.connection import LiveRoomConnection, RoomState
from channels.acfun.live.manager import LiveSessionManager
from channels.acfun.live.protocol import (
    ENCRYPTION_SESSION_KEY,
    KlinkCodec,
    PacketHeader,
    ProtocolError,
    aes_decrypt,
    aes_encrypt,
    build_frame,
    decode_sc_message,
    enter_room_body,
    heartbeat_body,
    parse_cs_cmd_ack,
    parse_enter_room_ack,
    parse_frame,
    parse_register_response,
    pb_field_bytes,
    pb_field_message,
    pb_field_str,
    pb_field_varint,
    pb_get_str,
    pb_get_varint,
    pb_parse,
)
from channels.acfun.tools import AcfunToolsMixin

# ======================================================================
# protobuf 原语与 AES
# ======================================================================


class TestProtobufPrimitives:
    def test_varint_field_roundtrip(self):
        raw = pb_field_varint(1, 13) + pb_field_varint(2, 264001)
        fields = pb_parse(raw)
        assert pb_get_varint(fields, 1) == 13
        assert pb_get_varint(fields, 2) == 264001

    def test_bytes_and_str_fields(self):
        raw = pb_field_str(4, "Global.ZtLiveInteractive.CsCmd") + pb_field_bytes(9, b"\x01\x02")
        fields = pb_parse(raw)
        assert pb_get_str(fields, 4) == "Global.ZtLiveInteractive.CsCmd"
        assert pb_get_bytes_safe(fields, 9) == b"\x01\x02"

    def test_empty_message(self):
        assert pb_parse(b"") == []

    def test_aes_roundtrip(self):
        key = os.urandom(16)
        plaintext = b"payload \xe4\xbd\xa0\xe5\xa5\xbd" * 7
        assert aes_decrypt(key, aes_encrypt(key, plaintext)) == plaintext

    def test_aes_tamper_detectable(self):
        key = os.urandom(16)
        plaintext = b"payload" * 8
        encrypted = aes_encrypt(key, plaintext)
        # 篡改 IV（前 16 字节）：首块解密即损坏，且与原文不同（确定性）
        tampered = bytearray(encrypted)
        tampered[0] ^= 0xFF
        assert aes_decrypt(key, bytes(tampered)) != plaintext
        # 截断载荷：长度校验确定性抛错
        with pytest.raises(Exception):
            aes_decrypt(key, encrypted[:20])


def pb_get_bytes_safe(fields, num: int) -> bytes:
    from channels.acfun.live.protocol import pb_get_bytes

    return pb_get_bytes(fields, num) or b""


# ======================================================================
# klink 帧与编解码器
# ======================================================================

SSECURITY_PLAIN = os.urandom(16)
SSECURITY_B64 = base64.standard_b64encode(SSECURITY_PLAIN).decode()


def make_codec() -> KlinkCodec:
    return KlinkCodec(uid=264001, did="did-xyz", ssecurity=SSECURITY_B64, service_token="tok")


def make_downlink(codec: KlinkCodec, command: str, payload: bytes, seq: int = 99) -> bytes:
    """按服务端视角合成一个下行帧（sessKey 加密）。"""
    plain = (
        pb_field_str(1, command)
        + pb_field_varint(2, seq)
        + pb_field_bytes(4, payload)
    )
    header = PacketHeader(
        app_id=13, uid=264001, instance_id=77, seq_id=seq,
        encryption_mode=ENCRYPTION_SESSION_KEY, token=b"tok",
    )
    return build_frame(header, codec._sess_key or SSECURITY_PLAIN, plain)


class TestKlinkCodec:
    def test_register_frame_parses_back(self):
        codec = make_codec()
        frame = codec.register()
        down = parse_frame(frame, SSECURITY_PLAIN, b"")
        assert down.command == "Basic.Register"
        body = pb_parse(down.payload_data)
        assert pb_get_str(body, 1, "") != ""  # appInfo 存在
        zt = pb_parse(pb_get_bytes_safe(body, 11))
        assert pb_get_str(zt, 1) == "ACFUN_APP"
        assert pb_get_varint(zt, 4) == 264001
        assert down.header is not None and down.header.encryption_mode == 1

    def test_session_flow(self):
        codec = make_codec()
        sess_key = os.urandom(16)
        # 服务端注册 ack：RegisterResponse(sessKey=2, instanceId=3)
        ack_payload = pb_field_bytes(2, sess_key) + pb_field_varint(3, 77)
        register_ack = make_register_ack_frame(ack_payload)
        down = codec.decode(register_ack)
        assert down.command == "Basic.RegisterAck"
        got_key, got_instance = parse_register_response(down.payload_data)
        codec.adopt_session(got_key, got_instance, down.header.app_id if down.header else 13)
        assert codec.has_session
        # 会话帧（sessKey 加密）可解
        downstream = make_downlink(codec, "Push.ZtLiveInteractive.Message", b"abc")
        parsed = codec.decode(downstream)
        assert parsed.command == "Push.ZtLiveInteractive.Message"
        assert parsed.payload_data == b"abc"

    def test_bad_magic_rejected(self):
        with pytest.raises(ProtocolError):
            parse_frame(b"\x00" * 20, SSECURITY_PLAIN, b"")

    def test_cs_cmd_and_acks(self):
        codec = make_codec()
        codec.adopt_session(os.urandom(16), 77, 13)
        frame = codec.cs_cmd("ZtLiveCsEnterRoom", enter_room_body("attach-token", False), "lid", "ticket-1")
        down = parse_frame(frame, SSECURITY_PLAIN, codec._sess_key)
        assert down.command == "Global.ZtLiveInteractive.CsCmd"
        body = pb_parse(down.payload_data)
        assert pb_get_str(body, 1) == "ZtLiveCsEnterRoom"
        assert pb_get_str(body, 3) == "ticket-1"
        assert pb_get_str(body, 4) == "lid"
        inner = pb_parse(pb_get_bytes_safe(body, 2))
        assert pb_get_str(inner, 4) == "attach-token"

        ack_payload = (
            pb_field_str(1, "ZtLiveCsEnterRoomAck")
            + pb_field_varint(2, 0)
            + pb_field_bytes(4, pb_field_varint(1, 10000))
        )
        ack_type, err, _msg, ack_data = parse_cs_cmd_ack(ack_payload)
        assert ack_type == "ZtLiveCsEnterRoomAck" and err == 0
        assert parse_enter_room_ack(ack_data) == 10000

    def test_heartbeat_body(self):
        fields = pb_parse(heartbeat_body(1725000000000, 5))
        assert pb_get_varint(fields, 1) == 1725000000000
        assert pb_get_varint(fields, 2) == 5

    def test_error_frame_raises(self):
        # encryptionMode=0 的服务端错误帧 → ProtocolError
        err_payload = pb_field_str(1, "boom")
        header = PacketHeader(app_id=13, uid=1, seq_id=1, encryption_mode=0)
        frame = build_frame(header, b"k" * 16, err_payload)  # mode 0 时载荷不加密
        with pytest.raises(ProtocolError):
            parse_frame(frame, SSECURITY_PLAIN, b"")


def make_register_ack_frame(payload: bytes) -> bytes:
    plain = pb_field_str(1, "Basic.RegisterAck") + pb_field_varint(2, 1) + pb_field_bytes(4, payload)
    header = PacketHeader(app_id=13, uid=264001, instance_id=77, seq_id=1,
                          encryption_mode=1, token=b"tok")
    return build_frame(header, SSECURITY_PLAIN, plain)


# ======================================================================
# ZtLiveSc 下行消息解包
# ======================================================================


def build_sc_frame(message_type: str, items: List[tuple], gzip_it: bool = True) -> bytes:
    """构造 ZtLiveScMessage：items = [(signalType, [payload bytes])]，每项包成 field 1 的 Item 消息。"""
    bundle = b"".join(
        pb_field_message(1, pb_field_str(1, signal) + b"".join(pb_field_bytes(2, p) for p in payloads))
        for signal, payloads in items
    )
    inner = gzip.compress(bundle) if gzip_it else bundle
    return (
        pb_field_str(1, message_type)
        + pb_field_varint(2, 2 if gzip_it else 1)
        + pb_field_bytes(3, inner)
        + pb_field_str(4, "live-id-x")
        + pb_field_varint(6, 1725000000000)
    )


def comment_payload(content: str, uid: int = 42, name: str = "acer甲") -> bytes:
    user = pb_field_varint(1, uid) + pb_field_str(2, name)
    return pb_field_str(1, content) + pb_field_varint(2, 1725000000123) + pb_field_message(3, user)


class TestDecodeScMessage:
    def test_action_signal_batch(self):
        raw = build_sc_frame("ZtLiveScActionSignal", [
            ("CommonActionSignalComment", [comment_payload("第一条"), comment_payload("第二条")]),
            ("CommonActionSignalLike", [pb_field_message(1, pb_field_varint(1, 7) + pb_field_str(2, "点赞人"))]),
        ])
        signals = decode_sc_message(raw)
        assert [s.signal_type for s in signals] == ["CommonActionSignalComment", "CommonActionSignalLike"]
        assert len(signals[0].payloads) == 2
        assert signals[0].live_id == "live-id-x"

    def test_state_and_standalone(self):
        display = pb_field_str(1, "1234") + pb_field_str(2, "56000") + pb_field_varint(3, 12)
        raw = build_sc_frame("ZtLiveScStateSignal", [("CommonStateSignalDisplayInfo", [display])])
        assert decode_sc_message(raw)[0].signal_type == "CommonStateSignalDisplayInfo"
        # 单载荷类型（StatusChanged / TicketInvalid）
        status = pb_field_varint(1, 1)  # LIVE_CLOSED
        raw2 = (
            pb_field_str(1, "ZtLiveScStatusChanged")
            + pb_field_varint(2, 1)
            + pb_field_bytes(3, status)
        )
        got = decode_sc_message(raw2)
        assert got[0].message_type == "ZtLiveScStatusChanged"
        assert got[0].payloads == [status]

    def test_no_compression(self):
        # 无压缩 + 单载荷类型直接产出
        raw = pb_field_str(1, "ZtLiveScTicketInvalid") + pb_field_varint(2, 1) + pb_field_bytes(3, b"")
        assert decode_sc_message(raw)[0].signal_type == "ZtLiveScTicketInvalid"
        # 空 item 的束 → 空列表
        empty_bundle = build_sc_frame("ZtLiveScActionSignal", [], gzip_it=False)
        assert decode_sc_message(empty_bundle) == []


# ======================================================================
# 信号解释器
# ======================================================================


class TestSignalInterpret:
    def test_comment(self):
        event = live_signals.interpret("CommonActionSignalComment", comment_payload("你好主播"))
        assert event is not None
        assert event.kind == live_signals.EVT_COMMENT
        assert event.text == "你好主播"
        assert event.user_id == "42" and event.user_name == "acer甲"

    def test_like_enter_follow(self):
        user = pb_field_message(1, pb_field_varint(1, 9) + pb_field_str(2, "u"))
        for tag, kind in [
            ("CommonActionSignalLike", live_signals.EVT_LIKE),
            ("CommonActionSignalUserEnterRoom", live_signals.EVT_ENTER),
            ("CommonActionSignalUserFollowAuthor", live_signals.EVT_FOLLOW),
        ]:
            event = live_signals.interpret(tag, user + pb_field_varint(2, 1))
            assert event is not None and event.kind == kind and event.user_id == "9"

    def test_gift(self):
        user = pb_field_message(1, pb_field_varint(1, 5) + pb_field_str(2, "老板"))
        payload = user + pb_field_varint(2, 111) + pb_field_varint(3, 8001) + pb_field_varint(4, 2) + pb_field_varint(5, 3)
        event = live_signals.interpret("CommonActionSignalGift", payload)
        assert event is not None and event.kind == live_signals.EVT_GIFT
        assert event.extra == {"gift_id": "8001", "count": 2, "combo": 3}

    def test_banana(self):
        visitor = pb_field_message(1, pb_field_varint(1, 8) + pb_field_str(2, "蕉农"))
        event = live_signals.interpret("AcfunActionSignalThrowBanana", visitor + pb_field_varint(2, 5) + pb_field_varint(3, 1))
        assert event is not None and event.kind == live_signals.EVT_BANANA
        assert event.extra["count"] == 5

    def test_display_info(self):
        payload = pb_field_str(1, "99") + pb_field_str(2, "1000") + pb_field_varint(3, 3)
        event = live_signals.interpret("CommonStateSignalDisplayInfo", payload)
        assert event is not None
        assert event.extra["watching"] == "99" and event.extra["likes"] == "1000"
        banana = live_signals.interpret("AcfunStateSignalDisplayInfo", pb_field_str(1, "77"))
        assert banana is not None and banana.extra["banana"] == "77"

    def test_status_changed_and_kick(self):
        banned = pb_field_varint(1, 4) + pb_field_message(3, pb_field_str(1, "违规原因"))
        event = live_signals.interpret("ZtLiveScStatusChanged", banned)
        assert event is not None
        assert event.extra["change"] == "live_banned" and event.extra["reason"] == "违规原因"
        kick = live_signals.interpret("CommonNotifySignalKickedOut", pb_field_str(1, "多端登录"))
        assert kick is not None and kick.kind == live_signals.EVT_KICKED and kick.text == "多端登录"

    def test_unknown_returns_none(self):
        assert live_signals.interpret("Something.New", b"\x08\x01") is None


# ======================================================================
# 管理器：模式开关 / 事件分级路由
# ======================================================================


def make_manager_channel(**cfg: Any) -> SimpleNamespace:
    defaults: Dict[str, Any] = {
        "live_max_rooms": 3, "live_watch_rooms": "", "live_recent_window": 20,
        "live_mention_names": "", "live_mention_trigger": True,
        "live_gift_trigger_mind": True, "live_record_chatter": False,
        "live_closed_retry_seconds": 300,
        # 通用频道配置（config_show/config_set 工具覆盖项）
        "live_mode": False, "poll_interval_seconds": 60,
        "notify_like": True, "notify_gift": True, "notify_system": True,
        "gift_trigger_mind": True, "like_trigger_mind": False,
        "live_danmaku_cooldown_seconds": 5, "whitelist_enabled": False,
        "user_whitelist": "", "message_max_length": 1000,
    }
    defaults.update(cfg)
    channel = SimpleNamespace(
        config=SimpleNamespace(**defaults),
        client=SimpleNamespace(username="小铃铛", uid="264001", is_logined=True),
        messages=[],
    )

    async def on_message(message: Any) -> None:
        channel.messages.append(message)

    channel.on_message = on_message
    channel.client.run = None  # 礼物名解析兜底路径用（会失败后回退 ID）
    return channel


@pytest.fixture()
def connected_off() -> None:
    """无需真实连接：watch 在模式关闭时不建连。"""


class TestLiveSessionManager:
    async def test_mode_toggle_without_rooms(self):
        channel = make_manager_channel()
        manager = LiveSessionManager(channel)
        assert manager.mode_enabled is False
        await manager.set_mode(True)
        assert manager.mode_enabled is True
        await manager.set_mode(False)
        assert manager.mode_enabled is False

    async def test_watch_respects_cap(self):
        channel = make_manager_channel(live_max_rooms=2, live_watch_rooms="")
        manager = LiveSessionManager(channel)
        await manager.watch("1001")
        await manager.watch("1002")
        assert manager.watched == ["1001", "1002"]
        assert "上限" in await manager.watch("1003")
        assert manager.watched == ["1001", "1002"]

    async def test_watch_invalid_uid(self):
        manager = LiveSessionManager(make_manager_channel())
        assert "无效" in await manager.watch("abc")

    async def test_comment_mention_dispatches(self):
        channel = make_manager_channel()
        manager = LiveSessionManager(channel)
        await manager._handle_event("1001", live_signals.LiveEvent(
            kind=live_signals.EVT_COMMENT, user_id="42", user_name="acer甲",
            text="大家好~", ts_ms=1))
        assert channel.messages == []  # 普通弹幕仅入缓冲
        assert len(manager.recent_danmaku("1001")) == 1

        await manager._handle_event("1001", live_signals.LiveEvent(
            kind=live_signals.EVT_COMMENT, user_id="42", user_name="acer甲",
            text="小铃铛 你在吗", ts_ms=2))
        assert len(channel.messages) == 1
        msg = channel.messages[0]
        assert msg.is_to_me is True and msg.trigger_mind is True
        assert msg.channel.channel_id == "live:1001"
        assert msg.content == "小铃铛 你在吗"

    async def test_mention_extra_names(self):
        channel = make_manager_channel(live_mention_names="阿铃,铃酱")
        manager = LiveSessionManager(channel)
        await manager._handle_event("1001", live_signals.LiveEvent(
            kind=live_signals.EVT_COMMENT, user_id="1", user_name="u", text="铃酱唱歌"))
        assert len(channel.messages) == 1

    async def test_chatter_record_option(self):
        channel = make_manager_channel(live_record_chatter=True)
        manager = LiveSessionManager(channel)
        await manager._handle_event("1001", live_signals.LiveEvent(
            kind=live_signals.EVT_COMMENT, user_id="1", user_name="u", text="闲聊"))
        assert len(channel.messages) == 1
        assert channel.messages[0].trigger_mind is False

    async def test_gift_and_banana_dispatch(self):
        channel = make_manager_channel()
        manager = LiveSessionManager(channel)
        # 礼物名解析走 HTTP（此处 client 无 run → 回退 礼物#ID）
        async def _run(fn, *a, **k):
            raise RuntimeError("offline")

        channel.client.run = _run
        await manager._handle_event("1001", live_signals.LiveEvent(
            kind=live_signals.EVT_GIFT, user_id="5", user_name="老板",
            extra={"gift_id": "8001", "count": 2, "combo": 1}))
        assert len(channel.messages) == 1
        assert "礼物#8001" in channel.messages[0].content
        assert channel.messages[0].trigger_mind is True
        assert manager.recent_gifts("1001")

        await manager._handle_event("1001", live_signals.LiveEvent(
            kind=live_signals.EVT_BANANA, user_id="6", user_name="蕉农", extra={"count": 3}))
        assert len(channel.messages) == 2
        assert "3 根香蕉" in channel.messages[1].content

    async def test_gift_trigger_off(self):
        channel = make_manager_channel(live_gift_trigger_mind=False)
        manager = LiveSessionManager(channel)

        async def _run(fn, *a, **k):
            return {}

        channel.client.run = _run
        await manager._handle_event("1001", live_signals.LiveEvent(
            kind=live_signals.EVT_GIFT, user_id="5", user_name="老板",
            extra={"gift_id": "1", "count": 1, "combo": 1}))
        assert channel.messages[0].trigger_mind is False

    async def test_like_enter_display_not_dispatched(self):
        channel = make_manager_channel()
        manager = LiveSessionManager(channel)
        for kind in (live_signals.EVT_LIKE, live_signals.EVT_ENTER, live_signals.EVT_DISPLAY):
            await manager._handle_event("1001", live_signals.LiveEvent(kind=kind, extra={}))
        assert channel.messages == []

    async def test_kick_and_violation_trigger_mind(self):
        channel = make_manager_channel()
        manager = LiveSessionManager(channel)
        await manager._handle_event("1001", live_signals.LiveEvent(
            kind=live_signals.EVT_KICKED, text="多端登录"))
        await manager._handle_event("1001", live_signals.LiveEvent(
            kind=live_signals.EVT_VIOLATION, text="请文明发言"))
        assert [m.trigger_mind for m in channel.messages] == [True, True]

    async def test_state_change_system_message(self):
        channel = make_manager_channel()
        manager = LiveSessionManager(channel)
        from channels.acfun.live.connection import RoomState

        await manager._handle_state("1001", RoomState.CONNECTED, "晚间歌回")
        await manager._handle_state("1001", RoomState.RECONNECTING, "第 1 次重连")
        assert len(channel.messages) == 2
        assert all(m.trigger_mind is False for m in channel.messages)
        assert "直播连接已建立" in channel.messages[0].content

    async def test_unwatch_clears_buffers(self):
        channel = make_manager_channel()
        manager = LiveSessionManager(channel)
        await manager._handle_event("1001", live_signals.LiveEvent(
            kind=live_signals.EVT_COMMENT, user_id="1", user_name="u", text="hi"))
        await manager.unwatch("1001")
        assert manager.recent_danmaku("1001") == []

    def test_snapshot_shape(self):
        channel = make_manager_channel()
        manager = LiveSessionManager(channel)
        snap = manager.snapshot()
        assert snap["mode"] is False and snap["rooms"] == [] and snap["watched"] == []


# ======================================================================
# 连接层：状态机局部（离线可测部分）
# ======================================================================


class TestLiveRoomConnection:
    async def test_not_live_marks_closed(self):
        async def enter_params(uid: str) -> Dict[str, Any]:
            return {"is_open": False, "title": "未开播的标题"}

        states: List[tuple] = []

        async def on_state(uid: str, state: RoomState, detail: str) -> None:
            states.append((uid, state, detail))

        async def on_event(uid: str, event: Any) -> None:
            pass

        conn = LiveRoomConnection(
            "1001", enter_params_fn=enter_params,
            credentials_fn=lambda: None, on_event=on_event, on_state=on_state,
        )
        assert await conn._connect_and_enter() is False
        assert conn.state == RoomState.CLOSED
        assert "未开播" in states[-1][2]

    async def test_missing_credentials_raises(self):
        async def enter_params(uid: str) -> Dict[str, Any]:
            return {"is_open": True, "live_id": "lid", "tickets": ["t1"], "enter_room_attach": "a"}

        conn = LiveRoomConnection(
            "1001", enter_params_fn=enter_params,
            credentials_fn=lambda: None,
            on_event=self._noop_event(), on_state=self._noop_state(),
        )
        with pytest.raises(RuntimeError):
            await conn._connect_and_enter()

    @staticmethod
    def _noop_event():
        async def on_event(uid: str, event: Any) -> None:
            pass
        return on_event

    @staticmethod
    def _noop_state():
        async def on_state(uid: str, state: RoomState, detail: str) -> None:
            pass
        return on_state

    async def test_stale_detection(self):
        conn = LiveRoomConnection(
            "1001", enter_params_fn=lambda uid: None,
            credentials_fn=lambda: None,
            on_event=self._noop_event(), on_state=self._noop_state(),
        )
        conn._heartbeat_interval = 5.0
        conn._last_inbound_at = time.time() - 20.0
        assert conn._is_stale() is True
        conn._last_inbound_at = time.time()
        assert conn._is_stale() is False


# ======================================================================
# 上下文 provider
# ======================================================================


class TestLiveContextProvider:
    def _patch_manager(self, monkeypatch, channel: Any) -> None:
        class FakeMgr:
            def __init__(self, ch: Any) -> None:
                self.mode_enabled = bool(ch.config.live_mode)

            def snapshot(self) -> Dict[str, Any]:
                return {
                    "mode": self.mode_enabled,
                    "watched": ["1001"],
                    "rooms": [{
                        "uid": "1001", "state": "connected", "detail": "晚间歌回",
                        "title": "晚间歌回", "user_name": "主播酱", "uptime": 750,
                        "watching": "1234", "likes": "56000", "banana": "78",
                        "danmaku_recent": 23,
                        "stats": {"danmaku": 100, "reconnects": 1, "last_signal_age": 3.0,
                                  "last_error": ""},
                    }],
                    "state_events": [],
                }

            def recent_danmaku(self, uid: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
                return [{"ts": time.time(), "uid": "42", "name": "acer甲", "text": "唱一首！"}]

            def recent_gifts(self, uid: str, limit: int = 5) -> List[str]:
                return ["老板 送出 礼物#8001×2"]

        fake_channel = SimpleNamespace(config=channel.config, live_manager=FakeMgr(channel))

        class FakeChannelManager:
            def get(self, key: str) -> Any:
                return fake_channel if key == "acfun" else None

        monkeypatch.setattr("agent.channel.manager.get_channel_manager", lambda: FakeChannelManager())

    def test_renders_when_mode_on(self, monkeypatch):
        channel = make_manager_channel(live_mode=True, live_recent_window=20)
        self._patch_manager(monkeypatch, channel)
        text = live_context._render_live_status()
        assert text is not None
        assert "AcFun 直播" in text
        assert "live:1001" in text and "晚间歌回" in text
        assert "观众≈1234" in text
        assert "acer甲" in text and "唱一首！" in text
        assert "礼物#8001" in text

    def test_none_when_mode_off(self, monkeypatch):
        channel = make_manager_channel(live_mode=False)
        self._patch_manager(monkeypatch, channel)
        assert live_context._render_live_status() is None

    def test_register_idempotent(self):
        live_context.register_live_context_provider()
        live_context.register_live_context_provider()
        names = [m.name for m in __import__("core.context_provider", fromlist=["x"]).ContextProviderRegistry.get_all()]
        assert names.count("acfun_live_status") == 1

    def test_render_fail_open(self, monkeypatch):
        def _boom():
            raise RuntimeError("manager exploded")

        monkeypatch.setattr("agent.channel.manager.get_channel_manager", _boom)
        assert live_context._render_live_status() is None


# ======================================================================
# 直播运维工具
# ======================================================================


class LiveToolHost(AcfunToolsMixin):
    """工具宿主替身：真实 LiveSessionManager + 假频道。"""

    def __init__(self, **cfg: Any) -> None:
        self.channel = make_manager_channel(**cfg)
        self.live_manager = LiveSessionManager(self.channel)
        self.client = self.channel.client

    @property
    def config(self) -> Any:
        return self.channel.config

    def persist_live_config(self, *, live_mode: Any = None, rooms: Any = None) -> None:
        """直连频道真实实现（写统一配置；宿主替身无变更监听，手动同步内存态）。"""
        from channels.acfun.adapter import AcfunChannel

        AcfunChannel.persist_live_config(self, live_mode=live_mode, rooms=rooms)
        if live_mode is not None:
            self.channel.config.live_mode = live_mode
        if rooms is not None:
            self.channel.config.live_watch_rooms = ",".join(rooms)


class TestLiveTools:
    async def test_live_mode_toggle_persists(self):
        host = LiveToolHost()
        result = json.loads(await host.live_mode("true"))
        assert result["success"] is True and result["live_mode"] is True
        assert host.live_manager.mode_enabled is True
        from core.config import ConfigManager
        assert ConfigManager.get("acfun_live_mode") is True

    async def test_live_watch_and_status(self):
        host = LiveToolHost()
        await host.live_watch("1001")
        assert host.live_manager.watched == ["1001"]
        from core.config import ConfigManager
        assert ConfigManager.get("acfun_live_watch_rooms") == "1001"
        status = json.loads(await host.live_status())
        assert status["success"] is True
        assert status["watched"] == ["1001"]

    async def test_live_unwatch(self):
        host = LiveToolHost()
        await host.live_watch("1001")
        result = json.loads(await host.live_unwatch("1001"))
        assert result["success"] is True
        assert host.live_manager.watched == []


# ======================================================================
# 直播路由端点（build_router /live/*）：状态 / 开关 / 观察管理
# ======================================================================


class TestLiveRouter:
    def _app(self, monkeypatch, tmp_path, channel: Any = None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from channels.acfun import adapter as adapter_mod

        if channel is None:
            monkeypatch.setattr(
                adapter_mod, "_get_acfun_channel",
                lambda: (_ for _ in ()).throw(LookupError("not registered")),
            )
        else:
            monkeypatch.setattr(adapter_mod, "_get_acfun_channel", lambda: channel)
        app = FastAPI()
        app.include_router(adapter_mod.build_router(), prefix="/channels/acfun")
        return TestClient(app), adapter_mod

    def test_status_unregistered(self, monkeypatch, tmp_path):
        client, _ = self._app(monkeypatch, tmp_path)
        body = client.get("/channels/acfun/live/status").json()
        assert body["mode"] is False and body["rooms"] == []
        assert body["channel_running"] is False and body["logined"] is False

    def test_mode_offline_persists_config(self, monkeypatch, tmp_path):
        client, _ = self._app(monkeypatch, tmp_path)
        body = client.post("/channels/acfun/live/mode", json={"enabled": True}).json()
        assert body["success"] is True and body["live_mode"] is True
        from core.config import ConfigManager
        assert ConfigManager.get("acfun_live_mode") is True

    def test_mode_online_applies_and_persists(self, monkeypatch, tmp_path):
        channel = make_manager_channel()
        channel.status = SimpleNamespace(value="running")  # 仅断言用
        # 伪装成运行中的频道：status 需为 ChannelStatus.RUNNING
        from agent.channel.channel_types import ChannelStatus

        channel.status = ChannelStatus.RUNNING
        channel.persist_live_config_calls = []

        def persist_live_config(**kwargs: Any) -> None:
            channel.persist_live_config_calls.append(kwargs)
            if "live_mode" in kwargs:
                channel.config.live_mode = kwargs["live_mode"]
            if "rooms" in kwargs:
                channel.config.live_watch_rooms = ",".join(kwargs["rooms"])

        channel.persist_live_config = persist_live_config
        channel.live_manager = LiveSessionManager(channel)
        client, _ = self._app(monkeypatch, tmp_path, channel=channel)
        body = client.post("/channels/acfun/live/mode", json={"enabled": True}).json()
        assert body["success"] is True and body["live_mode"] is True
        assert channel.live_manager.mode_enabled is True
        assert channel.persist_live_config_calls == [{"live_mode": True}]

    def test_watch_and_unwatch_online(self, monkeypatch, tmp_path):
        from agent.channel.channel_types import ChannelStatus

        channel = make_manager_channel()
        channel.status = ChannelStatus.RUNNING
        channel.persist_live_config = lambda **kw: None
        channel.live_manager = LiveSessionManager(channel)
        client, _ = self._app(monkeypatch, tmp_path, channel=channel)
        body = client.post("/channels/acfun/live/watch", json={"uid": "1001"}).json()
        assert body["success"] is True and body["watched"] == ["1001"]
        body = client.post("/channels/acfun/live/unwatch", json={"uid": "1001"}).json()
        assert body["success"] is True and body["watched"] == []

    def test_watch_unregistered_rejected(self, monkeypatch, tmp_path):
        client, _ = self._app(monkeypatch, tmp_path)
        body = client.post("/channels/acfun/live/watch", json={"uid": "1001"}).json()
        assert body["success"] is False

    def test_status_online_includes_snapshot(self, monkeypatch, tmp_path):
        from agent.channel.channel_types import ChannelStatus

        channel = make_manager_channel(live_mode=True)
        channel.status = ChannelStatus.RUNNING
        channel.client.is_logined = True
        channel.live_manager = LiveSessionManager(channel)
        client, _ = self._app(monkeypatch, tmp_path, channel=channel)
        body = client.get("/channels/acfun/live/status").json()
        assert body["channel_running"] is True and body["logined"] is True
        assert "watched" in body and "rooms" in body


# ======================================================================
# 频道持久化共享路径（persist_live_config 为 AI 工具与 Web API 同源入口）
# ======================================================================


class TestPersistLiveConfigShared:
    def test_channel_persist_writes_config(self, tmp_path, monkeypatch):
        from channels.acfun import adapter as adapter_mod
        from core.config import ConfigManager

        channel = adapter_mod.AcfunChannel()
        channel.persist_live_config(live_mode=True, rooms=["1001", "2002"])
        assert ConfigManager.get("acfun_live_mode") is True
        assert ConfigManager.get("acfun_live_watch_rooms") == "1001,2002"

    def test_offline_persist(self, tmp_path, monkeypatch):
        from channels.acfun import adapter as adapter_mod
        from core.config import ConfigManager

        adapter_mod._persist_live_config_offline(live_mode=True, rooms=["1001"])
        assert ConfigManager.get("acfun_live_mode") is True
        assert ConfigManager.get("acfun_live_watch_rooms") == "1001"


# ======================================================================
# 频道配置自管理工具（config_show / config_set）
# ======================================================================


class TestConfigTools:
    async def test_config_show(self):
        host = LiveToolHost()
        result = json.loads(await host.config_show())
        assert result["success"] is True
        assert "poll_interval_seconds" in result["config"]
        assert result["config"]["live_mode"] is False
        assert "password" not in result["config"]  # 凭据不可见

    async def test_config_set_type_coercion(self):
        from core.config import ConfigManager

        host = LiveToolHost()
        result = json.loads(await host.config_set("poll_interval_seconds", "30"))
        assert result["success"] is True and result["value"] == 30
        assert ConfigManager.get("acfun_poll_interval_seconds") == 30
        result = json.loads(await host.config_set("notify_like", "false"))
        assert result["value"] is False and ConfigManager.get("acfun_notify_like") is False
        result = json.loads(await host.config_set("user_whitelist", "1,2"))
        assert result["value"] == "1,2" and ConfigManager.get("acfun_user_whitelist") == "1,2"

    async def test_config_set_rejects_non_editable(self):
        host = LiveToolHost()
        for key in ("password", "username", "enabled", "live_mode", "live_watch_rooms", "nope"):
            result = json.loads(await host.config_set(key, "x"))
            assert result["success"] is False, key

    async def test_config_set_bad_type(self):
        host = LiveToolHost()
        result = json.loads(await host.config_set("poll_interval_seconds", "abc"))
        assert result["success"] is False
        assert host.config.poll_interval_seconds == 60  # 未被污染
