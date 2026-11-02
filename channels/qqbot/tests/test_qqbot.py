"""从真实频道入口验证回调身份、消息归属、平台回执和网关会话恢复。"""

import asyncio
import base64
import hashlib
import io
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.routing import APIRoute

from agent.channel.channel_types import ChannelStatus
from agent.channel.schemas import AdapterMessage, SegmentType, SendRequest, SendSegment
from channels.qqbot import adapter
from channels.qqbot.client import QQAPI
from channels.qqbot.config import GATEWAY_INTENTS, QQBotConfig
from channels.qqbot.events import Target, parse_event
from channels.qqbot.gateway import Gateway
from channels.qqbot.media import upload_media
from channels.qqbot.webhook import build_router
from core.http_endpoints import handles_own_auth
from core.tool_context import tool_request

BotFixture = tuple[adapter.QQBotChannel, list[httpx.Request]]


@pytest.fixture
async def bot(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[BotFixture]:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 7200})
        assert request.headers["Authorization"] == "QQBot test-token"
        if request.url.path == "/users/@me":
            return httpx.Response(200, json={"id": "bot", "username": "测试机器人"})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"file_info": "opaque"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"id": "sent-" + str(len(requests))})
        if request.url.path == "/interactions/button":
            return httpx.Response(204)
        if request.url.path == "/users/@me/dms":
            return httpx.Response(200, json={"guild_id": "virtual", "channel_id": "sub"})
        if request.url.path.endswith("/restrict_chat_setting") or "/reactions/" in request.url.path:
            return httpx.Response(204)
        return httpx.Response(404)

    clients: list[httpx.AsyncClient] = []

    def new_client(**kwargs: Any) -> httpx.AsyncClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        clients.append(http)
        return http

    monkeypatch.setattr(adapter, "async_network_client", new_client)
    channel = adapter.QQBotChannel()
    channel._config = QQBotConfig(app_id="123", app_secret="test")
    await channel.start()
    try:
        yield channel, requests
    finally:
        await channel.stop()
        assert all(http.is_closed for http in clients)


def group_event(event: str = "GROUP_AT_MESSAGE_CREATE") -> dict[str, Any]:
    return {"op": 0, "id": "event", "t": event, "d": {
        "id": "incoming", "group_openid": "group1", "author": {"member_openid": "member1"},
        "content": " 你好", "attachments": [{"url": "cdn.qq.com/image", "content_type": "image/png"}],
    }}


