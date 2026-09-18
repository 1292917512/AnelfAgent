"""MCP 注册治理（readOnlyHint 并行映射 + schema 尺寸上限）单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Generator

import pytest

from core.entity import EntityRegistry
from entities.mcp.bridge import MCPBridge
from entities.mcp.schema import (
    _MAX_PARAM_DESC_CHARS,
    _MAX_TOOL_DESC_CHARS,
    _parse_mcp_tool,
    clip_description,
)

_PROBE_SERVER = "gov-probe"
_PROBE_NAMES = ("probe_read", "probe_write", "probe_fat")


@pytest.fixture()
def bridge() -> Generator[MCPBridge, None, None]:
    instance = MCPBridge()
    yield instance
    instance.shutdown()
    for name in _PROBE_NAMES:
        EntityRegistry.unregister(name)
    EntityRegistry.unregister(f"mcp:{_PROBE_SERVER}")


def _tool(name: str, *, read_only: bool = False, description: str = "d",
          input_schema: dict | None = None) -> SimpleNamespace:
    annotations = SimpleNamespace(readOnlyHint=read_only) if read_only else None
    return SimpleNamespace(
        name=name, description=description,
        inputSchema=input_schema or {}, annotations=annotations,
    )


class TestReadOnlyParallelMapping:
    def test_read_only_hint_maps_to_concurrency_safe(self, bridge: MCPBridge) -> None:
        """服务器声明 readOnlyHint 的工具注册为可并行（meta 透传）。"""
        tools = [
            _tool("probe_read", read_only=True),
            _tool("probe_write"),
        ]
        bridge._register_tool_entries(_PROBE_SERVER, tools)

        read_tool = EntityRegistry.get("probe_read")
        write_tool = EntityRegistry.get("probe_write")
        assert read_tool is not None and write_tool is not None
        assert read_tool.meta.get("concurrency_safe") is True
        assert not write_tool.meta.get("concurrency_safe")

    def test_no_annotations_defaults_serial(self, bridge: MCPBridge) -> None:
        """无 annotations 的工具（旧版 server）保持串行（fail-closed）。"""
        tools = [SimpleNamespace(name="probe_write", description="d", inputSchema={})]
        bridge._register_tool_entries(_PROBE_SERVER, tools)
        tool = EntityRegistry.get("probe_write")
        assert tool is not None
        assert not tool.meta.get("concurrency_safe")


class TestSchemaSizeGovernance:
    def test_tool_description_clipped(self, bridge: MCPBridge) -> None:
        """超长工具描述注册时截断，字节此后稳定。"""
        fat = "长" * (_MAX_TOOL_DESC_CHARS + 500)
        bridge._register_tool_entries(_PROBE_SERVER, [_tool("probe_fat", description=fat)])
        registered = EntityRegistry.get("probe_fat")
        assert registered is not None
        assert len(registered.description) == _MAX_TOOL_DESC_CHARS
        assert registered.description.endswith("…")

    def test_param_description_clipped(self) -> None:
        schema = {
            "properties": {
                "q": {"type": "string", "description": "查" * (_MAX_PARAM_DESC_CHARS + 100)},
            },
        }
        _, params = _parse_mcp_tool(_tool("probe_fat", input_schema=schema))
        assert len(params[0].description) == _MAX_PARAM_DESC_CHARS
        assert params[0].description.endswith("…")

    def test_oversized_extra_dropped_type_kept(self) -> None:
        """巨型 enum/default 附加键整体丢弃，type 保留（不撑爆工具前缀）。"""
        schema = {
            "properties": {
                "mode": {"type": "string", "enum": [f"option-{i}" for i in range(500)]},
            },
        }
        _, params = _parse_mcp_tool(_tool("probe_fat", input_schema=schema))
        assert params[0].type == "string"
        assert params[0].schema_extra is None

    def test_normal_extra_preserved(self) -> None:
        schema = {
            "properties": {
                "limit": {"type": "integer", "default": 10, "minimum": 1},
            },
        }
        _, params = _parse_mcp_tool(_tool("probe_fat", input_schema=schema))
        assert params[0].schema_extra == {"default": 10, "minimum": 1}

    def test_clip_description_passthrough(self) -> None:
        assert clip_description("短描述") == "短描述"
        assert clip_description("") == ""
