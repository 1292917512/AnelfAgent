"""/v1/responses 网关契约测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from agent.llm.llm_client import LLMClient, LLMClientConfig
from agent.llm.protocol import ProviderCapability, TransportMode
from agent.llm.responses.router import ResponsesRoute
from agent.llm.responses.session import ResponseSessionStore
from agent.llm.responses.types import ResponseResult, ResponseStreamEvent, ResponseUsage
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
    from services.responses import ResponsesService
    from web.routers import v1_responses as v1_mod
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


@pytest.fixture()
def responses_service(monkeypatch: pytest.MonkeyPatch):
    from services.responses import ResponsesService

    model = _mock_client()
    manager = MagicMock()
    manager.resolve_client.return_value = model
    manager.get_client.return_value = model
    monkeypatch.setattr(ResponsesService, "_manager", lambda self: manager)
    store = ResponseSessionStore()
    return ResponsesService(session_store=store), model, store


@pytest.mark.parametrize("stream", [False, True])
def test_unknown_previous_id_is_client_error(client: TestClient, api_key: str, stream: bool):
    response = client.post("/v1/responses", headers={"Authorization": f"Bearer {api_key}"},
                           json={"model": "MiniMax", "input": "hi", "previous_response_id": "missing", "stream": stream})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "response_not_found"
    response = client.post("/v1/responses/compact", headers={"Authorization": f"Bearer {api_key}"},
                           json={"previous_response_id": "missing"})
    assert response.status_code == 404


def test_stream_id_supports_get_and_continuation(client: TestClient, api_key: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    response = client.post("/v1/responses", headers=headers,
                           json={"model": "MiniMax", "input": "hi", "stream": True})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    rid = events[0]["response"]["id"]
    assert events[-1]["response"]["id"] == rid
    assert client.get(f"/v1/responses/{rid}", headers=headers).json()["status"] == "completed"
    # A new provider response must have its own ID.
    from web.routers.v1_responses import _svc
    model = _svc.resolve_client("MiniMax")
    model.responses_create.return_value = ResponseResult(id="resp_next", status="completed")
    assert client.post("/v1/responses", headers=headers,
                       json={"model": "MiniMax", "input": "next", "previous_response_id": rid}).status_code == 200
    assert model.responses_create.call_args.kwargs["previous_response_id"] == rid


@pytest.mark.parametrize("status", ["completed", "failed", "incomplete", "cancelled"])
async def test_stream_terminal_saved_before_delivery(responses_service, status):
    svc, model, store = responses_service
    async def source(**kwargs):
        yield ResponseStreamEvent("response.created", {"response": {"id": "resp_terminal"}})
        yield ResponseStreamEvent(f"response.{status}", {"response": {
            "id": "resp_terminal", "status": status, "error": {"message": "reason"} if status == "failed" else None,
            "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        }}, True)
    model.responses_stream = source
    stream = await svc.stream(model="MiniMax", input="hi")
    async for event in stream:
        if event.is_terminal:
            result = await svc.get("resp_terminal")
            assert result["status"] == status
            assert result["incomplete_details"] == event.data["response"]["incomplete_details"]
    await store.fail("resp_terminal", error={"message": "late"})
    assert (await svc.cancel("resp_terminal"))["status"] == status


@pytest.mark.parametrize("operation", ["cancel", "disconnect", "delete"])
@pytest.mark.parametrize("transport", ["native", "bridge"])
async def test_stream_cancellation_closes_upstream(responses_service, operation, transport):
    import asyncio

    from agent.llm.responses.router import resolve_responses_route

    svc, model, store = responses_service
    model.responses_client.return_value.route = resolve_responses_route(api_type="openai" if transport == "native" else "anthropic")
    closed = asyncio.Event()
    async def source(**kwargs):
        try:
            yield ResponseStreamEvent("response.created", {"response": {"id": "resp_running"}})
            await asyncio.Event().wait()
        finally:
            closed.set()
    model.responses_stream = source
    stream = await svc.stream(model="MiniMax", input="hi")
    await anext(stream)
    if operation == "disconnect":
        await stream.aclose()
    elif operation == "delete":
        await svc.delete("resp_running")
        await stream.aclose()
        assert await store.get("resp_running") is None
    else:
        result = await asyncio.wait_for(svc.cancel("resp_running"), 2)
        assert result["status"] == "cancelled"
        remaining = [event async for event in stream]
        assert remaining[-1].type == "response.cancelled"
    assert closed.is_set()
    if operation != "delete":
        await store.complete("resp_running", result=ResponseResult(id="resp_running", status="completed"))
        assert (await svc.get("resp_running"))["status"] == "cancelled"


async def test_stream_without_terminal_is_failed(responses_service):
    from services.responses import ResponsesServiceError
    svc, model, store = responses_service
    async def source(**kwargs):
        yield ResponseStreamEvent("response.created", {"response": {"id": "resp_short"}})
    model.responses_stream = source
    stream = await svc.stream(model="MiniMax", input="hi")
    with pytest.raises(ResponsesServiceError):
        _ = [event async for event in stream]
    assert (await store.require("resp_short")).status == "failed"


async def test_concurrent_cancel_waits_for_one_cleanup(responses_service):
    import asyncio

    svc, model, _ = responses_service
    closing, release = asyncio.Event(), asyncio.Event()
    async def source(**kwargs):
        try:
            yield ResponseStreamEvent("response.created", {"response": {"id": "resp_cleanup"}})
            await asyncio.Event().wait()
        finally:
            closing.set()
            await release.wait()
    model.responses_stream = source
    stream = await svc.stream(model="MiniMax", input="hi")
    await anext(stream)
    first = asyncio.create_task(svc.cancel("resp_cleanup"))
    await asyncio.wait_for(closing.wait(), 2)
    second = asyncio.create_task(svc.cancel("resp_cleanup"))
    await asyncio.sleep(0)
    assert not first.done() and not second.done()
    release.set()
    results = await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert all(result["status"] == "cancelled" for result in results)
    await stream.aclose()


async def test_consumer_repeated_cancel_waits_for_upstream_close(responses_service):
    import asyncio

    svc, model, _ = responses_service
    closing, release, ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def source(**kwargs):
        try:
            yield ResponseStreamEvent("response.created", {"response": {"id": "resp_disconnect"}})
            await asyncio.Event().wait()
        finally:
            closing.set()
            await release.wait()
    model.responses_stream = source
    async def consume():
        async for _event in await svc.stream(model="MiniMax", input="hi"):
            ready.set()
    consumer = asyncio.create_task(consume())
    await ready.wait()
    consumer.cancel()
    await asyncio.wait_for(closing.wait(), 2)
    consumer.cancel()
    await asyncio.sleep(0)
    assert not consumer.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(consumer, 2)
    assert (await svc.get("resp_disconnect"))["status"] == "cancelled"


async def test_delete_terminal_stream_releases_backpressure(responses_service):
    import asyncio

    svc, model, store = responses_service
    closed = asyncio.Event()
    async def source(**kwargs):
        try:
            yield ResponseStreamEvent("response.created", {"response": {"id": "resp_blocked"}})
            yield ResponseStreamEvent("response.output_text.delta", {"delta": "x"})
            yield ResponseStreamEvent("response.completed", {"response": {"id": "resp_blocked", "status": "completed"}}, True)
        finally:
            closed.set()
    model.responses_stream = source
    stream = await svc.stream(model="MiniMax", input="hi")
    await anext(stream)
    async with asyncio.timeout(2):
        while (await store.require("resp_blocked")).status != "completed":
            await asyncio.sleep(0)
    await asyncio.wait_for(svc.delete("resp_blocked"), 2)
    assert closed.is_set()
    assert await store.get("resp_blocked") is None
    await stream.aclose()


async def test_terminal_cleanup_survives_cancel(responses_service):
    import asyncio

    svc, model, _ = responses_service
    closing, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def source(**kwargs):
        try:
            yield ResponseStreamEvent("response.completed", {"response": {"id": "resp_final", "status": "completed"}}, True)
        finally:
            closing.set()
            await release.wait()
            closed.set()
    model.responses_stream = source
    stream = await svc.stream(model="MiniMax", input="hi")
    await anext(stream)
    await closing.wait()
    cancellation = asyncio.create_task(svc.cancel("resp_final"))
    await asyncio.sleep(0)
    assert not cancellation.done()
    release.set()
    assert (await asyncio.wait_for(cancellation, 2))["status"] == "completed"
    assert closed.is_set()
    await stream.aclose()


async def test_native_background_query_refreshes_cached_progress(responses_service):
    svc, model, _ = responses_service
    model.responses_create.return_value = ResponseResult(id="resp_background", status="in_progress")
    await svc.create(model="MiniMax", input="hi")
    model.responses_get.return_value = ResponseResult(id="resp_background", status="completed")
    assert (await svc.get("resp_background"))["status"] == "completed"
    assert model.responses_get.await_count == 1


async def test_provider_read_cannot_overwrite_concurrent_cancel(responses_service):
    import asyncio

    svc, model, _ = responses_service
    entered, release = asyncio.Event(), asyncio.Event()
    model.responses_create.return_value = ResponseResult(id="resp_race", status="in_progress")
    await svc.create(model="MiniMax", input="hi")
    async def get_result(rid):
        entered.set()
        await release.wait()
        return ResponseResult(id=rid, status="completed")
    model.responses_get = get_result
    model.responses_cancel.return_value = ResponseResult(id="resp_race", status="cancelled")
    read = asyncio.create_task(svc.get("resp_race"))
    await entered.wait()
    assert (await svc.cancel("resp_race"))["status"] == "cancelled"
    release.set()
    assert (await read)["status"] == "cancelled"
