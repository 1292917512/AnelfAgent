"""MiniMax 图片协议契约：模型分流、旧版兼容、边界、SSE 与组件装卸载。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace

import httpx
import pytest

from agent.llm.adapter_base import AdapterRegistry, MediaOutcomeUnknown
from agent.llm.image_adapters import ImageGenParams, resolve_image_adapter
from agent.llm.media_client import MediaClient
from core.registration import registration_transaction
from entities.minimax.image_adapter import MiniMaxImagesAdapter


@pytest.mark.parametrize("model", ["MiniMax-H3-Image", "MiniMax-H3-Image-Highspeed"])
@pytest.mark.parametrize("images", [(), ("https://cdn/cat.jpg", "mm_file://123", "data:image/png;base64,AA==")])
def test_h3_request(model: str, images: tuple[str, ...]) -> None:
    req = MiniMaxImagesAdapter().build_request("https://api.minimax.cn/anthropic", ImageGenParams(
        model=model, prompt="图1与图2", images=images, image_size="16:9", n=10,
        options={"area": "2K", "output_format": "webp", "output_compression": 90, "aigc_watermark": True},
    ))
    assert req.url == "https://api.minimax.cn/v1/images/" + ("edits" if images else "generations")
    assert req.payload is not None
    assert req.payload["model"] == model and req.payload["n"] == 10
    assert req.payload["size"] == "auto" and req.payload["aspect_ratio"] == "16:9"
    assert req.payload["output_format"] == "webp" and req.payload["area"] == "2K"
    assert "num_inference_steps" not in req.payload and "cfg" not in req.payload
    if images:
        assert req.payload["images"] == [{"image_url": image} for image in images]


@pytest.mark.parametrize("model", ["image-01", "image-01-live"])
def test_legacy_generation_and_subject_reference(model: str) -> None:
    adapter = MiniMaxImagesAdapter()
    params = ImageGenParams(model=model, prompt="猫", image_size="1280x720", n=2)
    req = adapter.build_request("https://api.minimaxi.com/anthropic", params)
    assert req.url == "https://api.minimaxi.com/v1/image_generation"
    assert req.payload == {"model": model, "prompt": "猫", "aspect_ratio": "16:9", "response_format": "url", "n": 2}
    edit = adapter.build_request("https://api.minimax.io/v1", replace(params, images=("https://cdn/cat.jpg",)))
    assert edit.payload is not None
    assert edit.payload["subject_reference"] == [{"type": "character", "image_file": "https://cdn/cat.jpg"}]
    assert adapter._aspect_ratio("bad") == "1:1"
    assert adapter._aspect_ratio("928x1664") == "9:16"


@pytest.mark.parametrize("changes", [
    {"n": 11}, {"n": 0}, {"n": True}, {"images": ("https://cdn/a.png",) * 17},
    {"prompt": " "}, {"prompt": "x" * 32001}, {"image_size": "1x1"},
    {"image_size": "1000x4000"}, {"image_size": "bad"},
    {"image_size": "1024x1024", "options": {"area": "2K"}},
    {"options": {"model": "override"}}, {"options": {"size": "1024x1024"}},
    {"options": {"area": "8K"}}, {"options": {"output_compression": 101}},
    {"options": {"stream": "true"}}, {"options": {"aspect_ratio": "301:300"}},
    {"options": {"aspect_ratio": "4:1"}}, {"options": {"disable_prompt_enhanced": True}},
    {"images": ("https://cdn/a.png",), "options": {"disable_prompt_enhanced": False}},
    {"options": {"stream": True, "response_format": "b64_json"}},
    {"n": 2, "options": {"output_format": "png", "response_format": "b64_json"}},
])
def test_h3_rejects_invalid_request(changes: dict) -> None:
    with pytest.raises(ValueError):
        params = replace(ImageGenParams(model="MiniMax-H3-Image", prompt="猫"), **changes)
        MiniMaxImagesAdapter().build_request("https://api.minimax.cn", params)


def test_formats_partial_errors_and_legacy_envelopes() -> None:
    adapter = MiniMaxImagesAdapter()
    result = adapter.extract_urls({"output_format": "webp", "request_id": "req-1", "usage": {"generated_images": 1},
                                   "data": [{"b64_json": "QUJD", "size": "1024x1024"},
                                            {"error": {"code": "1027", "message": "blocked"}}]})
    assert result == ["data:image/webp;base64,QUJD"]
    assert result.details["image_details"][1]["error"]["code"] == "1027"
    assert result.details["usage"]["generated_images"] == 1
    assert "QUJD" not in json.dumps(result.details)
    assert adapter.extract_urls({"data": {"image_urls": ["https://cdn/a.png"]}}) == ["https://cdn/a.png"]
    assert adapter.extract_urls({"data": {"image_base64": ["QUJD"]}}) == ["data:image/png;base64,QUJD"]
    with pytest.raises(RuntimeError, match="1026"):
        adapter.extract_urls({"base_resp": {"status_code": 1026, "status_msg": "blocked"}})
    with pytest.raises(RuntimeError, match="全部失败"):
        adapter.extract_urls({"data": [{"error": {"code": "1027"}}]})
    with pytest.raises(RuntimeError, match="安全"):
        adapter.extract_urls({"metadata": {"failed_count": 1}})


@pytest.mark.parametrize("host", ["api.minimax.cn", "api.minimaxi.com", "api.minimax.io"])
def test_component_registration_and_rollback(host: str) -> None:
    import entities.minimax

    entities.minimax.register_components()
    entities.minimax.register_components()
    assert resolve_image_adapter(f"https://{host}/v1").name == "minimax"
    registry: AdapterRegistry[MiniMaxImagesAdapter] = AdapterRegistry("图片")
    with pytest.raises(RuntimeError), registration_transaction("entities.minimax"):
        registry.register(MiniMaxImagesAdapter(), host_keywords=(host,))
        raise RuntimeError("failed import")
    with pytest.raises(NotImplementedError):
        registry.resolve(f"https://{host}")
    registry.register(MiniMaxImagesAdapter(), host_keywords=(host,))
    registry.register(MiniMaxImagesAdapter(), host_keywords=(host,))
    registry.unregister_by_module("entities.minimax")
    with pytest.raises(NotImplementedError):
        registry.resolve(f"https://{host}")


@pytest.mark.parametrize("stream", [False, True])
async def test_http_generation_and_multireference_edit(stream: bool, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []
    reference = tmp_path / "local.png"
    reference.write_bytes(b"png")

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if stream:
            events = [
                {"type": "image_edit.partial_succeeded", "image_index": 1, "url": "https://cdn/second.webp", "output_format": "webp"},
                {"type": "image_edit.partial_failed", "image_index": 0, "code": "1027", "message": "blocked"},
                {"type": "image_edit.completed", "usage": {"generated_images": 1}, "request_id": "req-1"},
            ]
            return httpx.Response(200, text=": keepalive\n\n" + "".join(f"data: {json.dumps(e)}\n\n" for e in events),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"data": [{"url": "https://cdn/second.webp"}]})

    transport = httpx.MockTransport(respond)
    client = MediaClient("https://api.minimax.cn/v1", "test-key", media_protocol="minimax", timeout=650)
    monkeypatch.setattr(client, "_http_client", lambda timeout=None: httpx.AsyncClient(transport=transport, timeout=650))
    result = await client.edit_image("混合图1图2", model="MiniMax-H3-Image", n=2,
                                     image_paths=[str(reference), "https://cdn/ref.png", "mm_file://123"],
                                     options={"stream": stream})
    assert result == ["https://cdn/second.webp"]
    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/images/edits"
    assert requests[0].headers["authorization"] == "Bearer test-key"
    assert payload["images"] == [{"image_url": "data:image/png;base64,cG5n"},
                                 {"image_url": "https://cdn/ref.png"}, {"image_url": "mm_file://123"}]
    assert payload["n"] == 2
    if stream:
        assert result.details["image_details"][0]["error"]["code"] == "1027"
        assert result.details["request_id"] == "req-1"


async def test_stream_requires_terminal_event() -> None:
    async def events() -> AsyncIterator[dict]:
        yield {"type": "image_generation.partial_succeeded", "image_index": 0, "url": "https://cdn/a.png"}

    with pytest.raises(MediaOutcomeUnknown):
        await MiniMaxImagesAdapter().read_stream(events())


async def test_direct_component_reuses_protocol_and_its_own_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import ConfigManager
    from entities.minimax.client import MiniMaxClient
    from entities.minimax.providers import MiniMaxVisualProvider

    ConfigManager.set("minimax_image_model", "MiniMax-H3-Image")
    ConfigManager.set("minimax_image_timeout", 700)
    ConfigManager.set("minimax_image_base_url", "https://gateway.test/v1")
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"data": [{"url": "https://cdn/output.jpg"}]})

    monkeypatch.setattr("agent.llm.media_client.async_network_client", lambda **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), timeout=kwargs["timeout"],
    ))
    monkeypatch.setattr("entities.minimax.providers._client", lambda: MiniMaxClient(api_key="fake-key"))
    result = await MiniMaxVisualProvider().run("image_edit", prompt="edit", image_paths=["https://cdn/a", "https://cdn/b"])
    assert result["image_results"] == ["https://cdn/output.jpg"]
    assert str(sent[0].url) == "https://gateway.test/v1/images/edits"
    assert sent[0].extensions["timeout"]["read"] == 700
    assert len(json.loads(sent[0].content)["images"]) == 2


def test_sdk_unload_removes_image_component(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.llm import image_adapters
    from entities._sdk import register_image_adapter, unregister_module_components

    class TemporaryAdapter(MiniMaxImagesAdapter):
        __module__ = "entities.temporary_image"
        name = "temporary"

    registry = AdapterRegistry("test images")
    monkeypatch.setattr(image_adapters, "_REGISTRY", registry)
    register_image_adapter(TemporaryAdapter(), host_keywords=("images.test",))
    assert registry.resolve("https://images.test").name == "temporary"
    unregister_module_components("entities.temporary_image")
    with pytest.raises(NotImplementedError):
        registry.resolve("https://images.test")
