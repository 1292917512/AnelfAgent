"""MediaClient HTTP 错误提取（_check_resp）与 DashScope 图片协议单元测试。"""

from __future__ import annotations

import httpx
import pytest

from agent.llm.image_adapters import DashScopeImagesAdapter, ImageGenParams
from agent.llm.media_client import MediaClient


def _resp(status_code: int, body: object = None, text: str = "") -> httpx.Response:
    if body is not None:
        return httpx.Response(status_code, json=body)
    return httpx.Response(status_code, text=text)


class TestCheckResp:
    def test_success_noop(self) -> None:
        MediaClient._check_resp(_resp(200, {"ok": True}))
        MediaClient._check_resp(_resp(302))

    def test_minimax_v2_error_envelope(self) -> None:
        body = {"type": "error", "error": {"type": "authorized_error", "message": "missing api secret key (1004)"}}
        with pytest.raises(RuntimeError) as exc_info:
            MediaClient._check_resp(_resp(401, body))
        msg = str(exc_info.value)
        assert "HTTP 401" in msg
        assert "missing api secret key (1004)" in msg

    def test_minimax_v1_base_resp(self) -> None:
        body = {"base_resp": {"status_code": 1008, "status_msg": "余额不足"}}
        with pytest.raises(RuntimeError) as exc_info:
            MediaClient._check_resp(_resp(402, body))
        assert "HTTP 402" in str(exc_info.value)
        assert "[1008] 余额不足" in str(exc_info.value)

    def test_openai_style_error(self) -> None:
        body = {"error": {"message": "model not found", "type": "invalid_request_error"}}
        with pytest.raises(RuntimeError, match="model not found"):
            MediaClient._check_resp(_resp(400, body))

    def test_plain_message_field(self) -> None:
        with pytest.raises(RuntimeError, match="rate limited"):
            MediaClient._check_resp(_resp(429, {"message": "rate limited"}))

    def test_non_json_body_falls_back_to_text(self) -> None:
        with pytest.raises(RuntimeError, match="Bad Gateway"):
            MediaClient._check_resp(_resp(502, text="Bad Gateway"))


class TestDashScopeImageEdit:
    def test_edit_request_messages_with_image(self) -> None:
        adapter = DashScopeImagesAdapter()
        req = adapter.build_request(
            "https://example-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            ImageGenParams(model="qwen-image-3.0-pro",
            prompt="把猫变成戴眼镜的样子",
            images=("https://oss.example/cat.png",),
            num_inference_steps=20,
            cfg=4.0),
        )
        assert req.url == (
            "https://example-workspace.cn-beijing.maas.aliyuncs.com"
            "/api/v1/services/aigc/multimodal-generation/generation"
        )
        payload = req.payload or {}
        content = payload["input"]["messages"][0]["content"]
        assert content == [
            {"image": "https://oss.example/cat.png"},
            {"text": "把猫变成戴眼镜的样子"},
        ]
        assert payload["parameters"] == {"n": 1}

    def test_edit_response_reuses_extract_urls(self) -> None:
        adapter = DashScopeImagesAdapter()
        # qwen-image 系列内容项只有 image 键（实测），wan 系列带 type 字段，两种都要解析
        qwen_style = {"output": {"choices": [{"message": {"content": [
            {"image": "https://oss.example/edited.png"},
        ]}}]}}
        assert adapter.extract_urls(qwen_style) == ["https://oss.example/edited.png"]
        wan_style = {"output": {"choices": [{"message": {"content": [
            {"type": "image", "image": "https://oss.example/edited2.png"},
        ]}}]}}
        assert adapter.extract_urls(wan_style) == ["https://oss.example/edited2.png"]


@pytest.mark.parametrize("body", [
    {}, [], {"results": []}, {"results": [{"index": 0, "relevance_score": 0.9}]},
    {"results": [{"index": 0, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.8}]},
    {"results": [{"relevance_score": 0.9}, {"index": 1, "relevance_score": 0.8}]},
    {"results": [{"index": -1, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.8}]},
    {"results": [{"index": 2, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.8}]},
    {"results": [{"index": True, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.8}]},
    {"results": [{"index": 0, "relevance_score": "high"}, {"index": 1, "relevance_score": 0.8}]},
    {"results": [{"index": 0, "relevance_score": True}, {"index": 1, "relevance_score": 0.8}]},
    {"results": [None, {"index": 1, "relevance_score": 0.8}]},
])
async def test_rerank_rejects_invalid_candidates(body: object, monkeypatch: pytest.MonkeyPatch) -> None:
    client = MediaClient("https://rerank.test", "")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    monkeypatch.setattr(client, "_http_client", lambda: httpx.AsyncClient(transport=transport))
    with pytest.raises(ValueError, match="重排"):
        await client.rerank("证据", ["候选一", "候选二"], top_n=2)


async def test_rerank_keeps_valid_order_and_limits_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    items = [{"index": 1, "relevance_score": 2.3}, {"index": 0, "relevance_score": -0.5}]
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"results": items})

    client = MediaClient("https://rerank.test", "")
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(client, "_http_client", lambda: httpx.AsyncClient(transport=transport))
    assert await client.rerank("证据", ["候选一", "候选二"], top_n=1) == items[:1]
    assert json.loads(sent[0].content)["documents"] == ["候选一", "候选二"]
    assert await client.rerank("证据", []) == []
    assert len(sent) == 1


async def test_rerank_rejects_nonfinite_score(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MediaClient("https://rerank.test", "")
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=b'{"results":[{"index":0,"relevance_score":NaN}]}',
    ))
    monkeypatch.setattr(client, "_http_client", lambda: httpx.AsyncClient(transport=transport))
    with pytest.raises(ValueError, match="有限数值"):
        await client.rerank("证据", ["候选一"])


async def test_image_uses_model_timeout_options_and_quantity(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from agent.llm.config import LLMClientConfig

    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"data": [{"url": "https://cdn/a.png"}]})

    monkeypatch.setattr("agent.llm.media_client.async_network_client", lambda **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), timeout=kwargs["timeout"],
    ))
    client = MediaClient.from_config(LLMClientConfig(
        name="images", base_url="https://images.test/v1", api_key="key", model="generic-image",
        media_protocol="openai", timeout=650, extra_body={"quality": "high", "output_format": "png"},
    ))
    await client.generate_image("draw", model="generic-image", n=3, options={"quality": "medium"})
    assert sent[0].extensions["timeout"]["read"] == 650
    assert json.loads(sent[0].content) == {"model": "generic-image", "prompt": "draw", "n": 3,
                                         "size": "1024x1024", "quality": "medium", "output_format": "png"}


async def test_image_timeout_is_total_and_never_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from agent.llm.adapter_base import MediaOutcomeUnknown

    calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return httpx.Response(200, json={"data": []})

    client = MediaClient("https://images.test/v1", "", media_protocol="openai", timeout=0.01)
    monkeypatch.setattr(client, "_http_client", lambda timeout=None: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    with pytest.raises(MediaOutcomeUnknown):
        await client.generate_image("draw")
    assert calls == 1
