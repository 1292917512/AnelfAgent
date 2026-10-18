"""QQ 媒体段解析与按需下载提示链路测试（不触网）。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, Mock

from agent.channel.schemas import (
    AdapterChannel,
    ChannelType,
    MessageSegment,
    SegmentType,
    SendRequest,
    SendSegment,
)
from agent.mind.execution.media_pipeline import process_segments
from channels.qq.adapter import OneBotV11Channel
from channels.qq.parser import (
    _auto_download_to_me_images,
    _build_media_segment,
    _parse_message_event_async,
    _parse_message_segments_sync,
)
from channels.qq.send import QQSender


class TestBuildMediaSegment:
    """_build_media_segment 的 file 字段三分支。"""

    def test_bare_filename_goes_to_file_name(self) -> None:
        seg = _build_media_segment(SegmentType.FILE, {
            "file": "vlookup-7.13.xlsx",
            "name": "vlookup-7.13.xlsx",
            "file_id": "/abc-123",
            "size": 20480,
        })
        assert seg.file_path == ""
        assert seg.file_name == "vlookup-7.13.xlsx"
        assert seg.file_id == "/abc-123"
        assert seg.file_size == 20480

    def test_existing_local_path_used_as_file_path(self, tmp_path) -> None:
        real = tmp_path / "photo.jpg"
        real.write_bytes(b"\x00")
        seg = _build_media_segment(SegmentType.IMAGE, {
            "file": f"file://{real}",
            "url": "https://example.com/photo.jpg",
        })
        assert seg.file_path == str(real)
        assert seg.url == "https://example.com/photo.jpg"

    def test_file_field_fallback_to_file_name(self) -> None:
        seg = _build_media_segment(SegmentType.IMAGE, {"file": "abc.jpg"})
        assert seg.file_path == ""
        assert seg.file_name == "abc.jpg"

    def test_bad_size_tolerated(self) -> None:
        seg = _build_media_segment(SegmentType.VIDEO, {"file": "v.mp4", "size": "bad"})
        assert seg.file_size == 0


class TestParseMessageSegmentsSync:
    """同步消息解析段契约。"""

    def test_file_segment_keeps_file_id(self) -> None:
        _, segments = _parse_message_segments_sync([
            {"type": "file", "data": {
                "file": "vlookup-7.13.xlsx",
                "name": "vlookup-7.13.xlsx",
                "file_id": "/fid-1",
                "size": 1024,
            }},
        ])
        assert len(segments) == 1
        seg = segments[0]
        assert seg.type == SegmentType.FILE
        assert seg.file_path == ""
        assert seg.file_id == "/fid-1"
        assert seg.file_name == "vlookup-7.13.xlsx"

    def test_at_segment_uses_standard_tag(self) -> None:
        content, segments = _parse_message_segments_sync(
            [{"type": "at", "data": {"qq": 42, "name": "成员"}}],
            group_id="100",
            self_id="999",
        )
        assert content == "[at_uid:42]"
        assert segments[0].content == content


class _SenderChannelStub:
    """QQSender 的最小 API 频道替身。"""

    def __init__(self) -> None:
        self._self_id = "10086"
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _call_api_data(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action, params))
        return {"message_id": 123}


class TestQQSenderOutbound:
    """QQ 出站文本与图片 caption 共用 at 段转换契约。"""

    async def test_photo_caption_matches_text_at_segments(self) -> None:
        ch = _SenderChannelStub()
        sender = QQSender(ch)  # type: ignore[arg-type]
        caption = "请看[at_uid:1207435368]这里"

        await sender.send_photo(
            "1104224649", "https://example.com/image.png",
            caption=caption, channel_type="group",
        )
        await sender.send_text("1104224649", caption, channel_type="group")

        photo_action, photo_params = ch.calls[0]
        text_action, text_params = ch.calls[1]
        assert photo_action == text_action == "send_group_msg"
        photo_message = photo_params["message"]
        assert photo_message[0]["type"] == "image"
        assert photo_message[1:] == text_params["message"]
        assert {"type": "at", "data": {"qq": "1207435368"}} in photo_message
        assert all(
            "[at_uid:" not in segment["data"].get("text", "")
            for segment in photo_message
        )

    def test_filtered_self_at_does_not_restore_raw_tag(self) -> None:
        sender = QQSender(_SenderChannelStub())  # type: ignore[arg-type]
        for channel_type in ("group", "private"):
            assert sender._parse_at_in_text("[at_uid:10086]", channel_type) == []


class TestQQForwardMessage:
    """统一 SendRequest 到 OneBot API 的群聊路由与 caption 契约。"""

    async def test_group_image_preserves_channel_type_and_converts_at(self) -> None:
        ch = OneBotV11Channel()
        api_mock = AsyncMock(return_value={"message_id": 456})
        ch._call_api_data = api_mock  # type: ignore[method-assign]
        request = SendRequest(
            adapter_key="qq",
            channel=AdapterChannel(
                channel_id="1104224649", channel_type=ChannelType.GROUP
            ),
            segments=[SendSegment(
                type=SegmentType.IMAGE,
                content="https://example.com/image.png",
                caption="[at_uid:1207435368] 看图",
            )],
        )

        response = await ch.forward_message(request)

        assert response.success is True
        call = api_mock.await_args
        assert call is not None
        action, params = call.args
        assert action == "send_group_msg"
        assert params["group_id"] == 1104224649
        assert params["message"][1] == {
            "type": "at", "data": {"qq": "1207435368"}
        }


class TestMediaPipelineHints:
    """媒体段的三种提示形态。"""

    async def test_local_path_tag(self, tmp_path) -> None:
        real = tmp_path / "a.ogg"
        real.write_bytes(b"\x00")
        seg = MessageSegment(type=SegmentType.VOICE, file_path=str(real))
        results = await process_segments([seg])
        from core.tags import etag_all
        assert len(results) == 1
        assert etag_all(results[0]) == [("media_file", f"voice:{real.as_posix()}")]

    async def test_url_hint(self) -> None:
        seg = MessageSegment(
            type=SegmentType.FILE,
            url="https://example.com/f.xlsx",
            file_name="f.xlsx",
        )
        results = await process_segments([seg])
        assert len(results) == 1
        assert "[media_file:file:未下载]" in results[0]
        assert 'web_download(url="https://example.com/f.xlsx"' in results[0]

    async def test_file_id_hint(self) -> None:
        seg = MessageSegment(
            type=SegmentType.FILE,
            file_id="/fid-9",
            file_name="f.xlsx",
        )
        results = await process_segments([seg])
        assert len(results) == 1
        assert 'qq_download_file(file_id="/fid-9")' in results[0]

    async def test_voice_url_prefers_transcribe(self) -> None:
        seg = MessageSegment(type=SegmentType.VOICE, url="https://example.com/a.amr")
        results = await process_segments([seg])
        assert 'voice_to_text(url="https://example.com/a.amr")' in results[0]

    async def test_empty_segment_skipped(self) -> None:
        seg = MessageSegment(type=SegmentType.FILE)
        assert await process_segments([seg]) == []

    async def test_image_index_and_face_worker_keep_inbound_scope(self, monkeypatch) -> None:
        from agent.mind.execution import media_pipeline

        indexed, face = Mock(), Mock()
        media_pipeline.image_index_submit_port.set(indexed)
        monkeypatch.setattr("agent.vision.face.worker.submit_face_image", face)
        seg = MessageSegment(type=SegmentType.IMAGE, url="https://example.com/photo.jpg")
        result = await process_segments([seg], scope="user_qq:123")
        indexed.assert_called_once_with(seg.url, "inbound")
        face.assert_called_once_with(seg.url, "inbound", "user_qq:123")
        assert len(result) == 1 and "[media_file:image:未下载]" in result[0]


class TestExtractImagesFallback:
    """_extract_images 假路径回退到 URL。"""

    def test_fake_file_path_falls_back_to_url(self) -> None:
        from agent.channel.manager import ChannelManager

        class _Msg:
            segments = [MessageSegment(
                type=SegmentType.IMAGE,
                file_path="not-exists-abc.jpg",
                url="https://example.com/a.jpg",
            )]

        images = ChannelManager._extract_images(_Msg())
        assert len(images) == 1
        assert images[0].data == "https://example.com/a.jpg"
        assert images[0].is_url is True

    def test_real_file_path_preferred(self, tmp_path) -> None:
        from agent.channel.manager import ChannelManager

        real = tmp_path / "img.jpg"
        real.write_bytes(b"\x00")

        class _Msg:
            segments = [MessageSegment(
                type=SegmentType.IMAGE,
                file_path=str(real),
                url="https://example.com/a.jpg",
            )]

        images = ChannelManager._extract_images(_Msg())
        assert len(images) == 1
        assert images[0].data == str(real)
        assert images[0].is_url is False


class TestDownloadFileFallback:
    """download_file 的 get_file → get_image → get_record 类型回退链。"""

    def _make_tools(self, responses: dict):
        from channels.qq.tools import QQToolsMixin

        class _Stub(QQToolsMixin):
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def _call_api_data(self, action, params):  # noqa: ANN001
                self.calls.append(action)
                return responses.get(action)

        return _Stub()

    def _patch_upload_dir(self, monkeypatch, tmp_path) -> None:  # noqa: ANN001
        import agent.channel.media as media

        monkeypatch.setattr(media, "get_upload_dir", lambda: str(tmp_path / "uploads"))

    async def test_get_file_hit_stops_chain(self, tmp_path, monkeypatch) -> None:
        self._patch_upload_dir(monkeypatch, tmp_path)
        real = tmp_path / "a.xlsx"
        real.write_bytes(b"\x00")
        tools = self._make_tools({"get_file": {"file": str(real), "file_name": "a.xlsx"}})

        result = json.loads(await tools.download_file("fid-1"))
        assert result["success"] is True
        assert "/uploads/file/" in result["path"].replace("\\", "/")
        assert tools.calls == ["get_file"]

    async def test_image_file_id_falls_back_to_get_image(self, tmp_path, monkeypatch) -> None:
        self._patch_upload_dir(monkeypatch, tmp_path)
        real = tmp_path / "pic.jpg"
        real.write_bytes(b"\x00")
        tools = self._make_tools({
            "get_file": None,
            "get_image": {"file": str(real), "file_name": "pic.jpg"},
        })

        result = json.loads(await tools.download_file("img-fid"))
        assert result["success"] is True
        assert "/uploads/image/" in result["path"].replace("\\", "/")
        assert result["name"].endswith("pic.jpg")
        assert tools.calls == ["get_file", "get_image"]

    async def test_voice_file_id_falls_back_to_get_record(self, tmp_path, monkeypatch) -> None:
        self._patch_upload_dir(monkeypatch, tmp_path)
        real = tmp_path / "v.mp3"
        real.write_bytes(b"\x00")
        tools = self._make_tools({
            "get_file": None,
            "get_image": None,
            "get_record": {"file": str(real)},
        })

        result = json.loads(await tools.download_file("voice-fid"))
        assert result["success"] is True
        assert "/uploads/voice/" in result["path"].replace("\\", "/")
        assert tools.calls == ["get_file", "get_image", "get_record"]

    async def test_all_attempts_fail_returns_error(self) -> None:
        tools = self._make_tools({})
        result = json.loads(await tools.download_file("bad-fid"))
        assert result["success"] is False
        assert "获取文件信息失败" in result["error"]


class TestAutoDownloadToMeImages:
    """is_to_me 场景图片自动下载。"""

    async def test_get_image_local_cache_backfills_file_path(self, tmp_path) -> None:
        real = tmp_path / "p.jpg"
        real.write_bytes(b"\x00")

        async def api_caller(action, params):  # noqa: ANN001
            assert action == "get_image"
            assert params == {"file": "img-fid"}
            return {"data": {"file": f"file://{real}"}}

        seg = MessageSegment(type=SegmentType.IMAGE, file_id="img-fid")
        await _auto_download_to_me_images([seg], api_caller)
        assert seg.file_path == str(real)

    async def test_failure_keeps_segment_untouched(self) -> None:
        async def api_caller(action, params):  # noqa: ANN001
            return None

        seg = MessageSegment(type=SegmentType.IMAGE, file_id="img-fid")
        await _auto_download_to_me_images([seg], api_caller)
        assert seg.file_path == ""

    async def test_existing_local_path_not_re_fetched(self, tmp_path) -> None:
        real = tmp_path / "p.jpg"
        real.write_bytes(b"\x00")
        called = False

        async def api_caller(action, params):  # noqa: ANN001
            nonlocal called
            called = True
            return None

        seg = MessageSegment(type=SegmentType.IMAGE, file_path=str(real))
        await _auto_download_to_me_images([seg], api_caller)
        assert called is False


class TestParseEventAutoDownload:
    """@bot 消息（含回复图片消息）在解析阶段自动缓存图片。"""

    def _group_event(self, message: list) -> dict:
        return {
            "post_type": "message",
            "message_type": "group",
            "group_id": 100,
            "user_id": 42,
            "message_id": 7,
            "self_id": 999,
            "sender": {"nickname": "n"},
            "message": message,
            "time": 1,
        }

    async def test_at_bot_message_image_auto_downloaded(self, tmp_path) -> None:
        real = tmp_path / "p.jpg"
        real.write_bytes(b"\x00")

        async def api_caller(action, params):  # noqa: ANN001
            if action == "get_image":
                return {"data": {"file": str(real)}}
            return None

        data = self._group_event([
            {"type": "at", "data": {"qq": "999"}},
            {"type": "image", "data": {"file": "p.jpg", "file_id": "img-fid"}},
        ])
        msg = await _parse_message_event_async(data, api_caller)
        assert msg.is_to_me is True
        images = [s for s in msg.segments if s.type == SegmentType.IMAGE]
        assert len(images) == 1
        assert images[0].file_path == str(real)

    async def test_reply_image_appended_when_at_bot(self, tmp_path) -> None:
        real = tmp_path / "r.jpg"
        real.write_bytes(b"\x00")

        async def api_caller(action, params):  # noqa: ANN001
            if action == "get_msg":
                return {"data": {
                    "message": [{"type": "image", "data": {"file": "r.jpg", "file_id": "r-fid"}}],
                    "sender": {"nickname": "m"},
                }}
            if action == "get_image":
                return {"data": {"file": str(real)}}
            return None

        data = self._group_event([
            {"type": "reply", "data": {"id": "55"}},
            {"type": "at", "data": {"qq": "999"}},
            {"type": "text", "data": {"text": "看下这张"}},
        ])
        msg = await _parse_message_event_async(data, api_caller)
        assert msg.is_to_me is True
        assert msg.reply_content.startswith("m:")
        images = [s for s in msg.segments if s.type == SegmentType.IMAGE]
        assert len(images) == 1
        assert images[0].file_path == str(real)

    async def test_not_to_me_skips_auto_download(self) -> None:
        called = False

        async def api_caller(action, params):  # noqa: ANN001
            nonlocal called
            if action == "get_image":
                called = True
            return None

        data = self._group_event([
            {"type": "text", "data": {"text": "hi"}},
            {"type": "image", "data": {"file": "p.jpg", "file_id": "img-fid"}},
        ])
        msg = await _parse_message_event_async(data, api_caller)
        assert msg.is_to_me is False
        assert called is False
        images = [s for s in msg.segments if s.type == SegmentType.IMAGE]
        assert images[0].file_path == ""


class TestEmptyMessageDropped:
    """无内容的空消息事件（NapCat 灰条回执）解析阶段直接丢弃。"""

    def _private_event(self, message) -> dict:  # noqa: ANN001
        return {
            "post_type": "message",
            "message_type": "private",
            "user_id": 42,
            "message_id": 7,
            "self_id": 999,
            "sender": {"nickname": "n"},
            "message": message,
            "time": 1,
        }

    async def test_empty_segment_list_dropped(self) -> None:
        msg = await _parse_message_event_async(self._private_event([]), None)
        assert msg is None

    async def test_whitespace_text_dropped(self) -> None:
        msg = await _parse_message_event_async(
            self._private_event([{"type": "text", "data": {"text": "  "}}]), None
        )
        assert msg is None

    async def test_media_segment_kept(self) -> None:
        msg = await _parse_message_event_async(
            self._private_event([
                {"type": "image", "data": {"file": "p.jpg", "file_id": "fid"}},
            ]),
            None,
        )
        assert msg is not None
        assert msg.segments[0].type == SegmentType.IMAGE
