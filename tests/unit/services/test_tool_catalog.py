"""工具目录来源采用实际注册契约，混合分组不改变工具与开关范围。"""

from __future__ import annotations

import pytest

from core.entity import EntityMetadata, EntityRegistry, EntityType
from services.tool import ToolService, tool_origin


@pytest.mark.parametrize("module, source, owner, meta, expected", [
    ("agent.memory.tools", "internal", "", {}, "core"),
    ("core.tools", "builtin", "", {}, "core"),
    ("agent.channel.output_tools", "channel.output", "", {}, "core"),
    ("agent.channel.manage_tools", "channel.manage", "", {}, "core"),
    ("entities.filesystem.tools", "entity", "", {}, "entity"),
    ("entities.mcp.manage_tools", "mcp", "mcp:entities.mcp.manage_tools", {}, "entity"),
    ("entities.mcp.bridge", "mcp", "mcp:remote", {"mcp_original_name": "read"}, "external"),
    ("agent.channel.tool_bridge", "channel.qq", "", {}, "channel"),
    ("agent.channel.tool_bridge", "channel.auto", "", {}, "channel"),
    ("channels.demo.tools", "internal", "", {}, "channel"),
    ("custom_plugin.weather", "plugin", "", {}, "external"),
])
def test_origin_uses_registration_not_name_group_or_core_tag(module, source, owner, meta, expected):
    def inspect():
        return "ok"

    inspect.__module__ = module
    metadata = EntityMetadata(
        name="core_restart", entity_type=EntityType.TOOL, source=source, owner=owner,
        group="memory", tags=["core"], func=inspect, meta=meta,
    )
    origin = tool_origin(metadata)
    assert origin["kind"] == expected
    if source == "channel.qq":
        assert origin["module"] == "channels.qq"
    elif meta:
        assert origin["module"] == owner
    else:
        assert origin["module"] == module


def test_grouped_and_flat_catalogs_preserve_mixed_members_and_registry_version():
    def builtin():
        return "ok"

    def adapter():
        return "ok"

    builtin.__module__ = "agent.channel.manage_tools"
    adapter.__module__ = "agent.channel.tool_bridge"
    names = ("catalog_builtin", "catalog_channel")
    try:
        EntityRegistry.register_tool(names[0], builtin, group="catalog_mixed")
        EntityRegistry.register_tool(names[1], adapter, group="catalog_mixed", source="channel.qq")
        EntityRegistry.set_enabled(names[1], False, persist=False)
        service = ToolService()
        version = EntityRegistry.version()
        group = next(group for group in service.list_grouped_tools() if group["group"] == "catalog_mixed")
        flat = {tool["name"]: tool for tool in service.list_tools() if tool["name"] in names}
        assert group["total_count"] == 2 and group["enabled_count"] == 1
        assert [tool["origin"]["kind"] for tool in group["tools"]] == ["core", "channel"]
        assert all(tool["origin"] == flat[tool["name"]]["origin"] for tool in group["tools"])
        assert EntityRegistry.version() == version
    finally:
        for name in names:
            EntityRegistry.unregister(name)
