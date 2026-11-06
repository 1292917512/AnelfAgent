"""真实文件、SQLite、发送管道与 Web 路由之间的媒体往返契约。"""

from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agent.channel import output_tools
from agent.channel.channel_types import ChannelStatus
from agent.storage.data_center import ConversationData
from agent.storage.sqlite_backend import SqliteBackend
from agent.storage.storage_router import StorageRouter
from channels.webui.adapter import WebUIChannel
from core import realtime_hub
from core.conversation_scope import bind_scope, reset_scope
from core.path import ConfigPaths
from services import chat as chat_service
from web.routers import chat

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5XcAAAAASUVORK5CYII=")


@pytest.fixture
async def media_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(ConfigPaths, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(chat, "_UPLOAD_DIR", uploads)
    db = SqliteBackend(db_path=str(tmp_path / "messages.sqlite3"))
    data = ConversationData(StorageRouter(sqlite=db))
    channel = WebUIChannel()
    channel._status = ChannelStatus.RUNNING
    monkeypatch.setattr(output_tools, "_get_channel", lambda cid: channel if cid == "webui" else None)
    monkeypatch.setattr(output_tools, "conversation_data_port", SimpleNamespace(bound=True, get=lambda: data))
    monkeypatch.setattr(chat_service, "get_runtime", lambda: SimpleNamespace(data_center=SimpleNamespace(sqlite=db)))
    monkeypatch.setattr(chat_service, "is_ready", lambda: True)
    # 明确安装与生产相同的事件桥；测试间事件总线会清空监听器。
    chat._setup_chat_broadcast_bridge()
    subscriber = realtime_hub.subscribe(client_kind="web")
    token = bind_scope("user_webui:web_user#media-test")
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, db, channel, subscriber
    finally:
        reset_scope(token)
        realtime_hub.unsubscribe(subscriber)
        await db.close()


@pytest.mark.parametrize("kind,method,suffix", [
    ("image", "send_photo", ".png"), ("voice", "send_voice", ".wav"),
    ("audio", "send_audio", ".mp3"), ("video", "send_video", ".mp4"),
    ("file", "send_file", ".txt"),
])
async def test_media_survives_source_removal_and_history_reload(media_runtime, tmp_path, kind, method, suffix):
    client, db, channel, subscriber = media_runtime
    source = tmp_path / ("截图 (1) #50%" + suffix)
    content = PNG if kind == "image" else b"original attachment"
    source.write_bytes(content)
    caption = "附件说明"
    if method == "send_photo":
        raw = await output_tools.send_photo("webui", "web_user", str(source), caption)
    elif method == "send_file":
        raw = await output_tools.send_file("webui", "web_user", str(source), caption)
    elif method == "send_voice":
        caption = ""
        raw = await output_tools.send_voice("webui", "web_user", str(source))
    else:
        async def invoke(ch, target, channel_type, session):
            return await getattr(ch, method)(target, str(source), caption=caption, session_id=session)
        raw = await output_tools.execute_send_action(
            channel_id="webui", target_id="web_user", operation=kind, invoke=invoke,
        )
    receipt = json.loads(raw)
    assert receipt["success"] and receipt["history_saved"]
    frame = await asyncio.wait_for(subscriber.queue.get(), 1)
    assert frame["event"] == "media" and frame["chat_id"] == "media-test"
    assert frame["cid"] == receipt["media"]["cid"]
    assert frame["media_type"] == kind
    source.write_bytes(b"replacement")
    source.unlink()
    response = await client.get(frame["url"])
    assert response.status_code == 200 and response.content == content
    if kind == "image":
        assert response.headers["content-type"] == "image/png"
    if kind == "voice":
        assert frame["url"].startswith("/api/chat/files/audio/")

    # 关闭连接后从磁盘重读，不能以进程内消息缓存冒充持久化。
    await db.close()
    history_response = await client.get("/api/chat/history", params={"chat_id": "media-test"})
    assert history_response.status_code == 200
    [message] = history_response.json()
    assert message["role"] == "assistant" and message["content"] == caption
    assert {key: message[key] for key in receipt["media"]} == receipt["media"]
    assert (await client.get(message["url"])).content == content
    incremental = await client.get("/api/chat/history", params={"chat_id": "media-test", "after_id": 0})
    assert incremental.json() == [message]
    other = await client.get("/api/chat/history", params={"chat_id": "other"})
    assert other.json() == []
    model_history = await db.fetch_conversation(scope_type="user", scope_id="webui:web_user#media-test", limit=10)
    assert "media" not in model_history[0] and "url" not in model_history[0]


async def test_history_failure_is_reported_without_claiming_send_failure(media_runtime, tmp_path, monkeypatch):
    _, db, _, subscriber = media_runtime
    source = tmp_path / "photo.png"
    source.write_bytes(PNG)
    monkeypatch.setattr(db, "append_conversation", AsyncMock(side_effect=OSError("disk full")))
    receipt = json.loads(await output_tools.send_photo("webui", "web_user", str(source)))
    assert receipt["success"] is True and receipt["history_saved"] is False
    assert "不要重复发送" in receipt["warning"]
    assert (await asyncio.wait_for(subscriber.queue.get(), 1))["event"] == "media"


async def test_existing_conversation_database_migrates_without_changing_history(tmp_path):
    db_path = str(tmp_path / "legacy.sqlite3")
    original = SqliteBackend(db_path=db_path)
    try:
        await original.append_conversation(scope_type="user", scope_id="webui:web_user", role="assistant", content="旧消息")
        expected = await original.fetch_conversation_with_id(scope_type="user", scope_id="webui:web_user")
    finally:
        await original.close()
    with sqlite3.connect(db_path) as legacy:
        legacy.execute("ALTER TABLE conversation_messages DROP COLUMN media_json")
    migrated = SqliteBackend(db_path=db_path)
    try:
        assert await migrated.fetch_conversation_with_id(scope_type="user", scope_id="webui:web_user") == expected
        await migrated.close()
        assert await migrated.fetch_conversation_with_id(scope_type="user", scope_id="webui:web_user") == expected
    finally:
        await migrated.close()
