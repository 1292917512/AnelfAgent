"""语音协议适配器（agent.llm.speech_adapters）单元测试。"""

from __future__ import annotations

import pytest

from agent.llm.speech_adapters import (
    MiniMaxSpeechAdapter,
    OpenAISpeechAdapter,
    SpeechParams,
    resolve_speech_adapter,
)


def _params(**overrides: object) -> SpeechParams:
    base: dict = {"model": "m", "text": "你好"}
    base.update(overrides)
    return SpeechParams(**base)  # type: ignore[arg-type]


class TestOpenAISpeechAdapter:
    def test_voice_format(self) -> None:
        adapter = OpenAISpeechAdapter()
        assert adapter.binary_response
        req = adapter.build_tts_request("https://api.siliconflow.cn/v1", _params(voice="anna"))
        payload = req.payload or {}
        assert req.url == "https://api.siliconflow.cn/v1/audio/speech"
        assert payload["voice"] == "m:anna"
        assert payload["input"] == "你好"

    def test_default_voice_and_references(self) -> None:
        adapter = OpenAISpeechAdapter()
        req = adapter.build_tts_request("https://x/v1", _params())
        assert (req.payload or {})["voice"] == "m:alex"
        refs = [{"audio": "data:audio/mpeg;base64,AA", "text": "t"}]
        req2 = adapter.build_tts_request("https://x/v1", _params(references=refs))
        assert (req2.payload or {})["references"] == refs
        assert "voice" not in (req2.payload or {})

    def test_voice_mgmt_unsupported(self) -> None:
        adapter = OpenAISpeechAdapter()
        assert not adapter.supports_voice_mgmt
        assert not adapter.supports_async
        with pytest.raises(NotImplementedError):
            adapter.build_get_voice_request("https://x/v1", voice_type="all")


class TestMiniMaxSpeechAdapter:
    def test_sync_tts_request_and_hex_audio(self) -> None:
        adapter = MiniMaxSpeechAdapter()
        req = adapter.build_tts_request(
            "https://api.minimaxi.com/anthropic",
            _params(model="speech-2.8-hd", voice="female-yujie", emotion="happy", speed=1.2),
        )
        assert req.url == "https://api.minimaxi.com/v1/t2a_v2"
        payload = req.payload or {}
        assert payload["model"] == "speech-2.8-hd"
        assert payload["voice_setting"]["voice_id"] == "female-yujie"
        assert payload["voice_setting"]["emotion"] == "happy"
        assert payload["voice_setting"]["speed"] == 1.2
        assert payload["output_format"] == "hex"

        result = {
            "data": {"audio": b"abc".hex(), "status": 2},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }
        assert adapter.extract_audio(result) == b"abc"

    def test_default_voice(self) -> None:
        adapter = MiniMaxSpeechAdapter()
        req = adapter.build_tts_request("https://api.minimaxi.com/v1", _params())
        assert (req.payload or {})["voice_setting"]["voice_id"] == "male-qn-qingse"

    def test_base_resp_error(self) -> None:
        adapter = MiniMaxSpeechAdapter()
        with pytest.raises(RuntimeError, match="1004"):
            adapter.extract_audio({"base_resp": {"status_code": 1004, "status_msg": "鉴权失败"}})

    def test_async_flow(self) -> None:
        adapter = MiniMaxSpeechAdapter()
        assert adapter.supports_async
        req = adapter.build_async_create_request("https://api.minimaxi.com/v1", _params())
        assert req.url == "https://api.minimaxi.com/v1/t2a_async_v2"
        task_id = adapter.extract_async_task_id(
            {"task_id": 123, "base_resp": {"status_code": 0}}
        )
        assert task_id == "123"

        qreq = adapter.build_async_query_request("https://api.minimaxi.com/v1", "123")
        assert qreq.method == "GET"
        assert qreq.params == {"task_id": "123"}

        processing = adapter.parse_async_query({"status": "Processing", "base_resp": {"status_code": 0}})
        assert processing.status == "processing"
        done = adapter.parse_async_query(
            {"status": "Success", "file_id": 456, "base_resp": {"status_code": 0}}
        )
        assert done.status == "succeeded"
        assert done.file_id == "456"
        failed = adapter.parse_async_query(
            {"status": "Failed", "base_resp": {"status_code": 0}}
        )
        assert failed.status == "failed"

        rreq = adapter.build_retrieve_request("https://api.minimaxi.com/v1", "456")
        assert rreq.url == "https://api.minimaxi.com/v1/files/retrieve"
        url = adapter.extract_download_url(
            {"file": {"download_url": "https://cdn/a.mp3"}, "base_resp": {"status_code": 0}}
        )
        assert url == "https://cdn/a.mp3"

    def test_voice_clone_request(self) -> None:
        adapter = MiniMaxSpeechAdapter()
        req = adapter.build_voice_clone_request(
            "https://api.minimaxi.com/v1",
            file_id=111, voice_id="myvoice01", preview_text="试听", model="speech-2.8-hd",
        )
        payload = req.payload or {}
        assert req.url == "https://api.minimaxi.com/v1/voice_clone"
        assert payload["file_id"] == 111
        assert payload["voice_id"] == "myvoice01"
        assert payload["text"] == "试听"
        assert payload["model"] == "speech-2.8-hd"
        parsed = adapter.parse_voice_clone(
            {"demo_audio": "https://cdn/demo.mp3", "base_resp": {"status_code": 0}}
        )
        assert parsed["demo_audio"] == "https://cdn/demo.mp3"

    def test_voice_design(self) -> None:
        adapter = MiniMaxSpeechAdapter()
        req = adapter.build_voice_design_request(
            "https://api.minimaxi.com/v1", prompt="低沉磁性男声", preview_text="你好",
        )
        assert (req.payload or {})["prompt"] == "低沉磁性男声"
        result = {
            "voice_id": "designed-1",
            "trial_audio": b"xy".hex(),
            "base_resp": {"status_code": 0},
        }
        parsed = adapter.parse_voice_design(result)
        assert parsed["voice_id"] == "designed-1"
        assert parsed["trial_audio"] == b"xy"

    def test_get_and_delete_voice(self) -> None:
        adapter = MiniMaxSpeechAdapter()
        req = adapter.build_get_voice_request("https://api.minimaxi.com/v1", voice_type="all")
        assert (req.payload or {})["voice_type"] == "all"
        parsed = adapter.parse_get_voice(
            {"system_voice": [{"voice_id": "v1"}], "base_resp": {"status_code": 0}}
        )
        assert parsed["system_voice"][0]["voice_id"] == "v1"

        dreq = adapter.build_delete_voice_request(
            "https://api.minimaxi.com/v1", voice_type="voice_cloning", voice_id="v1",
        )
        assert (dreq.payload or {})["voice_id"] == "v1"
        dparsed = adapter.parse_delete_voice(
            {"voice_id": "v1", "base_resp": {"status_code": 0}}
        )
        assert dparsed["voice_id"] == "v1"


class TestResolveSpeechAdapter:
    def test_default_and_host_rule(self) -> None:
        assert resolve_speech_adapter("https://api.siliconflow.cn/v1").name == "openai"
        assert resolve_speech_adapter("https://api.minimaxi.com/anthropic").name == "minimax"

    def test_unknown_protocol_falls_back_to_host(self) -> None:
        # media_protocol 为视频协议时，语音解析回退 host 规则而非报错
        assert resolve_speech_adapter("https://api.minimaxi.com/v1", "minimax_v2").name == "minimax"
        assert resolve_speech_adapter("https://x/v1", "minimax_v2").name == "openai"
