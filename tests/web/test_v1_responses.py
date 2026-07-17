"""/v1/responses 网关契约测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.llm.llm_client import LLMClient, LLMClientConfig
from agent.llm.protocol import TransportMode
from agent.llm.responses.router import ResponsesRoute
from agent.llm.responses.session import ResponseSessionStore
from agent.llm.responses.types import ResponseResult, ResponseStreamEvent, ResponseUsage
from agent.llm.protocol import ProviderCapability
from web.auth_keys import create_api_key, hash_api_key


@pytest.fixture()
def webui_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg_path = tmp_path / "webui.json"
    cfg_path.write_text(json.dumps({
        "auth": {"password": "", "api_keys": []},
        "server": {"host": "127.0.0.1", "port": 8092},
    }), encoding="utf-8")
    monkeypatch.setattr("core.path.ConfigPaths.WEBUI_CONFIG", str(cfg_path))
    monkeypatch.setattr("web.auth_keys.ConfigPaths.WEBUI_CONFIG", str(cfg_path))
    return cfg_path


@pytest.fixture()
def api_key(webui_cfg: Path) -> str:
    created = create_api_key(name="test")
    return created["api_key"]


def _native_route() -> ResponsesRoute:
    capability = ProviderCapability(
        create=TransportMode.NATIVE,
        stream=TransportMode.NATIVE,
        retrieve=TransportMode.NATIVE,
        delete=TransportMode.NATIVE,
        cancel=TransportMode.NATIVE,
        compact=TransportMode.NATIVE,
        previous_response_id=TransportMode.NATIVE,
        builtin_tools=TransportMode.NATIVE,
    )
    return ResponsesRoute(
        transport=TransportMode.NATIVE,
        capability=capability,
        force_chat_completions_api=False,
        api_type="openai",
        api_base="https://api.openai.com/v1",
    )


def _mock_client() -> LLMClient:
    client = LLMClient(LLMClientConfig(
        name="MiniMax",
        model="MiniMax-Text",
        api_type="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-secret-provider-key",
        provider_id="openai",
        chat_protocol="responses",
    ))
    result = ResponseResult(
        id="resp_mock_1",
        status="completed",
        model="MiniMax-Text",
        output_text="hello",
        output=[{
            "type": "message",
            "content": [{"type": "output_text", "text": "hello"}],
        }],
        usage=ResponseUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        transport="native",
        raw={
            "id": "resp_mock_1",
            "object": "response",
            "status": "completed",
            "model": "MiniMax-Text",
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": "hello"}],
            }],
        },
    )
    client.responses_create = AsyncMock(return_value=result)  # type: ignore[method-assign]
    client.responses_get = AsyncMock(return_value=result)  # type: ignore[method-assign]
    client.responses_delete = AsyncMock(return_value={"id": "resp_mock_1", "deleted": True})  # type: ignore[method-assign]
    client.responses_cancel = AsyncMock(return_value=result)  # type: ignore[method-assign]
    client.responses_compact = AsyncMock(return_value=result)  # type: ignore[method-assign]

    async def _stream(**_kwargs: Any) -> AsyncGenerator[ResponseStreamEvent, None]:
        yield ResponseStreamEvent(
            type="response.created",
            data={"type": "response.created", "response": {"id": "resp_mock_1"}},
        )
        yield ResponseStreamEvent(
            type="response.completed",
            data={"type": "response.completed", "response": result.raw},
            is_terminal=True,
        )

    client.responses_stream = _stream  # type: ignore[method-assign]

    mock_rc = MagicMock()
    mock_rc.route = _native_route()
    client.responses_client = MagicMock(return_value=mock_rc)  # type: ignore[method-assign]
    return client


@pytest.fixture()
def client(api_key: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    mock_llm = MagicMock()
    mock_model = _mock_client()
    mock_llm.resolve_client.return_value = mock_model
    mock_llm.get_client.return_value = mock_model

    store = ResponseSessionStore()
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: mock_llm)
    monkeypatch.setattr("agent.llm.llm_manager.get_llm_manager", lambda: mock_llm)
    monkeypatch.setattr(
        "services.responses.get_response_session_store",
        lambda: store,
    )
    monkeypatch.setattr(
        "web.routers.v1_responses.get_response_session_store",
        lambda: store,
    )

    def _sanitize(self: Any, exc: Exception, *extra_secrets: str) -> str:
        message = str(exc)
        for secret in extra_secrets:
            if secret:
                message = message.replace(secret, "****")
        return message.replace("sk-secret-provider-key", "****")

    monkeypatch.setattr(
        "services.model.ModelService.sanitize_error",
        _sanitize,
    )

    from web.server import create_app
    app = create_app()
    # 确保服务层使用同一 session store
    from web.routers import v1_responses as v1_mod
    from services.responses import ResponsesService
    v1_mod._svc = ResponsesService(session_store=store)

    return TestClient(app)


def test_v1_requires_bearer(client: TestClient) -> None:
    resp = client.post("/v1/responses", json={"model": "MiniMax", "input": "hi"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


def test_v1_create_get_delete_cancel_compact(client: TestClient, api_key: str) -> None:
    headers = {"Authorization": f"Bearer {api_key}"}
    created = client.post(
        "/v1/responses",
        headers=headers,
        json={"model": "MiniMax", "input": "hello", "stream": False},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["id"] == "resp_mock_1"
    assert body["status"] == "completed"
    assert "sk-secret-provider-key" not in created.text

    got = client.get("/v1/responses/resp_mock_1", headers=headers)
    assert got.status_code == 200
    assert got.json()["id"] == "resp_mock_1"

    cancelled = client.post("/v1/responses/resp_mock_1/cancel", headers=headers)
    assert cancelled.status_code == 200

    compact = client.post(
        "/v1/responses/compact",
        headers=headers,
        json={"model": "MiniMax", "input": "hello"},
    )
    assert compact.status_code == 200

    deleted = client.delete("/v1/responses/resp_mock_1", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_v1_stream_sse(client: TestClient, api_key: str) -> None:
    headers = {"Authorization": f"Bearer {api_key}"}
    with client.stream(
        "POST",
        "/v1/responses",
        headers=headers,
        json={"model": "MiniMax", "input": "hello", "stream": True},
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert "event: response.created" in text
    assert "event: response.completed" in text
    assert "sk-secret-provider-key" not in text


def test_api_key_hash_roundtrip(webui_cfg: Path) -> None:
    created = create_api_key(name="roundtrip")
    raw = created["api_key"]
    data = json.loads(webui_cfg.read_text("utf-8"))
    stored = data["auth"]["api_keys"][0]
    assert stored["key_hash"] == hash_api_key(raw)
    assert "api_key" not in stored