async def test_signed_webhook_delivery_and_reply(bot: BotFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    channel, traffic = bot
    dispatch = AsyncMock()
    monkeypatch.setattr(channel, "on_message", dispatch)
    monkeypatch.setattr("channels.qqbot.webhook.get_channel_manager", lambda: {"qqbot": channel})
    app = FastAPI()
    router = build_router()
    auth = {route.path: handles_own_auth(route.endpoint) for route in router.routes if isinstance(route, APIRoute)}
    assert auth == {"/setup": False, "/webhook": True}
    app.include_router(router, prefix="/api/channels/qqbot")
    path = "/api/channels/qqbot/webhook"
    assert path in app.openapi()["paths"]
    headers = {"X-Bot-Appid": "123"}
    key = Ed25519PrivateKey.from_private_bytes(b"test" * 8)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as web:
        setup_path = "/api/channels/qqbot/setup"
        initial = (await web.get(setup_path)).json()
        assert initial == {"callback_path": path, "intents": GATEWAY_INTENTS,
                           "status": "running", "mode": "webhook", "bot_name": "测试机器人",
                           "self_id": "bot", "last_event_at": 0, "last_error": ""}
        challenge = {"op": 13, "d": {"event_ts": "123", "plain_token": "qq-test"}}
        response = await web.post(path, json=challenge, headers=headers)
        key.public_key().verify(bytes.fromhex(response.json()["signature"]), b"123qq-test")
        challenge["d"]["plain_token"] = '{"op":0}'
        assert (await web.post(path, json=challenge, headers=headers)).status_code == 400
        event = group_event()
        event["d"]["message_scene"] = {"ext": ["msg_idx=REFIDX_incoming"]}
        body = json.dumps(event).encode()
        assert (await web.post(path, content=body, headers=headers)).status_code == 403
        ts = str(int(time.time()))
        headers.update({"X-Signature-Timestamp": ts, "X-Signature-Ed25519": key.sign(ts.encode() + body).hex()})
        assert (await web.post(path, content=body + b" ", headers=headers)).status_code == 403
        assert (await web.post(path, content=body, headers={**headers, "X-Bot-Appid": "other"})).status_code == 403
        replies = await asyncio.gather(*(web.post(path, content=body, headers=headers) for _ in range(2)))
        assert all(reply.json() == {"op": 12, "d": 0} for reply in replies)
        assert (await web.get(setup_path)).json()["last_event_at"] >= int(ts)
        monkeypatch.setattr("channels.qqbot.webhook.get_channel_manager", lambda: {})
        stopped = (await web.get(setup_path)).json()
        assert stopped["status"] == "stopped" and stopped["mode"] is None and stopped["callback_path"] == path
    dispatch.assert_awaited_once()
    message: AdapterMessage = dispatch.await_args.args[0]
    assert message.channel.channel_id == "group.group1" and message.sender.user_id == "member1"
    assert message.is_to_me and message.content == "你好" and message.segments[1].url == "https://cdn.qq.com/image"
    results = await asyncio.gather(channel.send_text("group.group1", "回复", reply_to="incoming"),
                                   channel.send_text("group.group1", "补充"))
    assert all(json.loads(result)["success"] for result in results)
    sent = [json.loads(req.content) for req in traffic if req.url.path == "/v2/groups/group1/messages"]
    assert [data["msg_seq"] for data in sent] == [1, 2]
    assert all(data["msg_id"] == "incoming" for data in sent)
    assert sent[0]["message_reference"] == {"message_id": "REFIDX_incoming"}
    assert "message_reference" not in sent[1]
    await channel.stop()
    await channel.start()
    await channel.dispatch(event)
    dispatch.assert_awaited_once()
    assert channel.get_status_info()["last_event_at"] == 0


@pytest.mark.parametrize(("event", "fields", "expected", "sender", "to_me"), [
    ("C2C_MESSAGE_CREATE", {"author": {"user_openid": "p"}}, "c2c.p", "c2c.p", True),
    ("DIRECT_MESSAGE_CREATE", {"author": {"id": "p"}, "guild_id": "dm1"}, "dm.dm1.p", "p", True),
    ("AT_MESSAGE_CREATE", {"author": {"id": "p"}, "channel_id": "sub"}, "channel.sub", "p", True),
    ("MESSAGE_CREATE", {"author": {"id": "p"}, "channel_id": "sub"}, "channel.sub", "p", False),
    ("GROUP_MESSAGE_CREATE", {"author": {"member_openid": "p"}, "group_openid": "grp"}, "group.grp", "p", False),
])
def test_conversation_routes(event: str, fields: dict[str, Any], expected: str, sender: str, to_me: bool) -> None:
    payload = {"op": 0, "t": event, "d": {"id": "msg", "content": "test", **fields}}
    msg = parse_event(payload, "bot", True)
    assert msg and (msg.channel.channel_id, msg.sender.user_id, msg.trigger_mind) == (expected, sender, to_me)
    payload["d"]["author"]["bot"] = True
    assert parse_event(payload, "bot", True) is None


async def test_private_session_media_partial_result(bot: BotFixture, tmp_path: Path) -> None:
    channel, traffic = bot
    assert channel.conversation_scope_for_target("dm.virtual.peer", "private") == (
        "user", "qqbot:peer#dm.virtual.peer")
    result = json.loads(await channel.send_text("peer", "私信", session_id="dm.virtual.peer", reply_to="anchor"))
    assert result["success"] and traffic[-1].url.path == "/dms/virtual/messages"
    assert json.loads(traffic[-1].content) == {"msg_id": "anchor", "content": "私信",
                                              "message_reference": {"message_id": "anchor"}}
    assert not json.loads(await channel.send_text("other", "私信", session_id="dm.virtual.peer"))["success"]
    path = tmp_path / "sample.txt"
    path.write_bytes(b"QQ")
    request = SendRequest(adapter_key="qqbot", channel=Target("c2c", "peer").channel, reply_to="in",
                          segments=[SendSegment(type=SegmentType.FILE, file_path=str(path)),
                                    SendSegment(type=SegmentType.LOCATION)])
    response = await channel.forward_message(request)
    assert not response.success and response.message_ids and "location" in str(response.error)
    upload, send = traffic[-2:]
    assert upload.url.path == "/v2/users/peer/files"
    assert json.loads(upload.content) == {"file_type": 4, "file_name": "sample.txt",
                                         "file_data": "UVE=", "srv_send_msg": False}
    assert json.loads(send.content) == {"msg_type": 7, "media": {"file_info": "opaque"},
                                        "msg_id": "in", "msg_seq": 1}


async def test_dispatch_retry_and_filters(bot: BotFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    channel, _ = bot
    sink = AsyncMock(side_effect=[RuntimeError("not ready"), None])
    monkeypatch.setattr(channel, "on_message", sink)
    message = group_event()
    with pytest.raises(RuntimeError, match="not ready"):
        await channel.dispatch(message)
    await channel.dispatch(message)
    await channel.dispatch({**message, "id": "another-delivery"})
    channel.config.allowed_chats = "group.other"
    blocked = group_event("GROUP_MESSAGE_CREATE")
    blocked["d"]["id"] = "new"
    await channel.dispatch(blocked)
    assert sink.await_count == 2


async def test_token_single_flight_401_refresh_and_ambiguous_write() -> None:
    tokens, writes = 0, 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal tokens, writes
        if "getAppAccessToken" in request.url.path:
            await asyncio.sleep(0)
            tokens += 1
            return httpx.Response(200, json={"access_token": str(tokens), "expires_in": "7200"})
        writes += 1
        if request.headers["Authorization"] == "QQBot 1":
            return httpx.Response(401)
        raise httpx.ReadTimeout("unconfirmed")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        api = QQAPI(http, "testid", "secret")
        assert await asyncio.gather(api.token(), api.token()) == ["1", "1"]
        with pytest.raises(httpx.ReadTimeout):
            await api.request("POST", "/messages", json={"content": "once"})
        assert (tokens, writes) == (2, 2)


async def test_gateway_resume_uses_last_accepted_sequence(bot: BotFixture) -> None:
    channel, _ = bot
    dispatch = AsyncMock(side_effect=[None, RuntimeError("inbox unavailable")])
    gateway = Gateway(channel.api, 123, dispatch, lambda _: None)
    ws = MagicMock(spec=aiohttp.ClientWebSocketResponse)
    hello = {"op": 10, "d": {"heartbeat_interval": 30000}}
    ws.receive_json.side_effect = [hello, {"op": 0, "s": 1, "t": "READY", "d": {"session_id": "session"}},
                                   {**group_event(), "s": 2}, {**group_event(), "s": 3}]
    with pytest.raises(RuntimeError, match="inbox unavailable"):
        await gateway._listen(ws)
    assert ws.send_json.await_args.args[0]["op"] == 2 and gateway.seq == 2
    ws.receive_json.side_effect = [hello, {"op": 9}]
    with pytest.raises(ConnectionError):
        await gateway._listen(ws)
    assert ws.send_json.await_args.args[0] == {"op": 6, "d": {"token": "QQBot test-token", "session_id": "session",
                                                              "seq": 2}}
    assert not gateway.session_id and gateway.seq is None


async def test_start_without_credentials_and_hot_change(bot: BotFixture) -> None:
    channel, _ = bot
    assert (await channel.health_check()).healthy
    from core.config import ConfigManager
    ConfigManager.set("qqbot_app_secret", "changed")
    assert channel.status == ChannelStatus.ERROR and channel.webhook is None
    await channel.stop()
    channel._config = QQBotConfig()
    with pytest.raises(ValueError, match="AppID"):
        await channel.start()


async def test_button_event_ack_and_template_reply(bot: BotFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    channel, traffic = bot
    sink = AsyncMock()
    monkeypatch.setattr(channel, "on_message", sink)
    event = {"op": 0, "t": "INTERACTION_CREATE", "id": "callback", "d": {
        "type": 11, "chat_type": 1, "id": "button", "group_openid": "grp", "group_member_openid": "peer",
        "data": {"resolved": {"button_id": "confirm", "button_data": "ok", "message_id": "original"}},
    }}
    await channel.dispatch(event)
    await channel.dispatch(event)
    sink.assert_awaited_once()
    msg = sink.await_args.args[0]
    assert msg.is_to_me and msg.reply_to_id == "original" and msg.channel.channel_id == "group.grp"
    assert traffic[-1].url.path == "/interactions/button" and json.loads(traffic[-1].content) == {"code": 0}
    result = json.loads(await channel.send_markdown("group.grp", "**确认**", "keyboard"))
    assert result["success"] and json.loads(traffic[-1].content) == {
        "markdown": {"content": "**确认**"}, "msg_type": 2,
        "keyboard": {"id": "keyboard"}, "event_id": "callback",
    }


async def test_multipart_upload_stays_in_target_and_omits_credentials(tmp_path: Path) -> None:
    path = tmp_path / "video.mp4"
    block = 3 * 1024 * 1024
    content = b"A" * block + b"B" * block
    path.write_bytes(content)
    seen: list[str] = []

    def respond(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        seen.append(req.url.path)
        if req.method == "PUT":
            assert req.url.host == "store.test" and "authorization" not in req.headers
            expected = content[:block] if req.url.path == "/p0" else content[block:]
            assert req.content == expected
            return httpx.Response(200)
        data = json.loads(req.content)
        assert req.url.path.startswith("/v2/groups/g/")
        if req.url.path.endswith("upload_prepare"):
            assert (data["file_size"], data["md5"], data["sha1"]) == (
                str(len(content)), hashlib.md5(content).hexdigest(), hashlib.sha1(content).hexdigest())
            return httpx.Response(200, json={"upload_id": "upload", "block_size": str(block), "parts": [
                {"index": i, "presigned_url": "https://store.test/p" + str(i)} for i in (0, 1)
            ]})
        if req.url.path.endswith("upload_part_finish"):
            assert data["upload_id"] == "upload" and data["block_size"] == str(block)
            return httpx.Response(204)
        assert data == {"upload_id": "upload", "file_type": 2, "file_name": "video.mp4", "srv_send_msg": False}
        return httpx.Response(200, json={"file_info": "media"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        assert await upload_media(QQAPI(http, "123", "secret"), Target("group", "g"), str(path), 2) == "media"
    assert [s.rsplit("/", 1)[-1] for s in seen] == [
        "upload_prepare", "p0", "upload_part_finish", "p1", "upload_part_finish", "files"]


async def test_missing_heartbeat_ack_exits_connection(bot: BotFixture) -> None:
    channel, _ = bot
    gateway = Gateway(channel.api, 123, AsyncMock(), lambda _: None)
    ws = MagicMock(spec=aiohttp.ClientWebSocketResponse)
    frames = iter([{"op": 10, "d": {"heartbeat_interval": 10}},
                   {"op": 0, "s": 5, "t": "READY", "d": {"session_id": "session"}}])

    async def receive(*, timeout: float) -> dict[str, Any]:
        frame = next(frames, None)
        if frame:
            return frame
        await asyncio.sleep(timeout)
        raise TimeoutError

    ws.receive_json.side_effect = receive
    with pytest.raises(TimeoutError, match="心跳未确认"):
        await asyncio.wait_for(gateway._listen(ws), 1)
    assert ws.send_json.await_args.args == ({"op": 1, "d": 5},)


async def test_platform_operations_use_their_own_scene(bot: BotFixture) -> None:
    channel, traffic = bot
    assert json.loads(await channel.open_direct_message("guild", "peer"))["chat_id"] == "dm.virtual.peer"
    assert json.loads(traffic[-1].content) == {"source_guild_id": "guild", "recipient_id": "peer"}
    assert json.loads(await channel.set_member_mute("group.g", "m", "del"))["success"]
    assert traffic[-1].url.path == "/v2/groups/g/restrict_chat_setting"
    assert json.loads(traffic[-1].content) == {"members": [{"op": "del", "member_openid": "m"}]}
    assert await channel.set_message_reaction("channel.sub", "msg", "👍", False)
    assert traffic[-1].url.path == "/channels/sub/messages/msg/reactions/2/128077" and traffic[-1].method == "DELETE"
    before = len(traffic)
    assert "error" in json.loads(await channel.set_member_mute("channel.sub", "m", "add"))
    assert not await channel.set_message_reaction("group.g", "msg")
    assert len(traffic) == before


async def test_same_app_restart_dedup_and_origin_bound_reply(bot: BotFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    channel, traffic = bot
    sink = AsyncMock()
    monkeypatch.setattr(channel, "on_message", sink)
    old, new = group_event(), group_event()
    new["d"]["id"] = "second"
    old["d"]["message_scene"] = {"ext": ["msg_idx=REFIDX_old="]}
    await channel.dispatch(old)
    await channel.dispatch(new)
    target = Target("group", "group1")
    with tool_request(target.scope, message_id="incoming"):
        assert json.loads(await channel.send_text(target.address, "回复第一条"))["success"]
    assert json.loads(traffic[-1].content)["msg_id"] == "incoming"
    await channel.stop()
    await channel.start()
    await channel.dispatch({**old, "id": "retry", "d": {**old["d"], "id": "redelivery"}})
    assert sink.await_count == 2
    assert json.loads(await channel.send_text(target.address, "补充", reply_to="incoming"))["success"]
    assert json.loads(traffic[-1].content)["msg_seq"] == 2
    with tool_request(Target("dm", "virtual", "peer").scope, message_id="incoming"):
        assert json.loads(await channel.send_text(target.address, "跨会话发送"))["success"]
    assert "msg_id" not in json.loads(traffic[-1].content)
    await channel.stop()
    channel._config = QQBotConfig(app_id="other", app_secret="different")
    await channel.start()
    await channel.dispatch(old)
    assert sink.await_count == 3


def test_quoted_element_voice_and_card() -> None:
    payload = group_event("GROUP_MESSAGE_CREATE")
    data = payload["d"]
    data.update(content=" <@bot> 回复", message_type=103, message_scene={"ext": ["ref_msg_idx=REFIDX_ref="]},
                msg_elements=[{"content": "无关", "msg_idx": "other"},
                              {"content": "原文", "msg_idx": "REFIDX_ref=", "author": {"id": "bot", "bot": True}}],
                attachments=[{"url": "http://[invalid"}, {"content_type": "voice", "url": "example.test/a.silk",
                             "voice_wav_url": "https://example.test/a.wav", "asr_refer_text": "你好"}])
    msg = parse_event(payload, "bot", True)
    assert msg and msg.is_to_me and msg.trigger_mind and msg.content == "回复\n语音转写：你好"
    assert (msg.reply_content, msg.reply_to_id) == ("原文", "REFIDX_ref=")
    assert msg.segments[-1].type == SegmentType.VOICE and msg.segments[-1].url.endswith("a.wav")
    data.update(content="", attachments=[], message_type=3, ark_data={"fields": {"title": "名片标题", "desc": "描述"}})
    msg = parse_event(payload, "bot", True)
    assert msg and msg.content == "名片标题 描述" and not msg.reply_to_id


async def test_interaction_confirmation_retry_only(bot: BotFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    channel, _ = bot
    sink, ack = AsyncMock(), AsyncMock(side_effect=[httpx.ReadTimeout("lost"), {}])
    monkeypatch.setattr(channel, "on_message", sink)
    monkeypatch.setattr(channel.api, "request", ack)
    payload = {"t": "INTERACTION_CREATE", "id": "button-event", "d": {
        "type": 12, "id": "click", "chat_type": 2, "user_openid": "U", "data": {"resolved": {"feature_id": "menu"}},
    }}
    with pytest.raises(httpx.ReadTimeout):
        await channel.dispatch(payload)
    await channel.dispatch({**payload, "id": "retry"})
    await channel.dispatch(payload)
    assert sink.await_count == 1 and ack.await_count == 2
    assert "menu" in sink.await_args.args[0].content
    await channel.dispatch({**payload, "id": "clear-notice", "d": {**payload["d"], "id": "clear", "type": 14}})
    assert not sink.await_args.args[0].trigger_mind and ack.await_count == 2


async def test_official_envelope_and_unconfirmed_media_receipt(bot: BotFixture,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    channel, _ = bot
    from channels.qqbot.client import QQAPIError
    response = httpx.Response(200, json={"err_code": 40034005, "message": "expired", "trace_id": "qq-trace"})
    with pytest.raises(QQAPIError, match="40034005.*qq-trace"):
        channel.api._result(response)
    send = AsyncMock(side_effect=[{"file_info": "ok"}, httpx.ReadTimeout("lost")])
    monkeypatch.setattr(channel.api, "request", send)
    result = json.loads(await channel.send_photo("group.g", "https://example.test/media.png"))
    assert result["outcome"] == "unknown" and not result["success"] and not result["retryable"]
    assert send.await_count == 2
    with pytest.raises(RuntimeError, match="审核.*304023.*audit"):
        channel.api._result(httpx.Response(202, json={"err_code": 304023, "data": {"message_audit": {"audit_id": "audit"}}}))


async def test_guild_quote_and_own_message_reference(bot: BotFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    channel, _ = bot
    sink = AsyncMock()
    api = AsyncMock(side_effect=[{"message": {"author": {"id": "bot"}, "content": "旧文本"}},
                                 {"id": "out", "ext_info": {"ref_idx": "REFIDX_sent="}}, {"id": "followup"}])
    monkeypatch.setattr(channel, "on_message", sink)
    monkeypatch.setattr(channel.api, "request", api)
    await channel.dispatch({"t": "MESSAGE_CREATE", "d": {"id": "m", "channel_id": "sub", "content": "引用",
                           "author": {"id": "P"}, "message_reference": {"message_id": "old"}}})
    msg = sink.await_args.args[0]
    assert msg.is_to_me and msg.trigger_mind and msg.reply_content == "旧文本"
    await channel.dispatch(group_event())
    assert json.loads(await channel.send_text("group.group1", "首条"))["message_id"] == "out"
    assert json.loads(await channel.send_text("group.group1", "引用自己的回复", reply_to="out"))["success"]
    fields = api.await_args.kwargs["json"]
    assert fields["msg_id"] == "incoming" and fields["message_reference"] == {"message_id": "REFIDX_sent="}


@pytest.mark.parametrize("bad_plan", [False, True])
async def test_part_validation_and_safe_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_plan: bool) -> None:
    from channels.qqbot import media
    from core.path import ConfigPaths, upload_url_for
    monkeypatch.setattr(media, "_DIRECT_LIMIT", 2)
    monkeypatch.setattr(ConfigPaths, "UPLOAD_DIR", str(tmp_path))
    (tmp_path / "file").mkdir()
    (tmp_path / "file" / "样本.bin").write_bytes(b"abcd")
    puts: list[bytes] = []
    confirms: list[int] = []
    merges: list[dict[str, Any]] = []

    def respond(req: httpx.Request) -> httpx.Response:
        if "getAppAccessToken" in req.url.path:
            return httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
        if req.method == "PUT":
            assert "Authorization" not in req.headers
            puts.append(req.content)
            return httpx.Response(503 if len(puts) == 1 else 200)
        if req.url.path.endswith("upload_prepare"):
            return httpx.Response(200, json={"upload_id": "plan", "block_size": "2", "upload_config": {"retry_delay": 0},
                "parts": [{"index": i, "presigned_url": "https://store.test/p"} for i in ([0, 0] if bad_plan else [0, 1])]})
        if req.url.path.endswith("upload_part_finish"):
            confirms.append(json.loads(req.content)["part_index"])
            return httpx.Response(200, json={"err_code": 40093001} if len(confirms) == 1 else {})
        merges.append(json.loads(req.content))
        return httpx.Response(200, json={"file_info": "ready"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        upload = upload_media(QQAPI(http, "app", "secret"), Target("group", "g"), upload_url_for("file", "样本.bin"), 4)
        if bad_plan:
            with pytest.raises(RuntimeError, match="序号"):
                await upload
            assert puts == confirms == merges == []
        else:
            assert await upload == "ready"
            assert puts == [b"ab", b"ab", b"cd"] and confirms == [0, 0, 1]
            assert len(merges) == 1 and merges[0]["srv_send_msg"] is False


async def test_markdown_material_and_card(bot: BotFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image
    channel, traffic = bot
    path = tmp_path / "cover.png"
    Image.new("RGB", (2, 2)).save(path)
    raw = path.read_bytes()
    md5 = hashlib.md5(raw).hexdigest()
    etag = md5
    def handle(req: httpx.Request) -> httpx.Response:
        if req.method == "PUT":
            assert req.headers["Content-Type"] == "image/png" and "Authorization" not in req.headers
            assert req.content == raw
            return httpx.Response(200, headers={"ETag": f'"{etag}"'})
        if req.url.path.endswith("/prepare"):
            assert json.loads(req.content)["file_size"] == len(raw)
            return httpx.Response(200, json={"upload_id": "task", "presigned_url": "https://store.test/p"})
        assert req.url.path.endswith("/complete") and json.loads(req.content) == {"upload_id": "task", "md5": md5}
        return httpx.Response(200, json={"image_id": "I", "url": "qqbot-media://image/v1/token", "ttl": 3600})

    assert json.loads(await channel.send_card("c2c.P", "标题", "描述", "https://example.test", "https://example.test/i"))["success"]
    card = json.loads(traffic[-1].content)
    assert card["msg_type"] == 8 and card["card"]["content"]["description"] == "描述"
    assert not json.loads(await channel.send_card("channel.sub", "标题", "描述", "https://a.test", "https://b.test"))["success"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with monkeypatch.context() as patch:
            patch.setattr(channel.api, "http", http)
            material = json.loads(await channel.upload_markdown_material(path.as_uri()))
            assert material["url"] == "qqbot-media://image/v1/token" and material["image_id"] == "I"
            etag = "incorrect"
            assert "error" in json.loads(await channel.upload_markdown_material(str(path)))


async def test_navigation_pins_and_join_approval(bot: BotFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    channel, _ = bot
    array = AsyncMock(side_effect=[[{"id": "g"}], [{"id": "c", "name": "chat", "type": 0}]])
    api = AsyncMock(side_effect=[{}, {"list": [], "next_cursor": ""}, {}])
    monkeypatch.setattr(channel.api, "get_list", array)
    monkeypatch.setattr(channel.api, "request", api)
    assert json.loads(await channel.list_guilds(limit=1))["next_after"] == "g"
    assert json.loads(await channel.list_subchannels("g"))["channels"][0]["chat_id"] == "channel.c"
    assert json.loads(await channel.pin_message("channel.c", "M"))["success"]
    assert api.await_args.args == ("PUT", "/channels/c/pins/M")
    assert "error" in json.loads(await channel.unpin_message("channel.c", "all"))
    assert json.loads(await channel.list_join_requests("group.g", "page"))["list"] == []
    assert json.loads(await channel.review_join_request("group.g", "U", "J", "decline", "原因"))["success"]
    assert api.await_args.args == ("POST", "/v2/groups/g/approval_join_request/U")
    assert api.await_args.kwargs["json"] == {"op": "decline", "join_request_id": "J", "reject_reason": "原因"}


def test_history_share_without_element_index_and_notifications() -> None:
    p = group_event("GROUP_MESSAGE_CREATE")
    p["d"].update(content=" ", attachments=[], message_type=103, msg_elements=[{"content": "分享的聊天记录"}],
                  message_scene={"ext": ["ref_msg_idx=TMP_history"]})
    message = parse_event(p, "bot", True)
    assert message and "分享的聊天记录" in message.content and not message.reply_to_id
    notice = parse_event({"t": "CHANNEL_UPDATE", "id": "notice", "d": {"id": "c", "name": "大厅"}}, "bot", True)
    assert notice and notice.channel.channel_id == "notification" and not notice.trigger_mind
    subscribe = parse_event({"t": "SUBSCRIBE_MESSAGE_STATUS", "d": {"openid": "u", "result": [
        {"template_id": 12, "op": 1, "subscribe_id": "private-auth-id"}]}}, "bot", True)
    assert subscribe and subscribe.channel.channel_id == "c2c.u" and "private-auth-id" not in subscribe.content


def test_capacity_does_not_admit_a_duplicate_as_a_new_event() -> None:
    from agent.channel.dedup import MessageDedup
    cache = MessageDedup(ttl_seconds=60, max_size=2)
    assert not cache.is_duplicate("old") and not cache.is_duplicate("new")
    assert cache.is_duplicate("old")
    cache.discard("old")
    assert not cache.is_duplicate("old")


async def test_get_retry_is_bounded_and_does_not_repeat_writes() -> None:
    from channels.qqbot.client import QQAPIError
    calls: list[str] = []
    def respond(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
        calls.append(req.method)
        if len(calls) == 2:
            return httpx.Response(200, json=[{"id": "guild"}])
        return httpx.Response(200, json={"err_code": 11252, "trace_id": "trace"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        api = QQAPI(http, "A", "secret")
        assert await api.get_list("/users/@me/guilds") == [{"id": "guild"}]
        with pytest.raises(QQAPIError, match="11252.*trace"):
            await api.request("POST", "/messages", json={"content": "once"})
        with pytest.raises(QQAPIError):
            await api.request("GET", "/guilds/g")
        assert calls == ["GET", "GET", "POST", "GET", "GET"]


async def test_group_member_and_bot_permissions(bot: BotFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    channel, _ = bot
    api = AsyncMock(side_effect=[{"member_role": "admin", "username": "管理员"}, {"allow_proactive_msg": False},
                                 {"guild_id": "guild", "name": "子频道"}, {"user": {"id": "u"}, "nick": "成员"}])
    monkeypatch.setattr(channel.api, "request", api)
    assert json.loads(await channel.get_member_info("group.G", "u"))["role"] == "admin"
    assert json.loads(await channel.get_group_bot_state("group.G"))["allow_proactive_msg"] is False
    assert (await channel.get_user_info("u", "channel.C")).user_name == "成员"
    assert [c.args[1] for c in api.await_args_list] == [
        "/v2/groups/G/members/u", "/v2/groups/G/bot_state", "/channels/C", "/guilds/guild/members/u"]


async def test_partial_delivery_stops_at_uncertain_segment(bot: BotFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    channel, _ = bot
    api = AsyncMock(side_effect=[{"id": "confirmed"}, httpx.ReadTimeout("lost")])
    monkeypatch.setattr(channel.api, "request", api)
    target = Target("c2c", "U")
    result = await channel.forward_message(SendRequest(adapter_key="qqbot", channel=target.channel,
        segments=[SendSegment(content=c) for c in ("一", "二", "三")]))
    assert result.model_dump()["outcome"] == "unknown" and result.message_ids == ["confirmed"] and api.await_count == 2
    with tool_request(target.scope, message_id="trigger"):
        api.side_effect = [{"id": "reply"}]
        assert json.loads(await channel.send_text(target.address, "引用", reply_to="REFIDX_other="))["success"]
    data = api.await_args.kwargs["json"]
    assert data["msg_id"] == "trigger" and data["message_reference"]["message_id"] == "REFIDX_other="


async def test_inline_media_and_guild_multipart(bot: BotFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image
    channel, traffic = bot
    with io.BytesIO() as stream:
        Image.new("RGB", (2, 2)).save(stream, format="PNG")
        encoded = base64.b64encode(stream.getvalue()).decode("ascii")
    assert json.loads(await channel.send_photo("group.g", "data:image/png;base64," + encoded))["success"]
    uploaded = json.loads(traffic[-2].content)
    assert uploaded["file_data"] == encoded and uploaded["file_name"] == "media.png"
    bad = json.loads(await channel.send_photo("group.g", "base64://invalid!"))
    assert not bad["success"] and "invalid!" not in bad["error"]
    before = len(traffic)
    assert not json.loads(await channel.send_photo("channel.sub", "base64://" + encoded))["success"]
    assert len(traffic) == before
    gateway = MagicMock(spec=Gateway)
    gateway.online = True
    monkeypatch.setattr(channel, "_gateway", gateway)
    assert json.loads(await channel.send_photo("channel.sub", "base64://" + encoded, reply_to="old"))["success"]
    req = traffic[-1]
    assert req.url.path == "/channels/sub/messages" and "multipart/form-data" in req.headers["content-type"]
    assert b'name="file_image"' in req.content and b'Content-Type: application/json' in req.content
    assert b'{"message_reference": {"message_id": "old"}}' in req.content
