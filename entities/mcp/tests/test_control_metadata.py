"""执行来源经真实 bridge 线程传递到 MCP _meta，普通执行器不受影响。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from helpers.mcp_fakes import make_result

from core import tool_context
from entities.mcp.bridge import MCPBridge


def test_request_metadata_survives_bridge_thread_without_changing_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_context, "_controls", {})
    tool_context.register_control("controlled", "world", frozenset({"stop"}))
    bridge = MCPBridge()
    call = AsyncMock(return_value=make_result())
    bridge._sessions["controlled"] = SimpleNamespace(call_tool=call)
    bridge._sessions["plain"] = SimpleNamespace(call_tool=call)
    try:
        with tool_context.tool_request("group_minecraft:world", "Alice") as request:
            bridge._run_coro(bridge._do_call_tool("controlled", "stop", {}), timeout=5)
            assert call.await_args.kwargs["arguments"] == {}
            meta = call.await_args.kwargs["meta"]["anelf/action"]
            assert meta["requestId"] == request.request_id and meta["actor"] == "Alice"
            assert meta["epoch"] == meta["floor"] == 1
            bridge._run_coro(bridge._do_call_tool("controlled", "dig", {"x": 1}), timeout=5)
            old = call.await_args.kwargs["meta"]["anelf/action"]
            assert old["epoch"] == 0 and old["floor"] == 1
            assert call.await_args.kwargs["arguments"] == {"x": 1}
        bridge._run_coro(bridge._do_call_tool("plain", "stop", {}), timeout=5)
        assert "meta" not in call.await_args.kwargs
    finally:
        bridge._sessions.clear()
        bridge.shutdown()
