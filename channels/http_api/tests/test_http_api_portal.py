"""http_api 双向门户测试（不触网）。

覆盖：视觉推送端点、出站 webhook 回调级联、入站媒体落盘、
/api/chat 媒体与 callback_url 注册、reply_policy 契约文本。
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

import agent.vision.buffer as buffer_mod
import agent.vision.framework as framework_mod
from agent.vision import get_vision_buffer
from channels.http_api.adapter import ChatRequest, HttpApiChannel, ImageItem, MediaItem
from core.config import ConfigManager


@pytest.fixture(autouse=True)
def clean_state():
    saved = dict(framework_mod._SOURCES)
    framework_mod._SOURCES.clear()
    get_vision_buffer().reset()
    saved_cfg = {
        key: ConfigManager.get(f"http_api_{key}", "")
        for key in ("api_token", "callback_url", "callback_token", "callback_timeout")
    }
    yield
    framework_mod._SOURCES.clear()
    framework_mod._SOURCES.update(saved)
    get_vision_buffer().reset()
    for key, value in saved_cfg.items():
        ConfigManager.set(f"http_api_{key}", value)


@pytest.fixture
def fake_hash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(buffer_mod, "grid_dhash", lambda path, grid=4: [0] * (grid * grid))


@pytest.fixture
def fake_agent_app(monkeypatch: pytest.MonkeyPatch):
    """替换组合根 AgentApp，捕获 send_message 入参。"""
    calls: List[Dict[str, Any]] = []

    async def send_message(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        "agent.runtime.agent_app.get_agent_app",
        lambda: SimpleNamespace(send_message=send_message),
        raising=False,
    )
    return calls


class _FakeResponse:
    def raise_for_status(self) -> None:
        pass


class _FakeHttpClient:
    """捕获回调 POST 的假网络客户端。"""

    def __init__(self, calls: List[Tuple[str, Dict[str, Any], Dict[str, str]]]) -> None:
        self._calls = calls

    async def __aenter__(self) -> "_FakeHttpClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def post(self, url: str, json: Any = None, headers: Any = None) -> _FakeResponse:
        self._calls.append((url, json, headers or {}))
        return _FakeResponse()


@pytest.fixture
def http_capture(monkeypatch: pytest.MonkeyPatch):
    calls: List[Tuple[str, Dict[str, Any], Dict[str, str]]] = []
    monkeypatch.setattr(
        "core.network.async_network_client", lambda **kw: _FakeHttpClient(calls), raising=False)
    return calls


def _client(channel: HttpApiChannel) -> TestClient:
    """直接挂频道自建 app（含中间件；逐路由拷贝会丢 body 上限中间件）。"""
    return TestClient(channel._create_app())


class TestVisionPushEndpoint:
    def test_push_ok(self, fake_hash, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        channel = HttpApiChannel()
        resp = _client(channel).post("/api/vision/push", json={
            "source": "doorbell",
            "image_base64": base64.b64encode(b"\xff\xd8\xff\xd9").decode(),
            "mime_type": "image/jpeg",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok" and body["source"] == "external:doorbell"
        assert get_vision_buffer().latest_by_source.get("external:doorbell") is not None

    def test_push_requires_auth_when_token_configured(
            self, fake_hash, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        ConfigManager.set("http_api_api_token", "secret")
        channel = HttpApiChannel()
        client = _client(channel)
        resp = client.post("/api/vision/push", json={
            "source": "cam", "image_base64": "eA==", "mime_type": "image/png",
        })
        assert resp.json()["status"] == "error"
        assert "未认证" in resp.json()["error"]
        resp = client.post(
            "/api/vision/push",
            json={"source": "cam", "image_base64": "eA==", "mime_type": "image/png"},
            headers={"x-api-token": "secret"},
        )
        assert resp.json()["status"] == "ok"

    def test_push_rejected_when_source_disabled(self, fake_hash, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        framework_mod.set_enabled("external:doorbell", False)
        channel = HttpApiChannel()
        resp = _client(channel).post("/api/vision/push", json={
            "source": "doorbell", "image_base64": "eA==", "mime_type": "image/jpeg",
        })
        assert resp.json()["status"] == "error"
        framework_mod.set_enabled("external:doorbell", True)


class TestCallbackDelivery:
    def test_send_text_falls_back_to_callback(self, http_capture) -> None:
        ConfigManager.set("http_api_callback_url", "http://hook.local/anelf")
        channel = HttpApiChannel()
        result = json.loads(asyncio.run(channel.send_text("u1", "答复")))
        assert result["success"] is True and result["delivery"] == "callback"
        url, payload, _headers = http_capture[0]
        assert url == "http://hook.local/anelf"
        assert payload["event"] == "message" and payload["chat_id"] == "u1"
        assert payload["segments"] == [{"type": "text", "text": "答复"}]

    def test_session_callback_overrides_default(self, http_capture) -> None:
        ConfigManager.set("http_api_callback_url", "http://default.local/")
        channel = HttpApiChannel()
        channel._callbacks["u1"] = "http://session.local/"
        asyncio.run(channel.send_text("u1", "hi"))
        assert http_capture[0][0] == "http://session.local/"

    def test_callback_bearer_token_header(self, http_capture) -> None:
        ConfigManager.set("http_api_callback_url", "http://hook.local/")
        ConfigManager.set("http_api_callback_token", "tok")
        channel = HttpApiChannel()
        asyncio.run(channel.send_text("u1", "hi"))
        assert http_capture[0][2].get("authorization") == "Bearer tok"

    def test_callback_failure_returns_error(self, monkeypatch) -> None:
        ConfigManager.set("http_api_callback_url", "http://hook.local/")

        class _Boom:
            async def __aenter__(self) -> "_Boom":
                return self

            async def __aexit__(self, *args: Any) -> bool:
                return False

            async def post(self, *args: Any, **kwargs: Any) -> Any:
                raise ConnectionError("refused")

        monkeypatch.setattr(
            "core.network.async_network_client", lambda **kw: _Boom(), raising=False)
        channel = HttpApiChannel()
        result = json.loads(asyncio.run(channel.send_text("u1", "hi")))
        assert result["success"] is False and "回调投递失败" in result["error"]

    def test_media_segment_inlines_base64(self, http_capture, tmp_path) -> None:
        ConfigManager.set("http_api_callback_url", "http://hook.local/")
        media_file = tmp_path / "shot.jpg"
        media_file.write_bytes(b"\xff\xd8\xff\xd9")
        channel = HttpApiChannel()
        from agent.channel.schemas import SegmentType, SendSegment
        segs = [SendSegment(type=SegmentType.IMAGE, file_path=str(media_file), caption="现场")]

        async def scenario() -> None:
            delivered = await channel._deliver_callback("u1", segs)
            assert delivered is True

        asyncio.run(scenario())
        seg = http_capture[0][1]["segments"][0]
        assert seg["type"] == "image" and seg["caption"] == "现场"
        assert seg["base64"] == base64.b64encode(b"\xff\xd8\xff\xd9").decode()

    def test_forward_message_media_goes_callback(self, http_capture) -> None:
        """无 SSE 订阅者时，媒体通知经回调投递且按 fire-and-forget 成功。"""
        ConfigManager.set("http_api_callback_url", "http://hook.local/")
        channel = HttpApiChannel()
        from agent.channel.schemas import AdapterChannel, SegmentType, SendRequest, SendSegment
        request = SendRequest(
            adapter_key="http_api",
            channel=AdapterChannel(channel_id="u1"),
            segments=[SendSegment(type=SegmentType.FILE, file_path="/tmp/report.pdf", caption="报告")],
        )
        resp = asyncio.run(channel.forward_message(request))
        assert resp.success is True
        assert http_capture[0][1]["segments"][0]["type"] == "file"

    def test_forward_message_fire_and_forget(self) -> None:
        channel = HttpApiChannel()
        from agent.channel.schemas import AdapterChannel, SegmentType, SendRequest, SendSegment
        request = SendRequest(
            adapter_key="http_api",
            channel=AdapterChannel(channel_id="u1"),
            segments=[SendSegment(type=SegmentType.TEXT, content="通知")],
        )
        assert asyncio.run(channel.forward_message(request)).success is True


class TestInboundMedia:
    def test_audio_base64_saved_as_segment(self, tmp_path, monkeypatch) -> None:
        import os
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        channel = HttpApiChannel()
        req = ChatRequest(
            message="听", user_id="u",
            media=[MediaItem(
                kind="audio",
                base64=base64.b64encode(b"RIFF....").decode(),
                mime_type="audio/wav",
                file_name="note.wav",
            )],
        )
        segs = asyncio.run(channel._build_inbound_segments(req))
        assert len(segs) == 1 and segs[0].type.value == "audio"
        assert segs[0].file_name.endswith("note.wav")
        assert os.path.isfile(segs[0].file_path)
        # 与全仓 uploads/<type>/ 约定同树
        assert segs[0].file_path.startswith(str(tmp_path / "audio"))

    def test_image_base64_file_backed(self, tmp_path, monkeypatch) -> None:
        import os
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        channel = HttpApiChannel()
        req = ChatRequest(
            message="", user_id="u",
            images=[ImageItem(base64=base64.b64encode(b"\xff\xd8").decode(), mime_type="image/jpeg")],
        )
        segs = asyncio.run(channel._build_inbound_segments(req))
        assert segs[0].type.value == "image"
        assert os.path.isfile(segs[0].file_path)
        assert segs[0].file_path.endswith(".jpg")

    def test_unknown_kind_rejected(self) -> None:
        channel = HttpApiChannel()
        req = ChatRequest(message="x", media=[MediaItem(kind="voice", base64="eA==")])
        with pytest.raises(ValueError):
            asyncio.run(channel._build_inbound_segments(req))

    def test_missing_base64_rejected(self) -> None:
        channel = HttpApiChannel()
        req = ChatRequest(message="x", media=[MediaItem(kind="audio")])
        with pytest.raises(ValueError):
            asyncio.run(channel._build_inbound_segments(req))

    def test_bad_base64_rejected(self) -> None:
        channel = HttpApiChannel()
        req = ChatRequest(message="x", media=[MediaItem(kind="file", base64="!!!bad!!!")])
        with pytest.raises(ValueError):
            asyncio.run(channel._build_inbound_segments(req))


class TestChatValidation:
    def test_empty_message_rejected(self) -> None:
        channel = HttpApiChannel()
        assert "空消息" in channel._validate_chat_request(ChatRequest(message="  "))

    def test_oversize_message_rejected_by_schema(self, fake_agent_app) -> None:
        """超长消息在 pydantic 层被 422 拦截，不进业务校验。"""
        channel = HttpApiChannel()
        resp = _client(channel).post("/api/chat", json={
            "message": "x" * 300_000, "user_id": "u",
        })
        assert resp.status_code == 422
        assert not fake_agent_app

    def test_attachment_count_capped(self) -> None:
        channel = HttpApiChannel()
        req = ChatRequest(
            message="x",
            media=[MediaItem(kind="file", base64="eA==")] * 10,
        )
        assert "附件数量" in channel._validate_chat_request(req)

    def test_bad_kind_rejected(self) -> None:
        channel = HttpApiChannel()
        assert "未知消息类别" in channel._validate_chat_request(
            ChatRequest(message="x", kind="bogus"))

    def test_body_size_limit(self, fake_agent_app, monkeypatch) -> None:
        """请求体硬上限：Content-Length 超限直接 413。"""
        monkeypatch.setattr(
            "channels.http_api.adapter._MAX_BODY_BYTES", 100, raising=False)
        channel = HttpApiChannel()
        resp = _client(channel).post("/api/chat", json={
            "message": "x" * 500, "user_id": "u", "async_mode": True,
        })
        assert resp.status_code == 413
        assert not fake_agent_app


class TestChatEndpoint:
    def test_media_and_callback_registered(self, fake_agent_app, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        channel = HttpApiChannel()
        resp = _client(channel).post("/api/chat", json={
            "message": "听这段录音",
            "user_id": "hub",
            "async_mode": True,
            "callback_url": "http://hub.local/cb",
            "media": [{
                "kind": "audio",
                "base64": base64.b64encode(b"RIFF....").decode(),
                "mime_type": "audio/wav",
                "file_name": "a.wav",
            }],
        })
        assert resp.status_code == 200 and resp.json()["status"] == "accepted"
        assert channel._callbacks.get("hub") == "http://hub.local/cb"
        call = fake_agent_app[0]
        assert call["media_segments"] and call["media_segments"][0].type.value == "audio"
        assert call["adapter_key"] == "http_api"

    def test_bad_media_aborts_before_dispatch(self, fake_agent_app) -> None:
        channel = HttpApiChannel()
        resp = _client(channel).post("/api/chat", json={
            "message": "x", "user_id": "hub",
            "media": [{"kind": "audio", "base64": "!!!bad!!!"}],
        })
        assert resp.json()["status"] == "error"
        assert not fake_agent_app

    def test_bad_callback_scheme_rejected(self, fake_agent_app) -> None:
        channel = HttpApiChannel()
        resp = _client(channel).post("/api/chat", json={
            "message": "x", "user_id": "hub", "callback_url": "ftp://evil",
        })
        assert resp.json()["status"] == "error"
        assert not fake_agent_app

    def test_duplicate_message_id_deduped(self, fake_agent_app) -> None:
        """客户端重试同 message_id 不重复入管道。"""
        channel = HttpApiChannel()
        client = _client(channel)
        body = {"message": "上报", "user_id": "hub", "async_mode": True,
                "message_id": "m-1", "request_id": "r-1"}
        assert client.post("/api/chat", json=body).json()["status"] == "accepted"
        resp = client.post("/api/chat", json=body)
        assert resp.json()["status"] == "duplicate"
        assert len(fake_agent_app) == 1

    def test_dedup_namespaced_per_conversation(self, fake_agent_app) -> None:
        """不同会话的同号 message_id 互不误杀。"""
        channel = HttpApiChannel()
        client = _client(channel)
        for uid in ("u1", "u2"):
            resp = client.post("/api/chat", json={
                "message": "hi", "user_id": uid, "async_mode": True,
                "message_id": "m-1",
            })
            assert resp.json()["status"] == "accepted"
        assert len(fake_agent_app) == 2

    def test_sse_stream_requires_auth(self, fake_agent_app) -> None:
        """SSE 订阅端点必须鉴权（否则对话流可被任意订阅）。"""
        ConfigManager.set("http_api_api_token", "secret")
        channel = HttpApiChannel()
        client = _client(channel)
        assert client.get("/api/chat/stream?user_id=u1").status_code == 401

    def test_check_auth_accepts_query_token_and_headers(self) -> None:
        """鉴权三入口：?token=（EventSource 不能自定义头）/ x-api-token / Bearer。"""
        ConfigManager.set("http_api_api_token", "secret")
        channel = HttpApiChannel()
        from starlette.requests import Request

        def _req(query: str = "", headers: Optional[Dict[str, str]] = None) -> Request:
            return Request({
                "type": "http", "method": "GET", "path": "/x",
                "query_string": query.encode(),
                "headers": [
                    (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
                ],
            })

        assert channel._check_auth(_req("token=secret")) is True
        assert channel._check_auth(_req(headers={"x-api-token": "secret"})) is True
        assert channel._check_auth(_req(headers={"authorization": "Bearer secret"})) is True
        assert channel._check_auth(_req("token=wrong")) is False
        assert channel._check_auth(_req()) is False

    def test_ws_binary_frame_rejected_not_dropped(self, fake_hash, tmp_path, monkeypatch) -> None:
        """二进制帧回错误帧不断连（推流契约是 JSON 文本帧）。"""
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        channel = HttpApiChannel()
        with _client(channel).websocket_connect("/api/vision/stream") as ws:
            ws.send_bytes(b"\x00\x01\x02")
            ack = json.loads(ws.receive_text())
            assert ack["status"] == "error" and "文本" in ack["error"]
            ws.send_text(json.dumps({
                "source": "cam", "mime_type": "image/png",
                "image_base64": base64.b64encode(b"x").decode(),
            }))
            assert json.loads(ws.receive_text())["status"] == "ok"

    def test_kind_and_envelope_passthrough(self, fake_agent_app) -> None:
        """标准信封：kind/会话/引用/身份字段完整落入统一入站路径。"""
        channel = HttpApiChannel()
        resp = _client(channel).post("/api/chat", json={
            "message": "温度超限", "user_id": "sensor", "user_name": "传感器",
            "session_id": "alarms", "kind": "event", "async_mode": True,
            "reply_to_id": "m0", "reply_content": "上一条",
        })
        assert resp.json()["status"] == "accepted"
        call = fake_agent_app[0]
        assert call["message_kind"] == "event"
        assert call["session_id"] == "alarms"
        assert call["reply_to_id"] == "m0" and call["reply_content"] == "上一条"
        assert call["user_name"] == "传感器" and call["adapter_key"] == "http_api"

    def test_group_message_routed_as_group(self, fake_agent_app) -> None:
        channel = HttpApiChannel()
        resp = _client(channel).post("/api/chat", json={
            "message": "hi", "user_id": "u1", "group_id": "g1", "async_mode": True,
        })
        assert resp.json()["status"] == "accepted"
        call = fake_agent_app[0]
        assert call["group_id"] == "g1" and call["session_id"] == "g1"

    def test_notification_routed_to_notification_center(self, fake_agent_app) -> None:
        """kind=notification 聚合进通知中心会话，强制异步受理，来源在正文标注。"""
        channel = HttpApiChannel()
        resp = _client(channel).post("/api/chat", json={
            "message": "部署完成", "user_id": "ci_bot", "kind": "notification",
        })
        assert resp.json()["status"] == "accepted"
        call = fake_agent_app[0]
        assert call["message_kind"] == "notification"
        assert call["user_id"] == "http_api_notify"
        assert call["session_id"] == "notification"
        assert "ci_bot" in call["content"]


class TestReplyPolicy:
    def test_contract_text_covers_both_directions(self) -> None:
        channel = HttpApiChannel()
        text = channel.reply_policy.instructions
        assert "/api/chat" in text and "/api/vision/push" in text
        assert "/api/vision/stream" in text and "portal_help" in text
        assert "回调" in text and "级联" in text
        assert f"{channel.config.host}:{channel.config.port}" in text


class TestVisionStreamWs:
    def test_stream_frames_ingested(self, fake_hash, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        channel = HttpApiChannel()
        frame = {
            "source": "cam1",
            "image_base64": base64.b64encode(b"\xff\xd8\xff\xd9").decode(),
            "mime_type": "image/jpeg",
        }
        with _client(channel).websocket_connect("/api/vision/stream") as ws:
            ws.send_text(json.dumps(frame))
            ack = json.loads(ws.receive_text())
            assert ack["status"] == "ok" and ack["source"] == "external:cam1"
            # 坏帧只回错误不断连
            ws.send_text("not-json")
            assert json.loads(ws.receive_text())["status"] == "error"
            ws.send_text(json.dumps({"source": "../evil", "image_base64": "eA=="}))
            assert json.loads(ws.receive_text())["status"] == "error"
            ws.send_text(json.dumps(frame))
            assert json.loads(ws.receive_text())["status"] == "ok"
        assert get_vision_buffer().latest_by_source.get("external:cam1") is not None

    def test_stream_requires_auth(self, fake_hash, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        ConfigManager.set("http_api_api_token", "secret")
        channel = HttpApiChannel()
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with _client(channel).websocket_connect("/api/vision/stream"):
                pass
        assert exc_info.value.code == 4401
        with _client(channel).websocket_connect("/api/vision/stream?token=secret") as ws:
            ws.send_text(json.dumps({"source": "cam", "image_base64": "eA=="}))
            assert json.loads(ws.receive_text())["status"] == "ok"


class TestPortalHelpTool:
    def test_tool_marked_and_spec_live(self) -> None:
        ConfigManager.set("http_api_api_token", "secret")
        ConfigManager.set("http_api_callback_url", "http://hook.local/")
        channel = HttpApiChannel()
        meta = getattr(type(channel).portal_help, "_channel_tool_meta", None)
        assert meta is not None, "portal_help 应标记为频道工具"
        spec = json.loads(channel.portal_help())
        assert spec["base_url"] == f"http://{channel.config.host}:{channel.config.port}"
        assert spec["auth"]["required"] is True
        assert "chat" in spec["inbound"] and "vision_stream" in spec["inbound"]
        assert spec["outbound"]["callback_url"].startswith("http://hook.local")
        # 契约永不泄露令牌值
        assert "secret" not in json.dumps(spec)

    def test_tool_registers_into_entity_catalog(self) -> None:
        """频道注册时 portal_help 进入实体目录（group=channel_ops, tags=[http_api]），
        AI 可经 list_entity_methods('channel_ops') 发现并调用。"""
        from agent.channel.tool_bridge import register_channel_tools, unregister_channel_tools
        from core.entity import EntityRegistry
        channel = HttpApiChannel()
        try:
            register_channel_tools(channel)
            entity = EntityRegistry.get("http_api_portal_help")
            assert entity is not None, "http_api_portal_help 未注册"
            assert entity.group == "channel_ops"
            assert "http_api" in (entity.tags or [])
            import asyncio as _aio
            payload = json.loads(_aio.run(entity.func()))
            assert payload["channel_id"] == "http_api"
            assert "base_url" in payload and "inbound" in payload
        finally:
            unregister_channel_tools("http_api")
