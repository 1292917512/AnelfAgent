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
from channels.http_api.config import HttpApiConfig
from core.config import ConfigManager


@pytest.fixture(autouse=True)
def clean_state():
    saved = dict(framework_mod._SOURCES)
    framework_mod._SOURCES.clear()
    get_vision_buffer().reset()
    saved_cfg = {
        key: ConfigManager.get(f"http_api_{key}", getattr(HttpApiConfig(), key))
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


class TestSessionContexts:
    async def test_skill_fields_encode_internal_tags_and_replace_connection_selection(self, fake_agent_app) -> None:
        from core.tags import tag_label

        channel = HttpApiChannel()
        client = _client(channel)
        target = {"user_id": "app:alice", "session_id": "support"}
        first = client.put("/api/sessions/context", json={**target, "skills": ["order-support", "order-support"]})
        assert first.status_code == 200 and first.json()["skills"] == ["order-support"]
        assert first.json()["dynamic_context"] == ""
        snapshot = await channel.contexts.provide(ChatRequest(**target).scope)
        assert snapshot is not None and tag_label("skill", "order-support") in snapshot.content
        second = client.put("/api/sessions/context", json={**target, "dynamic_context": "当前订单已发货", "skills": ["shipping"]})
        assert second.json()["dynamic_context"] == "当前订单已发货" and second.json()["skills"] == ["shipping"]
        snapshot = await channel.contexts.provide(ChatRequest(**target).scope)
        assert snapshot is not None and "order-support" not in snapshot.content
        assert tag_label("skill", "shipping") in snapshot.content
        response = client.post("/api/chat", json={**target, "message": "请处理订单", "skills": ["order-support"], "async_mode": True})
        assert response.json()["status"] == "accepted"
        assert fake_agent_app[-1]["content"] == tag_label("skill", "order-support") + "\n请处理订单"
        assert client.post("/api/chat", json={**target, "skills": ["shipping"], "async_mode": True}).json()["status"] == "accepted"
        assert client.delete("/api/sessions/context", params=target).json()["skills"] == []
        assert await channel.contexts.provide(ChatRequest(**target).scope) is None
        for skills in (["../order-support"], ["bad][channel:qq"], [f"skill-{index}" for index in range(9)]):
            assert client.put("/api/sessions/context", json={**target, "skills": skills}).status_code == 422
            assert client.post("/api/chat", json={**target, "message": "x", "skills": skills}).status_code == 422

    def test_context_api_isolates_users_groups_and_sessions(self, fake_agent_app) -> None:
        channel = HttpApiChannel()
        client = _client(channel)
        targets = [
            {"user_id": "app:alice", "session_id": "first"},
            {"user_id": "app:alice", "session_id": "second"},
            {"user_id": "app:bob", "session_id": "first"},
            {"user_id": "app:alice", "group_id": "app:alice", "session_id": "first"},
        ]
        for index, target in enumerate(targets):
            response = client.put("/api/sessions/context", json={**target, "dynamic_context": f"state-{index}"})
            assert response.status_code == 200
        for index, target in enumerate(targets):
            assert client.get("/api/sessions/context", params=target).json()["dynamic_context"] == f"state-{index}"
        assert client.delete("/api/sessions/context", params=targets[0]).json()["dynamic_context"] == ""
        assert client.get("/api/sessions/context", params=targets[1]).json()["dynamic_context"] == "state-1"
        assert fake_agent_app == []

    def test_one_context_per_connection_overwrites_at_capacity_and_clear_releases_slot(self) -> None:
        channel = HttpApiChannel()
        channel._config = HttpApiConfig(max_context_sessions=1)
        client = _client(channel)
        target = {"user_id": "alice", "session_id": "one"}
        assert client.put("/api/sessions/context", json={**target, "dynamic_context": "old"}).status_code == 200
        assert client.put("/api/sessions/context", json={**target, "dynamic_context": "latest"}).status_code == 200
        assert client.get("/api/sessions/context", params=target).json()["dynamic_context"] == "latest"
        assert client.put("/api/sessions/context", json={"user_id": "bob", "dynamic_context": "new"}).status_code == 503
        assert client.get("/api/sessions/context", params=target).json()["dynamic_context"] == "latest"
        assert client.put("/api/sessions/context", json={**target, "dynamic_context": ""}).status_code == 200
        assert client.get("/api/sessions/context", params=target).json()["dynamic_context"] == ""
        assert client.put("/api/sessions/context", json={"user_id": "bob", "dynamic_context": "new"}).status_code == 200

    async def test_dynamic_snapshots_refresh_through_existing_provider_and_gate(self, monkeypatch) -> None:
        from agent.context.providers import collect_provider_messages
        from core.context_provider import ContextProviderRegistry

        monkeypatch.setattr(ContextProviderRegistry, "_providers", {})
        channel = HttpApiChannel()
        channel.contexts.register()
        client = _client(channel)
        target = {"user_id": "alice", "session_id": "one"}
        scope = ChatRequest(**target).scope
        try:
            assert client.put("/api/sessions/context", json={**target, "dynamic_context": "first snapshot"}).status_code == 200
            first = await collect_provider_messages(None, scope)
            assert len(first) == 1 and "first snapshot" in first[0]["content"]
            assert first[0]["_layer"] == "provider"
            assert first[0]["_source"]["provider"] == "http_api_context"
            assert await collect_provider_messages(None, ChatRequest(user_id="other").scope) == []
            client.put("/api/sessions/context", json={**target, "dynamic_context": "second snapshot"})
            second = await collect_provider_messages(None, scope)
            assert "second snapshot" in second[0]["content"] and "first snapshot" not in str(second)
            assert "first snapshot" in first[0]["content"]
            ConfigManager.set("http_api_context_inject", False)
            assert await collect_provider_messages(None, scope) == []
            assert client.get("/api/sessions/context", params=target).json()["dynamic_context"] == "second snapshot"
            ConfigManager.set("http_api_context_inject", True)
            assert "second snapshot" in str(await collect_provider_messages(None, scope))
        finally:
            await channel.stop()
        assert client.get("/api/sessions/context", params=target).json()["dynamic_context"] == ""
        assert channel.contexts.provider_name not in {meta.name for meta in ContextProviderRegistry.get_all()}

    def test_context_auth_validation_discovery_and_browser_methods(self) -> None:
        channel = HttpApiChannel()
        channel._config = HttpApiConfig(api_token="secret", cors_origins=["https://app.local"])
        client = _client(channel)
        url = "/api/sessions/context"
        for method in ("GET", "PUT", "DELETE"):
            assert client.request(method, url).status_code == 401
        headers = {"x-api-token": "secret", "Origin": "https://app.local"}
        for body in ({}, {"dynamic_context": "x", "typo": True},
                     {"dynamic_context": "x" * 16_001}, {"user_id": "bad#id", "dynamic_context": "x"}):
            assert client.put(url, json=body, headers=headers).status_code == 422
        for method in ("PUT", "DELETE"):
            response = client.options(url, headers={"Origin": "https://app.local", "Access-Control-Request-Method": method})
            assert response.status_code == 200
        assert set(client.get("/openapi.json").json()["paths"][url]) == {"put", "get", "delete"}
        spec = client.get("/api/sdk", headers=headers).json()["portal"]["inbound"]["context"]
        assert spec["path"].endswith(url)


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
        channel._register_callback("u1", "http://session.local/", "topic-1")
        asyncio.run(channel.send_text("u1", "hi", session_id="topic-1"))
        assert http_capture[0][0] == "http://session.local/"
        assert http_capture[0][1]["session_id"] == "topic-1"

    def test_callbacks_follow_subsession(self, http_capture) -> None:
        channel = HttpApiChannel()
        channel._register_callback("u1", "http://first.local/", "topic-1")
        channel._register_callback("u1", "http://second.local/", "topic-2")

        asyncio.run(channel.send_text("u1", "one", session_id="topic-1"))
        asyncio.run(channel.send_text("u1", "two", session_id="topic-2"))

        assert [item[0] for item in http_capture] == [
            "http://first.local/", "http://second.local/",
        ]
        assert [item[1]["session_id"] for item in http_capture] == ["topic-1", "topic-2"]

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
        """媒体消息经回调完整投递后才返回成功。"""
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
        assert resp.message_id == http_capture[0][1]["message_id"]
        second = asyncio.run(channel.forward_message(request))
        assert second.message_id == http_capture[1][1]["message_id"]
        assert second.message_id != resp.message_id

    def test_forward_message_without_recipient_fails(self) -> None:
        channel = HttpApiChannel()
        from agent.channel.schemas import AdapterChannel, SegmentType, SendRequest, SendSegment
        request = SendRequest(
            adapter_key="http_api",
            channel=AdapterChannel(channel_id="u1"),
            segments=[SendSegment(type=SegmentType.TEXT, content="通知")],
        )
        resp = asyncio.run(channel.forward_message(request))
        assert resp.success is False
        assert "无 SSE 订阅者或可用回调" in (resp.error or "")

    def test_forward_message_text_reaches_sse_subscriber(self) -> None:
        from agent.channel.schemas import AdapterChannel, SendRequest, SendSegment
        from channels.http_api.adapter import _StreamSub

        channel = HttpApiChannel()
        sub = _StreamSub("user_http_api:u1")
        channel._stream_subs.append(sub)
        response = asyncio.run(channel.forward_message(SendRequest(
            adapter_key="http_api",
            channel=AdapterChannel(channel_id="u1"),
            segments=[SendSegment(type="text", content="通知")],
        )))
        assert response.success is True
        assert json.loads(sub.queue.get_nowait()) == {"type": "reply", "content": "通知"}

    def test_forward_message_subsession_reaches_sse_with_session_id(self) -> None:
        from agent.channel.schemas import AdapterChannel, SendRequest, SendSegment
        from channels.http_api.adapter import _StreamSub

        channel = HttpApiChannel()
        sub = _StreamSub("user_http_api:u1")
        channel._stream_subs.append(sub)
        response = asyncio.run(channel.forward_message(SendRequest(
            adapter_key="http_api",
            channel=AdapterChannel(channel_id="u1"),
            segments=[SendSegment(type="text", content="进度")],
            extra={"session_id": "topic-1"},
        )))
        assert response.success is True
        assert json.loads(sub.queue.get_nowait()) == {
            "type": "reply", "content": "进度", "session_id": "topic-1",
        }

    def test_forward_message_media_needs_callback_even_with_text_subscriber(self) -> None:
        from agent.channel.schemas import AdapterChannel, SendRequest, SendSegment
        from channels.http_api.adapter import _StreamSub

        channel = HttpApiChannel()
        sub = _StreamSub("user_http_api:u1")
        channel._stream_subs.append(sub)
        request = SendRequest(
            adapter_key="http_api",
            channel=AdapterChannel(channel_id="u1"),
            segments=[
                SendSegment(type="text", content="报告"),
                SendSegment(type="file", file_path="/tmp/report.pdf"),
            ],
        )
        resp = asyncio.run(channel.forward_message(request))
        assert resp.success is False
        assert "媒体消息无可用回调" in (resp.error or "")
        assert sub.queue.empty()

    @pytest.mark.parametrize(
        ("segment_type", "expected_error"),
        [("location", "不受支持"), ("image", "缺少文件路径")],
    )
    def test_forward_message_rejects_unrepresentable_segments(
        self, segment_type: str, expected_error: str, http_capture,
    ) -> None:
        from agent.channel.schemas import AdapterChannel, SendRequest, SendSegment

        ConfigManager.set("http_api_callback_url", "http://hook.local/")
        channel = HttpApiChannel()
        response = asyncio.run(channel.forward_message(SendRequest(
            adapter_key="http_api",
            channel=AdapterChannel(channel_id="u1"),
            segments=[SendSegment(type=segment_type, content="value")],
        )))
        assert response.success is False
        assert expected_error in (response.error or "")
        assert not http_capture


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
    def test_group_and_private_same_id_have_independent_dedup_and_callbacks(self, fake_agent_app) -> None:
        channel = HttpApiChannel()
        client = _client(channel)
        for group_id, callback in (("", "https://private.local/"), ("42", "https://group.local/")):
            result = client.post("/api/chat", json={
                "user_id": "42", "group_id": group_id, "message": "hello",
                "message_id": "same", "async_mode": True, "callback_url": callback,
            })
            assert result.json()["status"] == "accepted"
        assert len(fake_agent_app) == 2
        assert channel._resolve_callback("42") == "https://private.local/"
        assert channel._resolve_callback("42", channel_type="group") == "https://group.local/"

    def test_failed_media_can_retry_same_id_without_registering_callback(self, fake_agent_app) -> None:
        channel = HttpApiChannel()
        client = _client(channel)
        body = {"user_id": "retry", "message_id": "m1", "async_mode": True,
                "callback_url": "https://retry.local/", "media": [{"kind": "audio", "base64": "!!!"}]}
        assert client.post("/api/chat", json=body).json()["status"] == "error"
        assert not channel._callbacks and not channel._pending_replies
        body.update(message="corrected", media=[])
        assert client.post("/api/chat", json=body).json()["status"] == "accepted"
        assert len(fake_agent_app) == 1

    def test_dispatch_failure_cleans_waiter_callback_and_dedup(self, monkeypatch) -> None:
        from unittest.mock import AsyncMock

        channel = HttpApiChannel()
        dispatch = AsyncMock(side_effect=RuntimeError("not ready"))
        monkeypatch.setattr("agent.channel.manager.get_channel_manager",
                            lambda: SimpleNamespace(dispatch_inbound=dispatch))
        client = _client(channel)
        body = {"message": "hello", "user_id": "u1", "message_id": "retry",
                "callback_url": "https://callback.local/"}
        assert client.post("/api/chat", json=body).json()["status"] == "error"
        assert not channel._callbacks and not channel._pending_replies
        dispatch.side_effect = None
        body["async_mode"] = True
        assert client.post("/api/chat", json=body).json()["status"] == "accepted"

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
        assert channel._resolve_callback("hub") == "http://hub.local/cb"
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

    def test_dedup_namespaced_per_subsession(self, fake_agent_app) -> None:
        channel = HttpApiChannel()
        client = _client(channel)
        for session_id in ("topic-1", "topic-2"):
            resp = client.post("/api/chat", json={
                "message": "hi", "user_id": "u1", "session_id": session_id,
                "async_mode": True, "message_id": "m-1",
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
    @pytest.mark.parametrize("topic,required", [
        ("overview", "steps"), ("chat", "example"), ("sessions", "stop"),
        ("context", "example"), ("sdk", "model_operations"), ("https", "native"),
    ])
    def test_ai_help_is_available_by_topic_without_credentials(self, topic, required) -> None:
        channel = HttpApiChannel()
        channel._config = HttpApiConfig(api_token="hidden-token", public_base_url="https://agent.example.com")
        help_text = channel.portal_help(topic=topic)
        payload = json.loads(help_text)
        assert payload["base_url"] == "https://agent.example.com"
        assert required in payload
        assert "hidden-token" not in help_text
        if topic == "overview":
            assert len(help_text) < 2500

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
            session_help = json.loads(_aio.run(EntityRegistry.execute_tool(
                "http_api_portal_help", json.dumps({"topic": "sessions"}),
            )))
            assert session_help["control"]["path"].endswith("/api/sessions/control")
            assert session_help["stop"]["action"] == "stop"
        finally:
            unregister_channel_tools("http_api")


class TestSdkPortal:
    async def test_sdk_capacity_rejects_overload_and_releases_slot(self, monkeypatch) -> None:
        import httpx

        from agent.llm.types import ChatResult

        entered, release = asyncio.Event(), asyncio.Event()

        async def chat(**kwargs: Any) -> ChatResult:
            entered.set()
            await release.wait()
            return ChatResult(content="done")

        monkeypatch.setattr("channels.http_api.sdk_routes.get_model_api", lambda: SimpleNamespace(chat=chat))
        channel = HttpApiChannel()
        channel._config = HttpApiConfig(sdk_concurrency=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=channel._create_app()),
                                     base_url="http://test") as client:
            body = {"messages": [{"role": "user", "content": "hello"}]}
            first = asyncio.create_task(client.post("/api/sdk/models/chat", json=body))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                assert (await client.post("/api/sdk/models/chat", json=body)).status_code == 503
                assert (await client.get("/api/sdk")).status_code == 200
            finally:
                release.set()
                assert (await first).status_code == 200
            assert (await client.post("/api/sdk/models/chat", json=body)).status_code == 200

    async def test_busy_listen_port_never_reports_running(self) -> None:
        import socket

        from agent.channel.channel_types import ChannelStatus

        channel = HttpApiChannel()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            channel._config = HttpApiConfig(port=listener.getsockname()[1])
            with pytest.raises(RuntimeError, match="监听失败"):
                await channel.start()
        assert channel.status is ChannelStatus.STOPPED
        assert not (await channel.health_check()).healthy

    def test_cors_preflight_does_not_bypass_api_auth(self) -> None:
        channel = HttpApiChannel()
        channel._config = HttpApiConfig(api_token="secret", cors_origins=["https://app.example.com"])
        client = _client(channel)
        response = client.options("/api/sdk", headers={"origin": "https://app.example.com",
                                  "access-control-request-method": "GET"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://app.example.com"
        assert client.get("/api/sdk", headers={"origin": "https://app.example.com"}).status_code == 401

    def test_unconfigured_model_is_a_structured_service_error(self, monkeypatch) -> None:
        from agent.llm.model_api import LLMNotConfiguredError

        def unavailable() -> None:
            raise LLMNotConfiguredError("no configured model")

        monkeypatch.setattr("channels.http_api.sdk_routes.get_model_api", unavailable)
        response = _client(HttpApiChannel()).get("/api/sdk/models")
        assert response.status_code == 503 and response.json()["status"] == "error"
        assert response.headers["x-request-id"]
    def test_discovery_and_openapi_describe_actual_sdk_routes(self) -> None:
        from channels.http_api.sdk_routes import MODEL_OPERATIONS

        client = _client(HttpApiChannel())
        spec = client.get("/api/sdk").json()
        openapi = client.get("/openapi.json").json()
        paths = openapi["paths"]
        assert spec["api_version"] == "1"
        for operation in MODEL_OPERATIONS:
            assert f"/api/sdk/models/{operation}" in paths
            assert operation in spec["model_operations"]
        assert "/api/sessions/control" in paths and "/api/sdk/tools/{name}" in paths
        assert "APIKeyHeader" in openapi["components"]["securitySchemes"]
        assert paths["/api/chat"]["post"]["security"]
        assert "security" not in paths["/health"]["get"]

    def test_public_address_policy_help_and_proxy_prefix_agree(self) -> None:
        channel = HttpApiChannel()
        channel._config = HttpApiConfig(public_base_url="https://agent.example.com/gateway", api_token="secret")
        client = _client(channel)
        contract = channel.portal_spec()
        assert contract["base_url"] == "https://agent.example.com/gateway"
        assert contract["inbound"]["vision_stream"]["path"].startswith("wss://")
        assert "https://agent.example.com/gateway" in channel.reply_policy.instructions
        assert client.get("/gateway/api/sdk?token=secret").status_code == 200
        assert client.get("/gateway/api/sdk").status_code == 401
        assert client.get("/api/sdk").status_code == 401
        assert "secret" not in json.dumps(contract)

    def test_public_app_fails_closed_when_token_or_public_config_is_cleared(self) -> None:
        channel = HttpApiChannel()
        channel._config = HttpApiConfig(public_base_url="https://agent.example.com", api_token="secret")
        client = _client(channel)
        assert client.get("/api/sdk?token=secret").status_code == 200
        channel._config = HttpApiConfig()
        assert client.get("/api/sdk").status_code == 401

    @pytest.mark.parametrize("changes", [
        {"ssl_certfile": "cert.pem"}, {"port": 0}, {"reply_timeout": 0},
        {"public_base_url": "https://user:pass@example.com"}, {"stream_queue_size": 0},
    ])
    def test_invalid_transport_configuration_is_rejected(self, changes: dict[str, Any]) -> None:
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            HttpApiConfig(**changes)

    def test_auth_precedes_body_validation_and_all_sdk_routes(self) -> None:
        ConfigManager.set("http_api_api_token", "secret")
        client = _client(HttpApiChannel())
        for path in ("/api/chat", "/api/sdk/models/chat", "/api/sdk/tools/unknown", "/api/sessions/control"):
            response = client.post(path, content="broken-json", headers={"content-type": "application/json"})
            assert response.status_code == 401
            assert response.headers["x-request-id"]
        assert client.get("/api/sdk?token=%E4%B8%AD").status_code == 401

    def test_chunked_body_limit_is_enforced(self, monkeypatch) -> None:
        monkeypatch.setattr("channels.http_api.adapter._MAX_BODY_BYTES", 100)
        response = _client(HttpApiChannel()).post(
            "/api/chat", content=iter([b'{"message":"', b"x" * 200, b'"}']),
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413

    def test_sdk_models_keep_typed_media_and_encode_binary_results(self, monkeypatch) -> None:
        from unittest.mock import AsyncMock

        from agent.llm.model_api import ImageContent, ModelResult

        images = AsyncMock(return_value=ModelResult("a cat", "vision", "model"))
        transcribe = AsyncMock(return_value=ModelResult("hello", "asr", "model"))
        speech = AsyncMock(return_value=ModelResult(b"\xff\x00", "tts", "model"))
        monkeypatch.setattr("channels.http_api.sdk_routes.get_model_api", lambda: SimpleNamespace(
            describe_images=images, transcribe=transcribe, text_to_speech=speech,
        ))
        client = _client(HttpApiChannel())
        response = client.post("/api/sdk/models/describe_images", json={"images": [{"data": "eA=="}]})
        assert response.status_code == 200 and response.json()["result"]["value"] == "a cat"
        assert isinstance(images.call_args.kwargs["images"][0], ImageContent)
        response = client.post("/api/sdk/models/transcribe", json={"audio_data": "_wA="})
        assert response.status_code == 200 and transcribe.call_args.kwargs["audio_data"] == b"\xff\x00"
        response = client.post("/api/sdk/models/text_to_speech", json={"text": "hello"})
        assert response.status_code == 200 and response.json()["result"]["value"] == "_wA="
        assert client.post("/api/sdk/models/text_to_speech", json={"text": "hello", "typo": True}).status_code == 422
        assert client.post("/api/sdk/models/not_a_method", json={}).status_code == 404

    def test_tool_calls_obey_permissions_and_bind_scope(self, monkeypatch) -> None:
        from unittest.mock import AsyncMock

        from agent.approval.types import ApprovalResult
        from core.conversation_scope import current_scope
        from core.entity import EntityRegistry
        from core.tool_context import request_trace
        from core.tool_registry import tool

        gate = SimpleNamespace(check=AsyncMock(return_value=ApprovalResult(False, "denied", "blocked")))
        monkeypatch.setattr("agent.approval.get_approval_gate", lambda: gate)

        @tool(name="http_test_context", group="http_test")
        def context() -> str:
            from entities._sdk import get_current_scope
            return json.dumps({"scope": get_current_scope(), "trace": request_trace()})

        try:
            client = _client(HttpApiChannel())
            assert client.get("/api/sdk/tools?group=http_test").json()["tools"]
            body = {"user_id": "alice", "group_id": "room", "session_id": "topic", "request_id": "once"}
            assert client.post("/api/sdk/tools/http_test_context", json=body).status_code == 403
            gate.check.return_value = ApprovalResult(True, "allowed", "ok", "review notice")
            response = client.post("/api/sdk/tools/http_test_context", json=body).json()
            assert response["notice"] == "review notice"
            result = response["result"]
            assert result["scope"] == result["trace"]["scope"] == "group_http_api:room#topic"
            assert result["trace"]["message_id"] == "once"
            assert gate.check.call_args.kwargs["user_id"] == "alice"
            assert client.post("/api/sdk/tools/http_test_context", json=body).json()["status"] == "duplicate"
            assert gate.check.await_count == 2
            assert current_scope() == "_global"
        finally:
            EntityRegistry.unregister("http_test_context")

    def test_tool_post_retains_sensitive_visibility_after_unregistration(self, monkeypatch) -> None:
        from unittest.mock import AsyncMock

        from agent.approval.types import ApprovalResult
        from core.entity import EntityRegistry
        from core.tool_registry import tool

        gate = SimpleNamespace(check=AsyncMock(return_value=ApprovalResult(True, "allowed", "ok")))
        hooks = AsyncMock(return_value=SimpleNamespace(allowed=True))
        monkeypatch.setattr("agent.approval.get_approval_gate", lambda: gate)
        monkeypatch.setattr("agent.hooks.hooks_active", lambda event: True)
        monkeypatch.setattr("agent.hooks.run_event_hooks", hooks)

        @tool(name="http_sensitive", group="http_test", sensitive_result=True)
        def sensitive() -> str:
            EntityRegistry.unregister("http_sensitive")
            return "private-value"

        try:
            response = _client(HttpApiChannel()).post("/api/sdk/tools/http_sensitive", json={}).json()
            assert response["result"] == "private-value"
            assert hooks.call_args.args == ("tool_post",)
            assert "private-value" not in hooks.call_args.kwargs["result_preview"]
        finally:
            EntityRegistry.unregister("http_sensitive")

    def test_stop_uses_sdk_target_and_resume_is_idempotent_inbound(self, fake_agent_app, monkeypatch) -> None:
        from unittest.mock import AsyncMock

        from agent.execution_control import execution_control_port

        stop = AsyncMock(return_value={"status": "ok", "interrupted": True})
        monkeypatch.setattr(execution_control_port, "_box", [stop])
        client = _client(HttpApiChannel())
        body = {"user_id": "alice", "group_id": "room", "session_id": "topic", "run_id": "run1"}
        response = client.post("/api/sessions/control", json=body)
        assert response.json()["interrupted"] is True
        assert stop.call_args.kwargs["scope"] == "group_http_api:room#topic"
        assert stop.call_args.kwargs["run_id"] == "run1"
        body.update(action="resume", run_id="", request_id="resume1")
        assert client.post("/api/sessions/control", json=body).json()["status"] == "accepted"
        assert client.post("/api/sessions/control", json=body).json()["status"] == "duplicate"
        assert len(fake_agent_app) == 1
        assert fake_agent_app[0]["group_id"] == "room" and fake_agent_app[0]["session_id"] == "topic"

    async def test_history_paginates_persisted_messages_without_crossing_scopes(self, tmp_path, monkeypatch) -> None:
        import httpx

        from agent.channel.output_tools import conversation_data_port
        from agent.storage.sqlite_backend import SqliteBackend

        db = SqliteBackend(str(tmp_path / "history.sqlite3"))
        monkeypatch.setattr(conversation_data_port, "_box", [SimpleNamespace(router=SimpleNamespace(sqlite=db))])
        try:
            for kind, scope, text in (("user", "http_api:42", "private"),
                                      ("group", "http_api:42#topic", "one"),
                                      ("group", "http_api:42#topic", "two")):
                await db.append_conversation(scope_type=kind, scope_id=scope, role="user", content=text)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=HttpApiChannel()._create_app()),
                                         base_url="http://test") as client:
                query = {"group_id": "42", "session_id": "topic", "limit": 1}
                result = (await client.get("/api/sessions/history", params=query)).json()
                assert [m["content"] for m in result["messages"]] == ["two"]
                query["before_id"] = result["messages"][0]["id"]
                older = (await client.get("/api/sessions/history", params=query)).json()
                assert [m["content"] for m in older["messages"]] == ["one"]
                query.pop("before_id")
                query["after_id"] = older["messages"][0]["id"]
                newer = (await client.get("/api/sessions/history", params=query)).json()
                assert [m["content"] for m in newer["messages"]] == ["two"]
        finally:
            await db.close()

    async def test_https_and_group_sse_use_real_tls_socket(self, tmp_path) -> None:
        import ipaddress
        import socket
        import ssl
        from datetime import datetime, timedelta, timezone

        import httpx
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                .sign(key, hashes.SHA256()))
        certfile, keyfile = tmp_path / "cert.pem", tmp_path / "key.pem"
        certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        channel = HttpApiChannel()
        channel._config = HttpApiConfig(port=port, api_token="tls-test", ssl_certfile=str(certfile), ssl_keyfile=str(keyfile))
        await channel.start()
        try:
            context = ssl.create_default_context(cafile=str(certfile))
            async with httpx.AsyncClient(base_url=channel.config.base_url, verify=context,
                                         trust_env=False, timeout=5, headers={"x-api-token": "tls-test"}) as client:
                assert (await client.get("/health")).json()["status"] == "ok"
                assert (await channel.health_check()).healthy
                async with client.stream("GET", "/api/chat/stream?group_id=room&session_id=topic") as response:
                    assert response.status_code == 200
                    await channel.send_text("room", "tls reply", channel_type="group", session_id="topic")
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            assert json.loads(line[6:])["content"] == "tls reply"
                            break
            listening_url = channel.config.base_url
            channel._config = channel.config.model_copy(update={"port": 1})
            assert json.loads(channel.portal_help())["base_url"] == listening_url
        finally:
            await channel.stop()
        assert not (await channel.health_check()).healthy
