"""Responses SessionStore 契约测试。"""

from __future__ import annotations

import asyncio

import pytest

from agent.llm.responses.session import ResponseSessionStore
from agent.llm.responses.types import ResponseResult


@pytest.mark.asyncio
async def test_session_create_complete_get_delete() -> None:
    store = ResponseSessionStore(ttl_seconds=60)
    session = await store.create(
        model_id="gpt-4o",
        provider_id="openai",
        api_type="openai",
        api_base="https://api.openai.com/v1",
        transport="native",
    )
    assert session.response_id.startswith("resp_")

    result = ResponseResult(id=session.response_id, status="completed", model="gpt-4o")
    await store.complete(session.response_id, result=result)
    loaded = await store.require(session.response_id, provider_id="openai")
    assert loaded.result is not None
    assert loaded.result.status == "completed"

    assert await store.delete(session.response_id) is True
    assert await store.get(session.response_id) is None


@pytest.mark.asyncio
async def test_session_provider_binding() -> None:
    store = ResponseSessionStore()
    session = await store.create(
        model_id="MiniMax",
        provider_id="minimax",
        api_type="openai",
        api_base="https://api.minimax.chat/v1",
        transport="bridge",
    )
    with pytest.raises(PermissionError):
        await store.require(session.response_id, provider_id="openai")
    with pytest.raises(PermissionError):
        await store.require(
            session.response_id,
            provider_id="minimax",
            api_base="https://other.example/v1",
        )


@pytest.mark.asyncio
async def test_session_cancel_stops_task() -> None:
    store = ResponseSessionStore()

    async def _hang() -> None:
        await asyncio.sleep(30)

    task = asyncio.create_task(_hang())
    session = await store.create(
        model_id="gpt-4o",
        provider_id="openai",
        api_type="openai",
        api_base="https://api.openai.com/v1",
        transport="native",
        task=task,
    )
    cancelled = await store.cancel(session.response_id)
    assert cancelled.status == "cancelled"
    assert task.cancelled() or task.done()
