"""工具执行边界、失败追踪与并发请求归属。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest

from core.entity import EntityRegistry
from core.event_bus import EVENT_TRACE_CALL_END, EVENT_TRACE_CALL_START, event_bus
from core.tool_context import tool_request
from core.tool_schema import extract_tool_params


@pytest.fixture
def execution(monkeypatch: pytest.MonkeyPatch) -> Iterator[AsyncMock]:
    emit = AsyncMock()
    monkeypatch.setattr(event_bus, "emit", emit)
    yield emit
    EntityRegistry.unregister("execution_probe")


@pytest.mark.parametrize("arguments", ['[]', 'null', '"text"', '{}', '{"value":1,"_timeout":true}', '{"value":1,"_timeout":NaN}'])
async def test_invalid_arguments_fail_before_invocation(execution: AsyncMock, arguments: str) -> None:
    invoked = False

    async def probe(value: int) -> str:
        nonlocal invoked
        invoked = True
        return "ok"

    EntityRegistry.register_tool(name="execution_probe", func=probe, params=extract_tool_params(probe))
    result = json.loads(await EntityRegistry.execute_tool("execution_probe", arguments))
    assert not invoked
    assert result["cause"] == "param"
    assert result["retryable"] is False
    start, end = execution.await_args_list
    assert start.args[0] == EVENT_TRACE_CALL_START
    assert end.args[0] == EVENT_TRACE_CALL_END
    assert result["diagnostic"]["call_id"] == end.args[1]["call_id"] == start.args[1]["call_id"]
    assert end.args[1]["success"] is False


async def test_reported_failure_keeps_request_and_call_identity(execution: AsyncMock) -> None:
    async def probe() -> str:
        return '{"ok":false,"message":"Service unavailable","cause":"network"}'

    EntityRegistry.register_tool(name="execution_probe", func=probe)
    with tool_request("user_webui:owner#chat-a", message_id="message-a") as request:
        result = json.loads(await EntityRegistry.execute_tool("execution_probe"))
    assert result["diagnostic"]["request_id"] == request.request_id
    assert result["diagnostic"]["message_id"] == "message-a"
    event = execution.await_args_list[-1].args[1]
    assert event["success"] is False
    assert event["error"] == "Service unavailable"


async def test_timeout_does_not_encourage_duplicate_side_effects(execution: AsyncMock) -> None:
    async def probe() -> None:
        await asyncio.sleep(1)

    EntityRegistry.register_tool(name="execution_probe", func=probe)
    result = json.loads(await EntityRegistry.execute_tool("execution_probe", timeout=0.01))
    assert result["code"] == "TOOL_TIMEOUT"
    assert result["outcome"] == "unknown" and result["retryable"] is False
    assert result["diagnostic"]["call_id"]


async def test_cancellation_closes_trace_and_propagates(execution: AsyncMock) -> None:
    started = asyncio.Event()

    async def probe() -> None:
        started.set()
        await asyncio.Event().wait()

    EntityRegistry.register_tool(name="execution_probe", func=probe)
    task = asyncio.create_task(EntityRegistry.execute_tool("execution_probe"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert execution.await_args_list[-1].args[1]["cancelled"] is True


async def test_parallel_calls_do_not_mix_diagnostics(execution: AsyncMock) -> None:
    async def probe() -> str:
        await asyncio.sleep(0)
        return '{"error":"Unavailable"}'

    EntityRegistry.register_tool(name="execution_probe", func=probe)

    async def run(scope: str) -> dict[str, str]:
        with tool_request(scope):
            return json.loads(await EntityRegistry.execute_tool("execution_probe"))["diagnostic"]

    first, second = await asyncio.gather(run("chat-a"), run("chat-b"))
    assert first["scope"] == "chat-a" and second["scope"] == "chat-b"
    assert first["call_id"] != second["call_id"]
    assert first["request_id"] != second["request_id"]
