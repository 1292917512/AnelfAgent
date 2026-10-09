"""按 MCP 服务与原始工具名调用的冲突隔离。"""

import json
from unittest.mock import AsyncMock

from entities.mcp.bridge import MCPBridge


async def test_server_call_resolves_prefixed_name() -> None:
    bridge = MCPBridge()
    try:
        bridge._tool_server_map.update({"chat": "other", "minecraft__chat": "minecraft"})
        bridge._tool_original_names["minecraft__chat"] = "chat"
        call = AsyncMock(return_value='{"ok":true}')
        bridge._call_bound_tool = call
        result = await bridge.call_server_tool("minecraft", "chat", {"message": "hello"})
        assert json.loads(result)["ok"]
        call.assert_awaited_once_with("minecraft", "chat", {"message": "hello"})
        missing = json.loads(await bridge.call_server_tool("missing", "chat", {}))
        assert missing["cause"] == "not_found"
        assert call.await_count == 1
    finally:
        bridge.shutdown()
