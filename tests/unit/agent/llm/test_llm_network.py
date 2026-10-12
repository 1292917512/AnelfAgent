"""经真实 LiteLLM 序列化与 HTTP 传输验证模型代理，不访问外部供应商。"""
import asyncio
import json

import httpx
import pytest

from agent.llm.llm_client import LLMClient, LLMClientConfig
from core.config import ConfigManager


@pytest.fixture
def settings(monkeypatch):
    values = {"proxy_enabled": True, "http_proxy": "http://proxy.test:7890"}
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: values.get(key, default)))
    return values


@pytest.mark.parametrize("api_type", ["openai", "anthropic"])
async def test_litellm_requests_use_shared_proxy_without_serializing_clients(settings: dict, monkeypatch: pytest.MonkeyPatch, api_type: str) -> None:
    import json

    from agent.llm.llm_client import LLMClient, LLMClientConfig

    requests = []
    def factory(*, proxy=None, **kwargs):
        def respond(request):
            requests.append((proxy, json.loads(request.content)))
            if api_type == "anthropic":
                return httpx.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant",
                    "model": "claude-sonnet-4", "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 1}})
            return httpx.Response(200, json={"id": "chatcmpl_1", "object": "chat.completion", "created": 1,
                "model": "gpt-4.1", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}})
        return httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)
    client = LLMClient(LLMClientConfig(api_type=api_type, base_url="https://model.example.test/v1", api_key="test-fixture",
        model="claude-sonnet-4" if api_type == "anthropic" else "gpt-4.1", proxy_mode="proxy", max_tokens=32, chat_protocol="chat_completions"))
    try:
        assert (await client.chat([{"role": "user", "content": "hi"}])).content == "ok"
    finally:
        await client.close()
    assert requests[0][0] == settings["http_proxy"]
    assert not {"client", "http_client", "proxy_mode"}.intersection(requests[0][1])



@pytest.mark.parametrize("operation", ["embedding", "text", "responses", "stream"])
async def test_openai_protocols_share_transport(settings, monkeypatch, operation):
    requests = []
    def factory(*, proxy=None, **kwargs):
        def respond(request):
            requests.append((proxy, request.url.path, json.loads(request.content)))
            if operation == "embedding":
                payload = {"object": "list", "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}], "model": "text-embedding-3-small", "usage": {"prompt_tokens": 1, "total_tokens": 1}}
            elif operation == "text":
                payload = {"id": "cmpl-1", "object": "text_completion", "created": 1, "model": "gpt-3.5-turbo-instruct", "choices": [{"index": 0, "text": "ok", "finish_reason": "stop", "logprobs": None}]}
            elif operation == "responses":
                payload = {"id": "resp_1", "object": "response", "created_at": 1, "model": "gpt-4.1", "status": "completed", "output": [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "ok", "annotations": []}]}]}
            else:
                chunk = {"id": "chatcmpl_1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-4.1", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
            return httpx.Response(200, json=payload)
        return httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)
    model = {"embedding": "text-embedding-3-small", "text": "gpt-3.5-turbo-instruct"}.get(operation, "gpt-4.1")
    client = LLMClient(LLMClientConfig(api_type="openai", base_url="https://protocols.example.test/v1", api_key="test-fixture", model=model, proxy_mode="proxy", chat_protocol="chat_completions"))
    try:
        if operation == "embedding":
            assert await client.embed(["hello"]) == [[0.1, 0.2]]
        elif operation == "text":
            assert (await client.text_completion("hello")).text == "ok"
        elif operation == "responses":
            assert (await client.responses_create(input="hello")).output_text == "ok"
        else:
            deltas = [delta async for delta in client.chat_stream([{"role": "user", "content": "hello"}])]
            assert any(delta.content == "ok" for delta in deltas)
    finally:
        await client.close()
    assert len(requests) == 1 and requests[0][0] == settings["http_proxy"]
    assert not {"client", "http_client", "proxy_mode"}.intersection(requests[0][2])


async def test_unbound_litellm_callers_route_via_system_policy(settings, monkeypatch):
    """无模型上下文的 litellm 直调方（cognee 记忆投影等）仍可用且按全局代理策略路由。"""
    import litellm

    from agent.llm.network import _SessionClient

    settings["proxy_default_mode"] = "proxy"
    captured = []
    def factory(*, proxy=None, **kwargs):
        def respond(request):
            captured.append((proxy, request.url.host))
            return httpx.Response(200, json={"id": "chatcmpl_1", "object": "chat.completion", "created": 1,
                "model": "gpt-4.1", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
        return httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)

    previous = litellm.aclient_session
    litellm.aclient_session = _SessionClient()
    try:
        response = await litellm.acompletion(
            model="openai/gpt-4.1",
            api_base="https://cognee.example.test/v1",
            api_key="test-fixture-mock-key-001",
            messages=[{"role": "user", "content": "hi"}],
        )
    finally:
        litellm.aclient_session = previous
    assert response.choices[0].message.content == "ok"
    assert captured == [(settings["http_proxy"], "cognee.example.test")]


async def test_cached_sdk_clients_keep_concurrent_model_policies_isolated(settings, monkeypatch):
    routes = []
    def factory(*, proxy=None, **kwargs):
        async def respond(request):
            await asyncio.sleep(0)
            routes.append((json.loads(request.content)["messages"][0]["content"], proxy))
            return httpx.Response(200, json={"id": "c", "object": "chat.completion", "created": 1, "model": "gpt-4.1", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]})
        return httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)
    clients = [LLMClient(LLMClientConfig(api_type="openai", base_url="https://shared.example.test/v1", api_key="test-fixture", model="gpt-4.1", proxy_mode=mode, chat_protocol="chat_completions")) for mode in ("direct", "proxy")]
    try:
        for _ in range(2):
            await asyncio.gather(*(client.chat([{"role": "user", "content": client.config.proxy_mode}]) for client in clients))
    finally:
        await asyncio.gather(*(client.close() for client in clients))
    assert routes.count(("direct", None)) == 2
    assert routes.count(("proxy", settings["http_proxy"])) == 2
