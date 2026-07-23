"""QQ 媒体段解析与按需下载提示链路测试（不触网）。"""

from __future__ import annotations

import os

from agent.channel.schemas import MessageSegment, SegmentType
from agent.mind.tools.media_pipeline import MediaPipeline
from channels.qq.parser import _build_media_segment, _parse_message_segments_sync


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
    """同步消息解析中的 file 段。"""

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


class TestMediaPipelineHints:
    """MediaPipeline 三种提示形态。"""

    async def test_local_path_tag(self, tmp_path) -> None:
        real = tmp_path / "a.ogg"
        real.write_bytes(b"\x00")
        seg = MessageSegment(type=SegmentType.VOICE, file_path=str(real))
        results = await MediaPipeline().process_segments([seg])
        assert results == [f"[media_file:voice:{real}]"]

    async def test_url_hint(self) -> None:
        seg = MessageSegment(
            type=SegmentType.FILE,
            url="https://example.com/f.xlsx",
            file_name="f.xlsx",
        )
        results = await MediaPipeline().process_segments([seg])
        assert len(results) == 1
        assert "[media_file:file:未下载]" in results[0]
        assert 'web_download(url="https://example.com/f.xlsx"' in results[0]

    async def test_file_id_hint(self) -> None:
        seg = MessageSegment(
            type=SegmentType.FILE,
            file_id="/fid-9",
            file_name="f.xlsx",
        )
        results = await MediaPipeline().process_segments([seg])
        assert len(results) == 1
        assert 'qq_download_file(file_id="/fid-9")' in results[0]

    async def test_voice_url_prefers_transcribe(self) -> None:
        seg = MessageSegment(type=SegmentType.VOICE, url="https://example.com/a.amr")
        results = await MediaPipeline().process_segments([seg])
        assert 'voice_to_text(url="https://example.com/a.amr")' in results[0]

    async def test_empty_segment_skipped(self) -> None:
        seg = MessageSegment(type=SegmentType.FILE)
        assert await MediaPipeline().process_segments([seg]) == []


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
