"""MCP 工具注册名冲突处理（entities.mcp.bridge）单元测试。

测试名一律用全仓唯一的探测名：真实工具名（如 entities.web.tools 的
web_search）经模块级 @tool 注册驻留全局 EntityRegistry——同 worker 的
其他测试只要 import 过该模块，"MCP 先占原名（无冲突）"的前提就会随
测试分布（xdist worker 数不同）随机破产。探测名与真实名走完全相同的
冲突检测/前缀让位/清理保护代码路径，名字本身不承载逻辑。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Generator

import pytest

from core.entity import EntityRegistry
from entities.mcp.bridge import MCPBridge

# 事故回归场景探测名（2026-09 send_message 丢失）；fetch 探测名同理
_PROBE = "sendmsg_probe"
_PROBE_FETCH = "fetch_probe"
_PROBE_SERVER_A = "minimax-coding-plan"
_PROBE_SERVER_B = "web-fetch"

# 全部可能产生的注册名（原名 + 两个 server 前缀名 + 分组实体），
# 断言中途失败也不向全局注册表泄漏
_ALL_PROBE_NAMES = (
    _PROBE,
    f"{_PROBE_SERVER_A}__{_PROBE}",
    f"{_PROBE_SERVER_B}__{_PROBE}",
    _PROBE_FETCH,
    f"mcp:{_PROBE_SERVER_A}",
)


@pytest.fixture()
def bridge() -> Generator[MCPBridge, None, None]:
    instance = MCPBridge()
    yield instance
    instance.shutdown()
    for name in _ALL_PROBE_NAMES:
        EntityRegistry.unregister(name)


def _register_internal_probe() -> None:
    def probe_tool(query: str) -> str:
        """内置同名工具（覆盖语义探测）。"""
        return "{}"

    EntityRegistry.register_tool(
        name=_PROBE,
        func=probe_tool,
        description="内置同名工具",
        group="web",
        source="internal",
    )


def test_conflicting_tool_registered_with_prefix(bridge: MCPBridge) -> None:
    """MCP 工具与内置工具同名时，应加 server 前缀注册，内置工具保留。"""
    _register_internal_probe()

    fake_tools = [SimpleNamespace(name=_PROBE, description="minimax 搜索", inputSchema={})]
    registered = bridge._register_tool_entries(_PROBE_SERVER_A, fake_tools)

    assert registered == [f"{_PROBE_SERVER_A}__{_PROBE}"]

    internal = EntityRegistry.get(_PROBE)
    assert internal is not None
    assert internal.source == "internal"
    assert internal.group == "web"

    mcp_tool = EntityRegistry.get(f"{_PROBE_SERVER_A}__{_PROBE}")
    assert mcp_tool is not None
    assert mcp_tool.source == "mcp"
    assert mcp_tool.group == f"mcp:{_PROBE_SERVER_A}"
    assert bridge._tool_server_map[f"{_PROBE_SERVER_A}__{_PROBE}"] == _PROBE_SERVER_A
    assert bridge._tool_original_names[f"{_PROBE_SERVER_A}__{_PROBE}"] == _PROBE


def test_cleanup_does_not_remove_internal_tool(bridge: MCPBridge) -> None:
    """清理 MCP server 实体时，不得注销被占用名的内置工具。"""
    _register_internal_probe()

    fake_tools = [SimpleNamespace(name=_PROBE, description="minimax 搜索", inputSchema={})]
    bridge._register_tool_entries(_PROBE_SERVER_A, fake_tools)

    bridge._cleanup_server_entities(_PROBE_SERVER_A)

    internal = EntityRegistry.get(_PROBE)
    assert internal is not None
    assert internal.source == "internal"
    assert EntityRegistry.get(f"{_PROBE_SERVER_A}__{_PROBE}") is None
    assert f"{_PROBE_SERVER_A}__{_PROBE}" not in bridge._tool_server_map
    assert f"{_PROBE_SERVER_A}__{_PROBE}" not in bridge._tool_original_names


def test_non_conflicting_tool_keeps_original_name(bridge: MCPBridge) -> None:
    """无冲突时 MCP 工具按原名注册，不记录原始名映射。"""
    fake_tools = [SimpleNamespace(name=_PROBE_FETCH, description="抓取", inputSchema={})]
    registered = bridge._register_tool_entries(_PROBE_SERVER_B, fake_tools)

    assert registered == [_PROBE_FETCH]
    assert EntityRegistry.get(_PROBE_FETCH) is not None
    assert _PROBE_FETCH not in bridge._tool_original_names


def test_call_tool_uses_original_name(bridge: MCPBridge) -> None:
    """重命名工具调用时应还原为 MCP 原始工具名。"""
    _register_internal_probe()

    fake_tools = [SimpleNamespace(name=_PROBE, description="minimax 搜索", inputSchema={})]
    bridge._register_tool_entries(_PROBE_SERVER_A, fake_tools)

    called: list[tuple[str, str]] = []

    async def fake_do_call(server_name: str, tool_name: str, arguments: dict) -> str:
        called.append((server_name, tool_name))
        return "{}"

    bridge._do_call_tool = fake_do_call  # type: ignore[method-assign]
    bridge._sessions[_PROBE_SERVER_A] = object()

    async def _run() -> None:
        await bridge.call_tool(f"{_PROBE_SERVER_A}__{_PROBE}", {"query": "test"})

    # call_tool 在非 MCP 事件循环中会调度到 bridge 的 loop 执行
    asyncio.run(_run())

    assert called == [(_PROBE_SERVER_A, _PROBE)]


def test_override_by_internal_yields_prefixed_mcp_tool(bridge: MCPBridge) -> None:
    """重名仲裁（启动顺序：MCP 先注册）：内置工具后注册覆盖原名时，
    MCP 工具经覆盖监听让位为 {server}__{name} 前缀名，两侧都可用。"""
    fake_tools = [SimpleNamespace(name=_PROBE, description="mcp 搜索", inputSchema={})]
    assert bridge._register_tool_entries(_PROBE_SERVER_B, fake_tools) == [_PROBE]

    _register_internal_probe()  # 内置覆盖原名

    internal = EntityRegistry.get(_PROBE)
    assert internal is not None and internal.source == "internal"
    renamed = EntityRegistry.get(f"{_PROBE_SERVER_B}__{_PROBE}")
    assert renamed is not None and renamed.source == "mcp"
    assert renamed.group == f"mcp:{_PROBE_SERVER_B}"
    assert bridge._tool_server_map[f"{_PROBE_SERVER_B}__{_PROBE}"] == _PROBE_SERVER_B
    assert bridge._tool_original_names[f"{_PROBE_SERVER_B}__{_PROBE}"] == _PROBE
    EntityRegistry.unregister(f"{_PROBE_SERVER_B}__{_PROBE}")


def test_cleanup_preserves_name_overtaken_by_internal(bridge: MCPBridge) -> None:
    """事故回归（2026-09 send_message 丢失）：MCP 先占原名 → 内置工具覆盖 →
    server 清理/重载不得注销覆盖者；MCP 重新注册时应让位为前缀名。"""
    # 1. MCP 先以原名注册（无冲突）
    fake_tools = [SimpleNamespace(name=_PROBE, description="mcp 搜索", inputSchema={})]
    assert bridge._register_tool_entries(_PROBE_SERVER_B, fake_tools) == [_PROBE]
    assert EntityRegistry.get(_PROBE).source == "mcp"  # type: ignore[union-attr]

    # 2. 内置/频道工具同名覆盖（EntityRegistry 覆盖语义）
    _register_internal_probe()
    assert EntityRegistry.get(_PROBE).source == "internal"  # type: ignore[union-attr]

    # 3. server 清理：不得误注销覆盖者的注册名
    bridge._cleanup_server_entities(_PROBE_SERVER_B)
    internal = EntityRegistry.get(_PROBE)
    assert internal is not None and internal.source == "internal"
    assert _PROBE not in bridge._tool_server_map

    # 4. MCP 重新注册：名字被内置占用 → 让位为前缀名
    assert bridge._register_tool_entries(_PROBE_SERVER_B, fake_tools) \
        == [f"{_PROBE_SERVER_B}__{_PROBE}"]
    assert EntityRegistry.get(_PROBE).source == "internal"  # type: ignore[union-attr]
    EntityRegistry.unregister(f"{_PROBE_SERVER_B}__{_PROBE}")


def test_register_server_tools_async_wrapper(bridge: MCPBridge) -> None:
    """异步入口 _register_server_tools：list_tools → 注册 → 记录工具清单。"""
    _register_internal_probe()

    class _FakeToolsResult:
        tools = [SimpleNamespace(name=_PROBE, description="minimax 搜索", inputSchema={})]

    class _FakeSession:
        async def list_tools(self) -> _FakeToolsResult:
            return _FakeToolsResult()

    srv = SimpleNamespace(name=_PROBE_SERVER_A, transport="stdio", command="uvx", url="")

    async def _run() -> int:
        return await bridge._register_server_tools(srv, _FakeSession())

    count = asyncio.run(_run())

    assert count == 1
    internal = EntityRegistry.get(_PROBE)
    assert internal is not None
    assert internal.source == "internal"
    mcp_entity = EntityRegistry.get(f"mcp:{_PROBE_SERVER_A}")
    assert mcp_entity is not None
    assert mcp_entity.meta["tools"] == [f"{_PROBE_SERVER_A}__{_PROBE}"]
    EntityRegistry.unregister(f"mcp:{_PROBE_SERVER_A}")


def test_registry_cross_source_overwrite_replaces() -> None:
    """跨来源同名注册仍覆盖（由 bridge 侧加前缀规避，注册表行为保持不变）。"""
    _register_internal_probe()

    def replacement(query: str) -> str:
        return "{}"

    EntityRegistry.register_tool(
        name=_PROBE,
        func=replacement,
        description="覆盖",
        group="mcp:x",
        source="mcp",
    )

    entity = EntityRegistry.get(_PROBE)
    assert entity is not None
    assert entity.source == "mcp"
    EntityRegistry.unregister(_PROBE)
