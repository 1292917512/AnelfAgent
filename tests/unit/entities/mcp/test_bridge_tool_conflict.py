"""MCP 工具注册名冲突处理（entities.mcp.bridge）单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Generator

import pytest

from core.entity import EntityRegistry
from entities.mcp.bridge import MCPBridge


@pytest.fixture()
def bridge() -> Generator[MCPBridge, None, None]:
    instance = MCPBridge()
    yield instance
    instance.shutdown()
    for name in ("web_search", "minimax-coding-plan__web_search", "fetch"):
        EntityRegistry.unregister(name)


def _register_internal_web_search() -> None:
    def web_search(query: str) -> str:
        """内置百度搜索工具。"""
        return "{}"

    EntityRegistry.register_tool(
        name="web_search",
        func=web_search,
        description="内置百度搜索",
        group="web",
        source="internal",
    )


def test_conflicting_tool_registered_with_prefix(bridge: MCPBridge) -> None:
    """MCP 工具与内置工具同名时，应加 server 前缀注册，内置工具保留。"""
    _register_internal_web_search()

    fake_tools = [SimpleNamespace(name="web_search", description="minimax 搜索", inputSchema={})]
    registered = bridge._register_tool_entries("minimax-coding-plan", fake_tools)

    assert registered == ["minimax-coding-plan__web_search"]

    internal = EntityRegistry.get("web_search")
    assert internal is not None
    assert internal.source == "internal"
    assert internal.group == "web"

    mcp_tool = EntityRegistry.get("minimax-coding-plan__web_search")
    assert mcp_tool is not None
    assert mcp_tool.source == "mcp"
    assert mcp_tool.group == "mcp:minimax-coding-plan"
    assert bridge._tool_server_map["minimax-coding-plan__web_search"] == "minimax-coding-plan"
    assert bridge._tool_original_names["minimax-coding-plan__web_search"] == "web_search"


def test_cleanup_does_not_remove_internal_tool(bridge: MCPBridge) -> None:
    """清理 MCP server 实体时，不得注销被占用名的内置工具。"""
    _register_internal_web_search()

    fake_tools = [SimpleNamespace(name="web_search", description="minimax 搜索", inputSchema={})]
    bridge._register_tool_entries("minimax-coding-plan", fake_tools)

    bridge._cleanup_server_entities("minimax-coding-plan")

    internal = EntityRegistry.get("web_search")
    assert internal is not None
    assert internal.source == "internal"
    assert EntityRegistry.get("minimax-coding-plan__web_search") is None
    assert "minimax-coding-plan__web_search" not in bridge._tool_server_map
    assert "minimax-coding-plan__web_search" not in bridge._tool_original_names


def test_non_conflicting_tool_keeps_original_name(bridge: MCPBridge) -> None:
    """无冲突时 MCP 工具按原名注册，不记录原始名映射。"""
    fake_tools = [SimpleNamespace(name="fetch", description="抓取", inputSchema={})]
    registered = bridge._register_tool_entries("web-fetch", fake_tools)

    assert registered == ["fetch"]
    assert EntityRegistry.get("fetch") is not None
    assert "fetch" not in bridge._tool_original_names


def test_call_tool_uses_original_name(bridge: MCPBridge) -> None:
    """重命名工具调用时应还原为 MCP 原始工具名。"""
    _register_internal_web_search()

    fake_tools = [SimpleNamespace(name="web_search", description="minimax 搜索", inputSchema={})]
    bridge._register_tool_entries("minimax-coding-plan", fake_tools)

    called: list[tuple[str, str]] = []

    async def fake_do_call(server_name: str, tool_name: str, arguments: dict) -> str:
        called.append((server_name, tool_name))
        return "{}"

    bridge._do_call_tool = fake_do_call  # type: ignore[method-assign]
    bridge._sessions["minimax-coding-plan"] = object()

    async def _run() -> None:
        await bridge.call_tool("minimax-coding-plan__web_search", {"query": "test"})

    # call_tool 在非 MCP 事件循环中会调度到 bridge 的 loop 执行
    asyncio.run(_run())

    assert called == [("minimax-coding-plan", "web_search")]


def test_register_server_tools_async_wrapper(bridge: MCPBridge) -> None:
    """异步入口 _register_server_tools：list_tools → 注册 → 记录工具清单。"""
    _register_internal_web_search()

    class _FakeToolsResult:
        tools = [SimpleNamespace(name="web_search", description="minimax 搜索", inputSchema={})]

    class _FakeSession:
        async def list_tools(self) -> _FakeToolsResult:
            return _FakeToolsResult()

    srv = SimpleNamespace(name="minimax-coding-plan", transport="stdio", command="uvx", url="")

    async def _run() -> int:
        return await bridge._register_server_tools(srv, _FakeSession())

    count = asyncio.run(_run())

    assert count == 1
    internal = EntityRegistry.get("web_search")
    assert internal is not None
    assert internal.source == "internal"
    mcp_entity = EntityRegistry.get("mcp:minimax-coding-plan")
    assert mcp_entity is not None
    assert mcp_entity.meta["tools"] == ["minimax-coding-plan__web_search"]
    EntityRegistry.unregister("mcp:minimax-coding-plan")


def test_registry_cross_source_overwrite_replaces() -> None:
    """跨来源同名注册仍覆盖（由 bridge 侧加前缀规避，注册表行为保持不变）。"""
    _register_internal_web_search()

    def replacement(query: str) -> str:
        return "{}"

    EntityRegistry.register_tool(
        name="web_search",
        func=replacement,
        description="覆盖",
        group="mcp:x",
        source="mcp",
    )

    entity = EntityRegistry.get("web_search")
    assert entity is not None
    assert entity.source == "mcp"
    EntityRegistry.unregister("web_search")
