"""音乐协议适配器（agent.llm.music_adapters）与 MiniMax 图片适配器单元测试。"""

from __future__ import annotations

import pytest

from agent.llm.image_adapters import MiniMaxImagesAdapter, resolve_image_adapter
from agent.llm.music_adapters import (
    MiniMaxMusicAdapter,
    MusicParams,
    resolve_music_adapter,
)


class TestMiniMaxMusicAdapter:
    def test_music_request_with_lyrics(self) -> None:
        adapter = MiniMaxMusicAdapter()
        req = adapter.build_music_request(
            "https://api.minimaxi.com/anthropic",
            MusicParams(model="music-3.0", prompt="流行", lyrics="[Verse]\n你好"),
        )
        assert req.url == "https://api.minimaxi.com/v1/music_generation"
        payload = req.payload or {}
        assert payload["model"] == "music-3.0"
        assert payload["prompt"] == "流行"
        assert payload["lyrics"] == "[Verse]\n你好"
        assert payload["output_format"] == "hex"
        assert "is_instrumental" not in payload

    def test_instrumental_and_cover_params(self) -> None:
        adapter = MiniMaxMusicAdapter()
        req = adapter.build_music_request(
            "https://api.minimaxi.com/v1",
            MusicParams(model="music-3.0", prompt="纯音乐", is_instrumental=True),
        )
        assert (req.payload or {})["is_instrumental"] is True
        req2 = adapter.build_music_request(
            "https://api.minimaxi.com/v1",
            MusicParams(model="music-cover", prompt="翻唱", cover_feature_id="fid-1"),
        )
        assert (req2.payload or {})["cover_feature_id"] == "fid-1"

    def test_extract_music(self) -> None:
        adapter = MiniMaxMusicAdapter()
        result = {
            "data": {"audio": b"song".hex(), "status": 2},
            "extra_info": {"music_duration": 25364},
            "base_resp": {"status_code": 0},
        }
        music = adapter.extract_music(result)
        assert music.audio == b"song"
        assert music.extra_info["music_duration"] == 25364

    def test_base_resp_error(self) -> None:
        adapter = MiniMaxMusicAdapter()
        with pytest.raises(RuntimeError, match="1008"):
            adapter.extract_music({"base_resp": {"status_code": 1008, "status_msg": "余额不足"}})

    def test_lyrics(self) -> None:
        adapter = MiniMaxMusicAdapter()
        req = adapter.build_lyrics_request(
            "https://api.minimaxi.com/v1", mode="write_full_song", prompt="夏日海滩",
        )
        payload = req.payload or {}
        assert req.url == "https://api.minimaxi.com/v1/lyrics_generation"
        assert payload == {"mode": "write_full_song", "prompt": "夏日海滩"}
        parsed = adapter.parse_lyrics({
            "song_title": "海滩", "style_tags": "流行,夏日", "lyrics": "[Verse]\n...",
            "base_resp": {"status_code": 0},
        })
        assert parsed["song_title"] == "海滩"
        assert parsed["lyrics"].startswith("[Verse]")

    def test_cover_preprocess(self) -> None:
        adapter = MiniMaxMusicAdapter()
        req = adapter.build_cover_preprocess_request(
            "https://api.minimaxi.com/v1", audio_url="https://x/a.mp3",
        )
        payload = req.payload or {}
        assert req.url == "https://api.minimaxi.com/v1/music_cover_preprocess"
        assert payload == {"model": "music-cover", "audio_url": "https://x/a.mp3"}
        with pytest.raises(ValueError):
            adapter.build_cover_preprocess_request("https://api.minimaxi.com/v1")
        parsed = adapter.parse_cover_preprocess({
            "cover_feature_id": "fid-1", "formatted_lyrics": "[Verse]\n...",
            "audio_duration": 30.5, "base_resp": {"status_code": 0},
        })
        assert parsed["cover_feature_id"] == "fid-1"
        assert parsed["audio_duration"] == 30.5


class TestResolveMusicAdapter:
    def test_minimax_host(self) -> None:
        assert resolve_music_adapter("https://api.minimaxi.com/anthropic").name == "minimax"

    def test_unsupported_provider(self) -> None:
        with pytest.raises(NotImplementedError):
            resolve_music_adapter("https://api.siliconflow.cn/v1")


class TestMiniMaxImagesAdapter:
    def test_generate_request(self) -> None:
        adapter = MiniMaxImagesAdapter()
        req = adapter.build_generate_request(
            "https://api.minimaxi.com/anthropic",
            model="image-01", prompt="猫", image_size="1280x720",
            num_inference_steps=20, cfg=None,
        )
        assert req.url == "https://api.minimaxi.com/v1/image_generation"
        payload = req.payload
        assert payload["model"] == "image-01"
        assert payload["aspect_ratio"] == "16:9"
        assert payload["response_format"] == "url"

    def test_aspect_ratio_mapping(self) -> None:
        assert MiniMaxImagesAdapter._aspect_ratio("1024x1024") == "1:1"
        assert MiniMaxImagesAdapter._aspect_ratio("928x1664") == "9:16"
        assert MiniMaxImagesAdapter._aspect_ratio("bad") == "1:1"

    def test_edit_request_maps_subject_reference(self) -> None:
        adapter = MiniMaxImagesAdapter()
        req = adapter.build_edit_request(
            "https://api.minimaxi.com/v1",
            model="image-01", prompt="换装", image_content="data:image/png;base64,AA",
            num_inference_steps=20, cfg=4.0,
        )
        assert req.payload["subject_reference"] == [
            {"type": "character", "image_file": "data:image/png;base64,AA"}
        ]

    def test_extract_urls(self) -> None:
        adapter = MiniMaxImagesAdapter()
        result = {
            "data": {"image_urls": ["https://cdn/a.png"]},
            "metadata": {"success_count": 1, "failed_count": 0},
            "base_resp": {"status_code": 0},
        }
        assert adapter.extract_urls(result) == ["https://cdn/a.png"]
        b64 = {"data": {"image_base64": ["QUJD"]}, "base_resp": {"status_code": 0}}
        assert adapter.extract_urls(b64) == ["data:image/png;base64,QUJD"]
        with pytest.raises(RuntimeError, match="1026"):
            adapter.extract_urls({"base_resp": {"status_code": 1026, "status_msg": "敏感"}})

    def test_resolve_host_rule(self) -> None:
        assert resolve_image_adapter("https://api.minimaxi.com/anthropic").name == "minimax"
