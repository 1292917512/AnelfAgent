"""chat_ws 协议层测试：envelope、错误码、action 分发（fake websocket，不起服务器）。"""

from __future__ import annotations

import pytest

from web.routers.chat_ws import (
    ERR_INVALID_PAYLOAD,
    ERR_UNKNOWN_ACTION,
    ERR_VOICE_SESSION_BUSY,
    _dispatch,
    _handle_binary,
    _status,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.fixture
def ws():
    return FakeWebSocket()


@pytest.fixture(autouse=True)
def clean_voice():
    from agent.voice import get_voice_manager
    get_voice_manager().reset()
    yield
    get_voice_manager().reset()


def _sub():
    """构造测试订阅对象（_dispatch 第三参；实时 sink 的队列出口）。"""
    from core import realtime_hub
    return realtime_hub.subscribe(client_kind="web")


class TestStatusFrame:
    def test_status_shape(self):
        frame = _status("X_CODE", "细节", request_id="r1")
        assert frame == {
            "type": "status",
            "message": {"code": "X_CODE", "details": "细节"},
            "request_id": "r1",
        }

    def test_status_without_request_id(self):
        assert "request_id" not in _status("X", "d")


class TestDispatch:
    async def test_ping_pong(self, ws):
        await _dispatch(ws, "c1", _sub(), {"action": "ping", "request_id": "r1"}, "u", "n", "")
        assert ws.sent == [{"type": "pong", "request_id": "r1"}]

    async def test_unknown_action_gets_closed_error(self, ws):
        await _dispatch(ws, "c1", _sub(), {"action": "fly", "request_id": "r2"}, "u", "n", "")
        frame = ws.sent[0]
        assert frame["type"] == "status"
        assert frame["message"]["code"] == ERR_UNKNOWN_ACTION
        assert frame["request_id"] == "r2"

    async def test_send_message_requires_content(self, ws):
        await _dispatch(ws, "c1", _sub(), {"action": "send_message"}, "u", "n", "")
        assert ws.sent[0]["message"]["code"] == ERR_INVALID_PAYLOAD

    async def test_ui_state_report_requires_object(self, ws):
        await _dispatch(ws, "c1", _sub(), {"action": "ui_state_report", "state": "x"}, "u", "n", "")
        assert ws.sent[0]["message"]["code"] == ERR_INVALID_PAYLOAD

    async def test_start_session_rejects_non_audio(self, ws):
        """兼容别名 start_session 仅接受 audio 会话（其余 input_type 显式拒绝）。"""
        await _dispatch(ws, "c1", _sub(), {"action": "start_session", "input_type": "screen"}, "u", "n", "")
        assert ws.sent[0]["message"]["code"] == ERR_INVALID_PAYLOAD

    async def test_voice_start_end_cycle(self, ws, tmp_path, monkeypatch):
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        await _dispatch(ws, "c1", _sub(), {"action": "voice_start", "sample_rate": 48000}, "u", "n", "")
        assert ws.sent[0] == {"type": "voice_ack", "ok": True, "active": True}

        # 同一连接的第二次 voice_start 撞租约 → 封闭错误码
        await _dispatch(ws, "c1", _sub(), {"action": "voice_start"}, "u", "n", "")
        assert ws.sent[1]["message"]["code"] == ERR_VOICE_SESSION_BUSY

        await _dispatch(ws, "c1", _sub(), {"action": "voice_end"}, "u", "n", "")
        assert ws.sent[2] == {"type": "voice_ack", "ok": True, "active": False}

    async def test_audio_session_alias_accepted(self, ws, tmp_path, monkeypatch):
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        await _dispatch(
            ws, "c1", _sub(),
            {"action": "start_session", "input_type": "audio", "sample_rate": 16000},
            "u", "n", "",
        )
        assert ws.sent[0]["type"] == "voice_ack"
        await _dispatch(ws, "c1", _sub(), {"action": "end_session"}, "u", "n", "")
        assert ws.sent[1] == {"type": "voice_ack", "ok": True, "active": False}


class TestBinaryFrames:
    async def test_bad_frame_dropped_without_closing(self, ws):
        """坏音频帧只丢帧（log），不抛异常不关连接。"""
        await _handle_binary(ws, "c1", b"garbage")
        assert ws.sent == []

    async def test_valid_frame_without_lease_dropped(self, ws):
        import struct
        frame = b"NEKO" + struct.pack("<I", 48000) + b"\x00\x00" * 480
        await _handle_binary(ws, "no-lease-conn", frame)  # 无语音会话，静默丢弃
        assert ws.sent == []


class TestAuth:
    def test_no_password_allows_all(self, monkeypatch):
        monkeypatch.setattr("web.server._load_auth_password", lambda: "")
        from web.routers.chat_ws import _check_auth
        assert _check_auth(None) is True

    def test_password_requires_matching_token(self, monkeypatch):
        from web.server import _make_token
        monkeypatch.setattr("web.server._load_auth_password", lambda: "secret")

        class _WS:
            def __init__(self, token: str = "", cookie: str = "") -> None:
                self.query_params = {"token": token} if token else {}
                self.cookies = {"_anelf_token": cookie} if cookie else {}

        from web.routers.chat_ws import _check_auth
        assert _check_auth(_WS()) is False
        assert _check_auth(_WS(token="wrong")) is False
        assert _check_auth(_WS(token=_make_token("secret"))) is True
        assert _check_auth(_WS(cookie=_make_token("secret"))) is True
