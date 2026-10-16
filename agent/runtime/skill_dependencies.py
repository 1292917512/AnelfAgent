"""把 MCP 连接事实与会话激活状态适配到技能依赖查询契约。"""
from __future__ import annotations

from typing import Literal

from agent.mind.tool_activation import tool_activation
from agent.skills.dependencies import MCPServerState
from entities.mcp.bridge import get_mcp_bridge
from entities.mcp.config import MCPServerStore, _mcp_sleep_enabled


def skill_mcp_snapshot(scope: str | None = None) -> dict[str, MCPServerState]:
    """只读当前配置与内存连接，不启动或激活任何服务。"""
    configs = MCPServerStore().load_config().get("mcpServers", {})
    bridge = get_mcp_bridge()
    connected = bridge.get_connected_servers() if bridge else {}
    result: dict[str, MCPServerState] = {}
    for name, config in configs.items():
        if not isinstance(config, dict):
            continue
        enabled = bool(config.get("enabled", True))
        tools = connected.get(name, [])
        group = f"mcp:{name}"
        activation: Literal["active", "sleeping", "deactivated", "unavailable", "on_demand"] = "unavailable"
        if enabled and name in connected and tools:
            sleeping = _mcp_sleep_enabled(name, stay_awake=bool(config.get("stay_awake")))
            if not sleeping:
                activation = "active"
            elif scope is not None and tool_activation.is_deactivated(group, scope):
                activation = "deactivated"
            elif scope is not None and tool_activation.is_active(group, scope):
                activation = "active"
            else:
                activation = "sleeping" if scope is not None else "on_demand"
        result[name] = MCPServerState(enabled, name in connected, len(tools), activation)
    return result
