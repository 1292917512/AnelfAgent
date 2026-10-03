"""WebUI 频道测试：media 帧 URL 契约、delta 合帧、在线判定、广播路径。"""

from __future__ import annotations

import json

import pytest

from channels.webui.adapter import WebUIChannel, _media_frame_url
from core import realtime_hub


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

    async def _bcast_scoped(event: str, payload: dict) -> None:
        scope = str(payload.get("scope") or "")
        chat_id = str(payload.get("chat_id") or "")
        if not chat_id and "#" in scope:
            chat_id = scope.split("#", 1)[1]
        frames.append({"event": event, **payload, "chat_id": chat_id})

    monkeypatch.setattr(WebUIChannel, "_broadcast", staticmethod(_bcast))
    monkeypatch.setattr(WebUIChannel, "_broadcast_scoped", staticmethod(_bcast_scoped))
    return frames


class TestMediaFrameUrl:
    def test_http_url_passthrough(self):
        assert _media_frame_url("https://x.com/a.png") == "https://x.com/a.png"

    def test_api_url_passthrough(self):
        assert _media_frame_url("/api/chat/files/image/a.png") == "/api/chat/files/image/a.png"

    def test_upload_path_rewritten_to_api_url(self):
        """上传目录下的本地绝对路径改写为可服务 URL（不再原样出网）。"""
        from core.path import ConfigPaths
        local = f"{ConfigPaths.UPLOAD_DIR}/image/123_a.png"
        assert _media_frame_url(local) == "/api/chat/files/image/123_a.png"

    def test_voice_upload_path_rewritten(self):
        from core.path import ConfigPaths
        local = f"{ConfigPaths.UPLOAD_DIR}/voice/456.wav"
        assert _media_frame_url(local) == "/api/chat/files/voice/456.wav"

    def test_foreign_path_passthrough(self):
        assert _media_frame_url("/etc/passwd") == "/etc/passwd"

    async def test_send_photo_uses_rewritten_url(self, channel, captured):
        from core.path import ConfigPaths
        realtime_hub.subscribe(client_kind="web")
        await channel.send_photo("c1", f"{ConfigPaths.UPLOAD_DIR}/image/x.png")
        assert captured[0]["url"] == "/api/chat/files/image/x.png"

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


class TestDeltaMerge:
    async def test_reasoning_flushed_before_text(self, channel, captured):
        """50ms 合帧后 reasoning 先于 text 发射（前端渲染顺序契约）。"""
        import asyncio
        await channel._on_assistant_delta({"turn_id": "t1", "delta": "正文", "scope": "s"})
        await channel._on_assistant_delta({"turn_id": "t1", "delta": "思考", "reasoning": True, "scope": "s"})
        await asyncio.sleep(0.08)
        kinds = [(f["event"], f.get("reasoning")) for f in captured]
        assert kinds == [("delta", True), ("delta", False)]

    async def test_reset_frame_clears_buffer(self, channel, captured):
        import asyncio
        await channel._on_assistant_delta({"turn_id": "t1", "delta": "残留", "scope": "s"})
        await channel._on_assistant_delta({"turn_id": "t1", "reset": True, "scope": "s"})
        await asyncio.sleep(0.08)
        assert len(captured) == 1
        assert captured[0]["reset"] is True


class TestChatIdResolution:
    def test_session_id_kwarg_wins(self):
        assert WebUIChannel._resolve_chat_id("t", {"session_id": "s1"}) == "s1"

    def test_target_hash_suffix(self):
        assert WebUIChannel._resolve_chat_id("scope#c9", {}) == "c9"

    def test_empty_when_no_hint(self):
        assert WebUIChannel._resolve_chat_id("scope", {}) == ""


class TestFileDiffFrame:
    """file_diff 出帧契约：路径统一为工作区相对（前端树/编辑器/改动集的共同键）。"""

    @pytest.fixture
    def ws_root(self, tmp_path):
        root = tmp_path / "ws"
        root.mkdir()
        from core.config import ConfigManager
        ConfigManager.set("workspace_root", str(root))
        return root

    async def test_absolute_path_relativized(self, channel, captured, ws_root):
        await channel._on_file_diff({
            "scope": "s", "path": str(ws_root / "notes" / "a.md"),
            "diff": "d", "additions": 2, "removals": 1,
        })
        assert captured[0]["path"] == "notes/a.md"
        assert captured[0]["additions"] == 2
        assert captured[0]["move_from"] is None

    async def test_move_from_relativized(self, channel, captured, ws_root):
        await channel._on_file_diff({
            "scope": "s", "path": str(ws_root / "b.md"),
            "move_from": str(ws_root / "sub" / "a.md"),
            "diff": "", "additions": 0, "removals": 0,
        })
        assert captured[0]["path"] == "b.md"
        assert captured[0]["move_from"] == "sub/a.md"

    async def test_outside_workspace_kept_absolute(self, channel, captured, ws_root, tmp_path):
        outside = str(tmp_path / "elsewhere" / "x.md")
        await channel._on_file_diff({
            "scope": "s", "path": outside, "diff": "d", "additions": 1, "removals": 0,
        })
        assert captured[0]["path"] == outside

    async def test_relative_path_passthrough(self, channel, captured, ws_root):
        await channel._on_file_diff({
            "scope": "s", "path": "already/rel.md", "diff": "d",
            "additions": 1, "removals": 0, "binary": True,
        })
        assert captured[0]["path"] == "already/rel.md"
        assert captured[0]["binary"] is True
