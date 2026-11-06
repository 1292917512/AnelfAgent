"""WebUI 频道测试：media 帧 URL 契约、频道会话隔离、在线判定、广播路径。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from channels.webui.adapter import WebUIChannel
from channels.webui.media import publish_media
from core import realtime_hub
from core.path import ConfigPaths, parse_upload_url, upload_url_for


@pytest.fixture(autouse=True)
def clean_hub():
    realtime_hub.reset()
    yield
    realtime_hub.reset()


@pytest.fixture
def channel():
    return WebUIChannel()


@pytest.fixture
def captured(channel, monkeypatch):
    """捕获 _broadcast/_broadcast_scoped 的出帧。"""
    frames: list = []

    async def _bcast(event: str, data: dict) -> None:
        frames.append({"event": event, **data})

    monkeypatch.setattr(WebUIChannel, "_broadcast", staticmethod(_bcast))
    return frames


class TestMediaFrameUrl:
    def test_http_url_passthrough(self):
        assert publish_media("https://x.com/a.png", "image") == "https://x.com/a.png"

    @pytest.mark.parametrize("as_url", [False, True])
    def test_uploaded_source_is_snapshotted(self, tmp_path, monkeypatch, as_url):
        monkeypatch.setattr(ConfigPaths, "UPLOAD_DIR", str(tmp_path))
        source = tmp_path / "image" / "截图 #50%.png"
        source.parent.mkdir()
        source.write_bytes(b"original")
        url = publish_media(upload_url_for("image", source.name) if as_url else str(source), "image")
        source.write_bytes(b"changed")
        parts = parse_upload_url(url)
        assert parts is not None
        assert (tmp_path / parts[0] / parts[1]).read_bytes() == b"original"

    @pytest.mark.parametrize("problem", ["missing", "empty", "directory", "oversize", "api"])
    async def test_unpublishable_media_fails_without_broadcast(self, channel, captured, tmp_path, monkeypatch, problem):
        from core.config import ConfigManager

        monkeypatch.setattr(ConfigPaths, "UPLOAD_DIR", str(tmp_path / "uploads"))
        ConfigManager.set("upload_max_mb", 1)
        source = tmp_path / "source.png"
        if problem == "empty":
            source.touch()
        elif problem == "directory":
            source.mkdir()
        elif problem == "oversize":
            source.write_bytes(b"x" * (1024 * 1024 + 1))
        realtime_hub.subscribe(client_kind="web")
        result = json.loads(await channel.send_photo("c1", "/api/unknown" if problem == "api" else str(source)))
        assert result["success"] is False
        assert result["cause"] in {"param", "not_found"}
        assert captured == []
        assert not list(Path(ConfigPaths.UPLOAD_DIR).rglob("*.*"))

    async def test_media_sends_fail_without_clients(self, channel, captured):
        """媒体与文本同一语义：无在线客户端时如实失败（不虚报已送达）。"""
        for send in (
            lambda: channel.send_photo("c1", "/tmp/x.png"),
            lambda: channel.send_voice("c1", "/tmp/x.wav"),
            lambda: channel.send_audio("c1", "/tmp/x.mp3"),
            lambda: channel.send_video("c1", "/tmp/x.mp4"),
            lambda: channel.send_file("c1", "/tmp/x.zip"),
        ):
            result = json.loads(await send())
            assert result["success"] is False
        assert captured == []


class TestOnlineGate:
    async def test_send_text_fails_without_clients(self, channel, captured):
        """无在线客户端时如实返回失败（投递方据此标记未送达）。"""
        result = json.loads(await channel.send_text("c1", "你好"))
        assert result["success"] is False
        assert captured == []

    async def test_send_text_broadcasts_with_web_client(self, channel, captured):
        realtime_hub.subscribe(client_kind="web")
        result = json.loads(await channel.send_text("c1", "你好", session_id="s1"))
        assert result["success"] is True
        assert captured[0]["event"] == "reply"
        assert captured[0]["content"] == "你好"
        assert captured[0]["chat_id"] == "s1"

    async def test_desktop_client_alone_counts_offline(self, channel, captured):
        """webui 频道只认 web 客户端（desktop 订阅不计入在线判定）。"""
        realtime_hub.subscribe(client_kind="desktop")
        result = json.loads(await channel.send_text("c1", "你好"))
        assert result["success"] is False


class TestScopedBroadcast:
    @pytest.mark.parametrize("scope", ["group_qq:42", "user_telegram:42", "reflect_123", "", "user_42"])
    async def test_foreign_activity_does_not_enter_web_chat(self, channel, captured, scope):
        await channel._broadcast_scoped("turn_end", {"scope": scope, "chat_id": "default"})
        assert captured == []

    @pytest.mark.parametrize(("scope", "chat_id"), [("user_webui:web_user#chat1", "chat1"), ("user_webui:web_user", "default")])
    async def test_web_scope_owns_routing(self, channel, captured, scope, chat_id):
        await channel._broadcast_scoped("turn_end", {"scope": scope, "chat_id": "wrong"})
        assert captured[0]["chat_id"] == chat_id


class TestChatIdResolution:
    def test_session_id_kwarg_wins(self):
        assert WebUIChannel._resolve_chat_id("t", {"session_id": "s1"}) == "s1"

    def test_target_hash_suffix(self):
        assert WebUIChannel._resolve_chat_id("scope#c9", {}) == "c9"

    def test_empty_when_no_hint(self):
        assert WebUIChannel._resolve_chat_id("scope", {}) == ""
