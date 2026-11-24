"""已安装插件的只读运行时视图。读取注册表与 MCP 连接快照。"""

from typing import Any

from core.entity import EntityRegistry
from core.plugins.store import InstalledPlugin
from entities.mcp.bridge import get_mcp_bridge


def plugin_detail(record: InstalledPlugin) -> dict[str, Any]:
    bridge = get_mcp_bridge() if record.mcp_servers else None
    servers = {s["name"]: s for s in bridge.list_available_servers() if s["plugin"] == record.name} if bridge else {}
    return {
        **record.to_dict(),
        "runtime": {
            "registered": EntityRegistry.get(f"plugin:{record.name}") is not None,
            "servers": [
                {"name": name, "state": ("connected" if servers[name]["connected"] else "disconnected")
                 if name in servers else ("missing" if bridge else "unknown")}
                for name in record.mcp_servers
            ],
        },
    }
