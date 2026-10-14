"""模块模型 SDK：类型路由、排序回退、取消、凭据归属与判断桥接。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from agent.llm.config import LLMClientConfig
from agent.llm.llm_client import LLMClient, LLMNotConfiguredError
from agent.llm.llm_manager import LLMManager
from agent.llm.media_client import MediaClient
from agent.llm.model_api import ImageContent, ModelAPI, ModelCallError, NoulQuestion, VideoContent
from agent.llm.types import ChatResult, ChatStreamDelta


@pytest.fixture
def manager(tmp_path: Path) -> LLMManager:
    return LLMManager(str(tmp_path / "models.json"))


def add(manager: LLMManager, name: str, kind: str, **options: object) -> LLMClient:
    client = LLMClient(LLMClientConfig(
        name=name, model=f"remote-{name}", base_url=f"https://{name}.example/v1",
        api_key=f"credential-{name}", model_types=[kind], **options,
    ))
    manager._clients[name] = client
    manager._type_priorities.setdefault(kind, []).append(name)
    return client


async def test_vision_prepares_once_off_loop_and_falls_back_after_stall_and_truncation(
    manager: LLMManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import threading

    from PIL import Image

    from agent.llm import image_utils
    from core.config import get_config_float

    path = tmp_path / "image.png"
    Image.new("RGB", (2000, 1500), (32, 96, 192)).save(path)
    first = add(manager, "stalled", "vision", supports_vision=True)
    second = add(manager, "truncated", "vision", supports_vision=True)
    third = add(manager, "complete", "vision", supports_vision=True)
    closed = asyncio.Event()
    attempts: list[str] = []
    payloads: list[list[dict]] = []
    threads: list[int] = []
    original = image_utils.optimize_for_vision

    def optimize(image: ImageContent) -> ImageContent:
        threads.append(threading.get_ident())
        return original(image)

    async def stalled(messages: list[dict], **kwargs: object):
        attempts.append("stalled")
        payloads.append(messages)
        try:
            yield ChatStreamDelta()
            await asyncio.sleep(10)
        finally:
            closed.set()

    async def truncated(messages: list[dict], **kwargs: object):
        attempts.append("truncated")
        payloads.append(messages)
        yield ChatStreamDelta(content="一部分", finish_reason="length")

    async def complete(messages: list[dict], **kwargs: object):
        attempts.append("complete")
        payloads.append(messages)
        yield ChatStreamDelta(content="完整识别结果", finish_reason="stop")

    monkeypatch.setattr(image_utils, "optimize_for_vision", optimize)
    monkeypatch.setattr(first, "chat_stream", stalled)
    monkeypatch.setattr(second, "chat_stream", truncated)
    monkeypatch.setattr(third, "chat_stream", complete)
    monkeypatch.setattr("core.config.get_config_float", lambda key, default: (
        0.03 if key == "describe_image_first_response_timeout" else get_config_float(key, default)
    ))
    result = await ModelAPI(manager).describe_images([ImageContent(str(path))], prompt="分析图片")
    assert result.value == "完整识别结果" and result.model_id == "complete"
    assert attempts == ["stalled", "truncated", "complete"] and closed.is_set()
    assert len(threads) == 1 and threads[0] != threading.get_ident()
    assert payloads[0] == payloads[1] == payloads[2]
    assert payloads[0][0]["content"][0]["text"] == "分析图片"


def test_channel_and_entity_exports_share_one_lazy_facade() -> None:
    from agent.channel import get_model_api as channel_api
    from entities._sdk import get_model_api as entity_api

    assert channel_api is entity_api
    assert channel_api() is entity_api()


async def test_chat_uses_compatible_candidates_and_hot_priority(manager: LLMManager) -> None:
    disabled = add(manager, "disabled", "chat", enabled=False)
    no_tools = add(manager, "no-tools", "chat", supports_tools=False)
    first = add(manager, "first", "chat")
    second = add(manager, "second", "chat")
    for client in (disabled, no_tools, first):
        client.chat = AsyncMock(side_effect=RuntimeError("offline"))
    second.chat = AsyncMock(return_value=ChatResult(content="ok"))
    api = ModelAPI(manager)
    messages = [{"role": "user", "content": "hi"}]
    tools = [{"type": "function", "function": {"name": "do_work"}}]
    result = await api.chat(messages, tools=tools, max_retries=0)
    assert result.content == "ok"
    disabled.chat.assert_not_awaited()
    no_tools.chat.assert_not_awaited()
    first.chat.assert_awaited_once()
    second.chat.assert_awaited_once()
    assert second.chat.call_args.args[0] == messages
    assert second.chat.call_args.kwargs["tools"] == tools

    manager._type_priorities["chat"] = ["second", "first"]
    assert api.list_models("chat") == ["second", "first"]
    await api.chat(messages, max_retries=0)
    first.chat.assert_awaited_once()
    assert second.chat.await_count == 2


async def test_vision_chat_does_not_escape_its_model_pool(manager: LLMManager) -> None:
    chat = add(manager, "chat", "chat", supports_vision=True)
    first = add(manager, "vision-a", "vision", supports_vision=True)
    second = add(manager, "vision-b", "vision", supports_vision=True)
    chat.chat = AsyncMock(side_effect=AssertionError("wrong pool"))
    first.chat = AsyncMock(side_effect=RuntimeError("offline"))
    second.chat = AsyncMock(return_value=ChatResult(content="image"))
    api = ModelAPI(manager)
    assert (await api.chat(
        [{"role": "user", "content": [ImageContent("aGk=").to_openai_block()]}],
        model_type="vision", max_retries=0,
    )).content == "image"
    chat.chat.assert_not_awaited()


async def test_explicit_model_is_validated_and_pinned(manager: LLMManager) -> None:
    primary = add(manager, "primary", "chat")
    fallback = add(manager, "fallback", "chat")
    primary.chat = AsyncMock(side_effect=RuntimeError("offline"))
    fallback.chat = AsyncMock()
    api = ModelAPI(manager)
    with pytest.raises(RuntimeError, match="offline"):
        await api.chat([], model_id="primary", max_retries=0)
    with pytest.raises(LLMNotConfiguredError):
        await api.chat([], model_id="unknown")
    with pytest.raises(LLMNotConfiguredError):
        await api.embed(["hello"], model_id="primary")
    primary.config.enabled = False
    with pytest.raises(LLMNotConfiguredError):
        await api.chat([], model_id="primary")
    fallback.chat.assert_not_awaited()


async def test_media_fallback_keeps_each_providers_credentials(
    manager: LLMManager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    add(manager, "disabled", "tts", enabled=False)
    add(manager, "first", "tts")
    add(manager, "second", "tts", extra_headers={"X-Project": "channel-test"})
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "first.example":
            return httpx.Response(503, json={"error": {"message": "temporarily unavailable"}})
        return httpx.Response(200, content=b"audio")

    monkeypatch.setattr(MediaClient, "_http_client", lambda *args, **kwargs:
                        httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    result = await ModelAPI(manager).text_to_speech("hello", voice="voice")
    assert result.value == b"audio"
    assert result.model_id == "second" and result.model == "remote-second"
    assert [request.url.host for request in requests] == ["first.example", "second.example"]
    assert [request.headers["Authorization"] for request in requests] == [
        "Bearer credential-first", "Bearer credential-second",
    ]
    assert b'remote-first' in requests[0].content and b'remote-second' in requests[1].content
    assert "X-Project" not in requests[0].headers
    assert requests[1].headers["X-Project"] == "channel-test"


@pytest.mark.parametrize(("kind", "method", "args", "kwargs", "value"), [
    ("asr", "transcribe", (b"audio",), {"file_name": "voice.wav"}, "text"),
    ("rerank", "rerank", ("query", ["doc"]), {"top_n": 1}, [{"index": 0}]),
    ("image_gen", "generate_image", ("draw",), {"image_size": "512x512"}, ["image"]),
    ("image_edit", "edit_image", ("edit",), {"image_path": "source.png"}, ["edited"]),
    ("video", "generate_video", ("video",), {"duration": 5}, "video-url"),
    ("music", "generate_music", (), {"lyrics": "song"}, "music-result"),
])
async def test_media_operations_use_their_own_type_chain(
    manager, monkeypatch, kind, method, args, kwargs, value,
) -> None:
    add(manager, "first", kind)
    add(manager, "second", kind)
    call = AsyncMock(side_effect=[NotImplementedError("unsupported protocol"), value])
    monkeypatch.setattr(MediaClient, method, call)
    result = await getattr(ModelAPI(manager), method)(*args, **kwargs)
    assert result.value == value and result.model_id == "second"
    assert [item.kwargs["model"] for item in call.call_args_list] == ["remote-first", "remote-second"]
    for key, expected in kwargs.items():
        assert call.call_args.kwargs[key] == expected


async def test_vision_filters_unsupported_video_and_returns_identity(manager: LLMManager) -> None:
    image_only = add(manager, "image-only", "vision")
    video = add(manager, "video", "vision", supports_video=True)
    image_only.describe_video = AsyncMock()
    video.describe_video = AsyncMock(return_value="description")
    result = await ModelAPI(manager).describe_video(VideoContent("data"))
    assert result.value == "description" and result.model_id == "video"
    image_only.describe_video.assert_not_awaited()


async def test_embedding_timeout_falls_back_but_pinned_space_never_switches(manager: LLMManager) -> None:
    first = add(manager, "first", "embedding")
    second = add(manager, "second", "embedding")
    cancelled = asyncio.Event()

    async def blocked(texts: list[str]) -> list[list[float]]:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return []

    first.embed = blocked
    second.embed = AsyncMock(return_value=[[0.1, 0.2]])
    api = ModelAPI(manager)
    result = await api.embed(["input"], timeout=0.01)
    assert result.model_id == "second" and result.value == [[0.1, 0.2]]
    assert cancelled.is_set()
    with pytest.raises(ModelCallError, match="TimeoutError"):
        await api.embed(["input"], model_id="first", timeout=0.01)
    second.embed.assert_awaited_once()


async def test_cancel_never_advances_to_another_model(manager: LLMManager) -> None:
    first = add(manager, "first", "vision")
    second = add(manager, "second", "vision")
    first.describe_images = AsyncMock(side_effect=asyncio.CancelledError())
    second.describe_images = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await ModelAPI(manager).describe_images([ImageContent("data")])
    second.describe_images.assert_not_awaited()


async def test_failure_details_are_ordered_and_do_not_leak_credentials(manager: LLMManager) -> None:
    first = add(manager, "first", "vision")
    second = add(manager, "second", "vision")
    first.describe_images = AsyncMock(side_effect=RuntimeError("bad credential-first"))
    second.describe_images = AsyncMock(side_effect=RuntimeError("bad credential-second"))
    with pytest.raises(ModelCallError) as error:
        await ModelAPI(manager).describe_images([ImageContent("data")])
    assert list(error.value.errors) == ["first", "second"]
    assert "credential-" not in str(error.value)


async def test_judgment_reuses_configured_engine_and_explicit_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.judgment.engine import JudgmentEngine
    from agent.judgment.types import JudgmentReport, JudgmentSource

    report = JudgmentReport(answers={}, source=JudgmentSource.TYPESAFE, model="jev")
    call = AsyncMock(return_value=report)
    monkeypatch.setattr(JudgmentEngine, "judge", call)
    questions = {"needed": NoulQuestion(instructions="Is this needed?")}
    state = {"message": "hello", "scope": "user_webui:web_user#one"}
    assert await ModelAPI().judge(state, questions, native_only=True, timeout=3) is report
    call.assert_awaited_once_with(state, questions, model_id="", native_only=True, timeout=3)
    with pytest.raises(ValueError, match="timeout"):
        await ModelAPI().judge(state, questions, timeout=float("nan"))
