"""ASR 协议适配器（agent.llm.asr_adapters）单元测试。"""

from __future__ import annotations

import base64

from agent.llm.asr_adapters import (
    DashScopeAsrAdapter,
    OpenAIAsrAdapter,
    resolve_asr_adapter,
)

_AUDIO = b"\x00\x01\x02\x03"
_DASHSCOPE_BASE = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"


class TestOpenAIAsrAdapter:
    def test_multipart_request(self) -> None:
        adapter = OpenAIAsrAdapter()
        req = adapter.build_transcribe_request(
            "https://api.siliconflow.cn/v1",
            model="m", audio_data=_AUDIO, file_name="a.wav", mime_type="audio/wav",
        )
        assert req.url == "https://api.siliconflow.cn/v1/audio/transcriptions"
        assert req.files == {"file": ("a.wav", _AUDIO, "audio/wav")}
        assert req.payload == {"model": "m"}

    def test_empty_model_omits_field(self) -> None:
        adapter = OpenAIAsrAdapter()
        req = adapter.build_transcribe_request(
            "https://x/v1", model="", audio_data=_AUDIO, file_name="a.mp3", mime_type="audio/mpeg",
        )
        assert req.payload == {}

    def test_extract_text(self) -> None:
        assert OpenAIAsrAdapter().extract_text({"text": "你好"}) == "你好"
        assert OpenAIAsrAdapter().extract_text({}) == ""


class TestDashScopeAsrAdapter:
    def test_request_uses_host_root_and_inline_audio(self) -> None:
        adapter = DashScopeAsrAdapter()
        req = adapter.build_transcribe_request(
            _DASHSCOPE_BASE,
            model="qwen-audio-3.0-asr-flash", audio_data=_AUDIO,
            file_name="a.wav", mime_type="audio/wav",
        )
        assert req.url == (
            "https://token-plan.cn-beijing.maas.aliyuncs.com"
            "/api/v1/services/aigc/multimodal-generation/generation"
        )
        assert req.files is None
        payload = req.payload or {}
        assert payload["model"] == "qwen-audio-3.0-asr-flash"
        assert payload["parameters"]["format"] == "wav"
        content = payload["input"]["messages"][0]["content"]
        assert content[0]["type"] == "input_audio"
        expected_uri = f"data:audio/wav;base64,{base64.b64encode(_AUDIO).decode()}"
        assert content[0]["input_audio"]["data"] == expected_uri

    def test_format_falls_back_to_ext(self) -> None:
        adapter = DashScopeAsrAdapter()
        req = adapter.build_transcribe_request(
            _DASHSCOPE_BASE, model="m", audio_data=_AUDIO, file_name="noext", mime_type="audio/mpeg",
        )
        assert (req.payload or {})["parameters"]["format"] == "mp3"

    def test_extract_text_variants(self) -> None:
        adapter = DashScopeAsrAdapter()
        # 实测响应：顶层 sentence 对象
        assert adapter.extract_text({"sentence": {"text": "你好"}}) == "你好"
        # output.sentence / output.text 变体
        assert adapter.extract_text({"output": {"sentence": {"text": "你好"}}}) == "你好"
        assert adapter.extract_text({"output": {"text": "你好"}}) == "你好"
        # OpenAI 兼容 chat 模式变体
        choices = {"output": {"choices": [{"message": {"content": "你好"}}]}}
        assert adapter.extract_text(choices) == "你好"
        list_content = {"output": {"choices": [{"message": {"content": [{"text": "你"}, {"text": "好"}]}}]}}
        assert adapter.extract_text(list_content) == "你好"
        assert adapter.extract_text({}) == ""


class TestResolveAsrAdapter:
    def test_default_and_host_rule(self) -> None:
        assert resolve_asr_adapter("https://api.siliconflow.cn/v1").name == "openai"
        assert resolve_asr_adapter(_DASHSCOPE_BASE).name == "dashscope"

    def test_explicit_protocol(self) -> None:
        assert resolve_asr_adapter("https://x/v1", "dashscope").name == "dashscope"

    def test_other_kind_protocol_falls_back_to_host(self) -> None:
        # media_protocol 为其他媒体类协议名时，ASR 解析回退 host 规则而非报错
        assert resolve_asr_adapter(_DASHSCOPE_BASE, "minimax_v2").name == "dashscope"
        assert resolve_asr_adapter("https://x/v1", "minimax_v2").name == "openai"
