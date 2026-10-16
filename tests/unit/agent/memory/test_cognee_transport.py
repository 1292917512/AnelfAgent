"""通过真实 Cognee/LiteLLM 转换验证抽取请求，HTTP 层隔离外部服务。"""
import asyncio
import importlib
import json
from types import SimpleNamespace

import httpx
import litellm
import pytest
from litellm.caching.llm_caching_handler import LLMClientCache
from pydantic import BaseModel

from agent.llm.config import LLMClientConfig
from agent.memory.cognee.config import CogneeChatModelConfig
from agent.memory.cognee.llm_bridge import resolve_chat_llm_config


class Extracted(BaseModel):
    fact: str


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("mode,protocol,outcome", [
    (mode, protocol, "success") for mode in ("", "json_mode", "tools")
    for protocol in ("chat_completions", "responses")
] + [("", "responses", "denied"), ("", "responses", "cancelled")])
async def test_extraction_honors_selected_protocol_and_structured_output(monkeypatch, mode, protocol, outcome):
    from cognee.infrastructure.llm.config import LLMConfig
    from tenacity import stop_after_attempt

    from agent.llm import network
    from agent.memory.cognee import adapters
    from core.config import ConfigManager

    adapters.install()
    ConfigManager.set("proxy_enabled", True)
    ConfigManager.set("http_proxy", "http://proxy.invalid:7890")
    ConfigManager.set("proxy_default_mode", "proxy")

    cfg = LLMClientConfig(name="extractor", model="glm-5.3-flash", api_type="openai",
        base_url="https://model.invalid/api/v1", api_key="test-placeholder", chat_protocol=protocol,
        supports_reasoning=True, reasoning_effort="high", timeout=120)
    manager = SimpleNamespace(get_client=lambda _: SimpleNamespace(config=cfg))
    payload = resolve_chat_llm_config(CogneeChatModelConfig(
        source="model", model_id="extractor", instructor_mode=mode, max_completion_tokens=8192,
    ), manager)
    factory = importlib.import_module(
        "cognee.infrastructure.llm.structured_output_framework.litellm_instructor.llm.get_llm_client"
        if mode else "cognee.infrastructure.llm.structured_output_framework.litellm_native.get_native_client",
    )
    monkeypatch.setattr(factory, "get_llm_context_config", lambda: LLMConfig(**payload))
    gateway = importlib.import_module("cognee.infrastructure.llm.LLMGateway")
    monkeypatch.setattr(gateway, "get_llm_config", lambda: LLMConfig(**payload))
    monkeypatch.setattr("cognee.infrastructure.llm.get_llm_config", lambda: LLMConfig(**payload))
    requests = []
    routes = []
    clients = []
    handler_type = network.ModelHTTPHandler

    def handler(*args):
        client = handler_type(*args)
        clients.append(client)
        return client

    monkeypatch.setattr(network, "ModelHTTPHandler", handler)

    def respond(request):
        body = json.loads(request.content)
        requests.append((request, body))
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        if outcome == "denied":
            return httpx.Response(403, json={"error": {"message": "No permission to access model", "type": "permission_denied"}})
        content = '{"fact":"verified"}'
        if protocol == "responses":
            output = [{"type": "function_call", "id": "fc_1", "call_id": "call_1",
                       "name": "Extracted", "arguments": content, "status": "completed"}] if mode == "tools" else [
                {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": content, "annotations": []}]}]
            data = {"id": "resp_1", "object": "response", "created_at": 1, "model": cfg.model,
                    "status": "completed", "output": output,
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
        else:
            message = {"role": "assistant", "content": content}
            if mode == "tools":
                message = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "Extracted", "arguments": content}}]}
            data = {"id": "chatcmpl_1", "object": "chat.completion", "created": 1, "model": cfg.model,
                    "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if mode == "tools" else "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        return httpx.Response(200, json=data)

    def http_transport(**kwargs):
        routes.append(kwargs.get("proxy"))
        return httpx.MockTransport(respond)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        monkeypatch.setattr(litellm, "in_memory_llm_clients_cache", LLMClientCache())
        monkeypatch.setattr(litellm, "aclient_session", transport)
        monkeypatch.setattr(httpx, "AsyncHTTPTransport", http_transport)
        adapter = factory.get_llm_client() if mode else factory.get_native_client()
        monkeypatch.setattr(adapter.acreate_structured_output.retry, "stop", stop_after_attempt(1))
        if outcome == "success":
            result = await gateway.LLMGateway.acreate_structured_output("source", "extract facts", Extracted)
            assert result.fact == "verified"
        else:
            with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else litellm.APIError) as failure:
                await gateway.LLMGateway.acreate_structured_output("source", "extract facts", Extracted)
            assert not adapters.retryable(failure.value)
    assert all(client._client.is_closed for client in clients)
    if protocol == "responses":
        assert routes == ["http://proxy.invalid:7890"]
    assert len(requests) == 1
    request, body = requests[0]
    assert request.url.path == "/api/v1/" + ("responses" if protocol == "responses" else "chat/completions")
    assert body["model"] == cfg.model
    assert "thinking" not in body
    assert body["max_output_tokens" if protocol == "responses" else "max_tokens"] == 8192
    assert (body["reasoning"]["effort"] if protocol == "responses" else body["reasoning_effort"]) == "high"
    assert cfg.chat_protocol == protocol and cfg.model == "glm-5.3-flash"


@pytest.mark.parametrize("api_type", ["openai", "azure", "anthropic"])
@pytest.mark.parametrize("protocol", ["auto", "responses", "chat_completions"])
def test_model_route_keeps_provider_and_does_not_mutate_config(api_type, protocol):
    cfg = LLMClientConfig(name="extractor", model="deployment", api_type=api_type,
                          chat_protocol=protocol, base_url="https://model.invalid/v1")
    payload = resolve_chat_llm_config(CogneeChatModelConfig(source="model", model_id="extractor"),
        SimpleNamespace(get_client=lambda _: SimpleNamespace(config=cfg)))
    route = "responses/" if api_type != "anthropic" and protocol == "responses" else ""
    assert payload["llm_model"] == f"{api_type}/{route}deployment"
    assert cfg.model == "deployment"


@pytest.mark.parametrize("suffix", ["responses", "chat/completions"])
def test_explicit_endpoint_overrides_protocol_without_double_suffix(suffix):
    cfg = LLMClientConfig(name="extractor", model="deployment", api_type="openai",
        chat_protocol="responses" if suffix == "chat/completions" else "chat_completions",
        base_url=f"https://model.invalid/v1/{suffix}/")
    payload = resolve_chat_llm_config(CogneeChatModelConfig(source="model", model_id="extractor"),
        SimpleNamespace(get_client=lambda _: SimpleNamespace(config=cfg)))
    assert payload["llm_endpoint"] == "https://model.invalid/v1"
    assert payload["llm_model"] == "openai/" + ("responses/" if suffix == "responses" else "") + "deployment"
