"""chat_ws 端到端测试：真实 ASGI 应用上的 WS 握手/协议帧（TestClient 起服）。"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """免密码 + 隔离配置的应用实例。"""
    monkeypatch.setattr("web.server._load_auth_password", lambda: "")
    from web.server import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_hub():
    from core import realtime_hub
    realtime_hub.reset()
    yield
    realtime_hub.reset()


class TestWebSocketEndToEnd:
    def test_ping_pong_over_real_socket(self, client):
        with client.websocket_connect("/api/chat/ws?client=web") as ws:
            ws.send_json({"action": "ping", "request_id": "r1"})
            frame = ws.receive_json()
            assert frame == {"type": "pong", "request_id": "r1"}

    def test_unknown_action_gets_status_frame(self, client):
        with client.websocket_connect("/api/chat/ws") as ws:
            ws.send_json({"action": "fly", "request_id": "r2"})
            frame = ws.receive_json()
            assert frame["type"] == "status"
            assert frame["message"]["code"] == "UNKNOWN_ACTION"
            assert frame["request_id"] == "r2"

    def test_downstream_receives_hub_events(self, client):
        """hub 广播的事件经 WS 下行（type 映射与字段透传）。"""
        from core import realtime_hub
        with client.websocket_connect("/api/chat/ws") as ws:
            realtime_hub.publish({
                "event": "reply", "content": "你好", "chat_id": "c1",
            })
            frame = ws.receive_json()
            assert frame["type"] == "reply"
            assert frame["content"] == "你好"
            assert frame["chat_id"] == "c1"

    def test_desktop_slot_supersedes_previous(self, client):
        """desktop 单槽位：第二条连接接管时旧连接收到 SUPERSEDED。"""
        with client.websocket_connect("/api/chat/ws?client=desktop") as old:
            with client.websocket_connect("/api/chat/ws?client=desktop") as new:
                new.send_json({"action": "ping"})
                assert new.receive_json()["type"] == "pong"
                frame = old.receive_json()
                assert frame["type"] == "status"
                assert frame["message"]["code"] == "CONNECTION_SUPERSEDED"

    def test_web_clients_not_kicked(self, client):
        """web 类客户端多开不互踢。"""
        with client.websocket_connect("/api/chat/ws?client=web") as a:
            with client.websocket_connect("/api/chat/ws?client=web") as b:
                a.send_json({"action": "ping"})
                b.send_json({"action": "ping"})
                assert a.receive_json()["type"] == "pong"
                assert b.receive_json()["type"] == "pong"

    def test_auth_rejects_bad_token(self, monkeypatch):
        from web.server import _make_token
        monkeypatch.setattr("web.server._load_auth_password", lambda: "secret")
        from web.server import create_app
        with TestClient(create_app()) as c:
            with pytest.raises(Exception):  # 4401 拒绝（握手失败）
                with c.websocket_connect("/api/chat/ws?token=wrong"):
                    pass
            with c.websocket_connect(f"/api/chat/ws?token={_make_token('secret')}") as ws:
                ws.send_json({"action": "ping"})
                assert ws.receive_json()["type"] == "pong"

    def test_http_endpoints_still_up(self, client):
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/chat/bot-name").status_code == 200


class TestRealtimeVoiceMode:
    def test_realtime_session_lifecycle(self, client):
        """voice_start(mode=realtime) 开实时会话；voice_end 收尾。"""
        from agent.realtime import get_realtime_engine
        with client.websocket_connect("/api/chat/ws") as ws:
            ws.send_json({"action": "voice_start", "mode": "realtime",
                          "sample_rate": 16000, "request_id": "v1"})
            for _ in range(5):
                frame = ws.receive_json()
                if frame["type"] == "voice_ack":
                    break
            assert frame["active"] is True
            conn_ids = get_realtime_engine().status()["owners"]
            assert len(conn_ids) == 1
            ws.send_json({"action": "voice_end", "request_id": "v2"})
            for _ in range(5):
                frame = ws.receive_json()
                if frame["type"] == "voice_ack":
                    break
            assert frame["active"] is False
            assert get_realtime_engine().status()["owners"] == []

    def test_realtime_lease_conflict_gets_busy(self, client):
        """同连接重复开实时会话 → VOICE_SESSION_BUSY。"""
        with client.websocket_connect("/api/chat/ws") as ws:
            ws.send_json({"action": "voice_start", "mode": "realtime",
                          "sample_rate": 16000})
            for _ in range(5):
                if ws.receive_json()["type"] == "voice_ack":
                    break
            ws.send_json({"action": "voice_start", "mode": "realtime",
                          "sample_rate": 16000, "request_id": "v3"})
            for _ in range(5):
                frame = ws.receive_json()
                if frame["type"] == "status":
                    break
            assert frame["message"]["code"] == "VOICE_SESSION_BUSY"
            ws.send_json({"action": "voice_end"})
            for _ in range(5):
                if ws.receive_json()["type"] == "voice_ack":
                    break

    def test_realtime_state_event_flows_downstream(self, client):
        """实时会话的状态事件经 WS 下行（rt_state JSON 帧）。"""
        from agent.realtime import get_realtime_engine
        with client.websocket_connect("/api/chat/ws") as ws:
            ws.send_json({"action": "voice_start", "mode": "realtime",
                          "sample_rate": 16000})
            # ack 与下行泵的 rt_state 存在竞速：扫帧找齐两者
            seen: set[str] = set()
            for _ in range(5):
                frame = ws.receive_json()
                seen.add(frame["type"])
                if {"voice_ack", "rt_state"} <= seen:
                    break
                if frame["type"] == "rt_state":
                    assert frame["state"] == "listening"
            assert {"voice_ack", "rt_state"} <= seen
            ws.send_json({"action": "voice_end"})
            for _ in range(5):
                if ws.receive_json()["type"] == "voice_ack":
                    break
            assert get_realtime_engine().status()["owners"] == []


@pytest.fixture(autouse=True)
def fake_voice_providers():
    """e2e 环境的语音组件桩：cascade 就绪门禁需要可用的 ASR/TTS。"""
    from agent.audio import get_audio_registry
    from agent.tts import get_tts_registry
    from agent.tts.providers import TtsStream

    class _FakeStreamAsr:
        name = "e2e_stream"
        kind = "asr_stream"
        priority = 1

        async def check_available(self) -> bool:
            return True

        def open_session(self, sample_rate: int = 16000):
            return None

    class _FakeTts:
        name = "e2e_tts"
        priority = 1

        async def check_available(self) -> bool:
            return True

        def stream_synthesize(self, text, *, voice="", sample_rate=24000):
            async def _chunks():
                yield b"\x00\x00" * 100
            return TtsStream(_chunks(), 24000)

    saved_audio = get_audio_registry().list()
    saved_tts = get_tts_registry().list()
    get_audio_registry().register(_FakeStreamAsr())
    get_tts_registry().register(_FakeTts())
    yield
    get_audio_registry().reset()
    get_tts_registry().reset()
    for p in saved_audio:
        get_audio_registry().register(p)
    for p in saved_tts:
        get_tts_registry().register(p)
