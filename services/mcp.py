"""MCP 服务管理服务 -- 配置读写、连接管理。"""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.path import ConfigPaths


class MCPService:

    # ------------------------------------------------------------------
    # 配置读写
    # ------------------------------------------------------------------

    def load_config(self) -> Dict[str, Any]:
        """加载 MCP 服务器配置。"""
        try:
            from agent.config import get_config_provider
            return get_config_provider().get_mcp_config()
        except Exception:
            p = Path(ConfigPaths.MCP_SERVERS)
            if p.exists():
                return json.loads(p.read_text("utf-8"))
        return {"mcpServers": {}}

    def save_config(self, data: Dict[str, Any]) -> None:
        try:
            from agent.config import get_config_provider
            get_config_provider().save_mcp_config(data)
        except Exception:
            Path(ConfigPaths.MCP_SERVERS).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )

    def get_config_json(self) -> str:
        """返回 JSON 文本形式的配置。"""
        return json.dumps(self.load_config(), ensure_ascii=False, indent=2)

    def save_config_json(self, json_str: str) -> None:
        """解析 JSON 文本并保存，自动触发热重载。"""
        data = json.loads(json_str)
        self.save_config(data)
        self._trigger_reload()

    # ------------------------------------------------------------------
    # 服务器列表 / 工具
    # ------------------------------------------------------------------

    def get_server_names(self, data: Optional[Dict[str, Any]] = None) -> List[str]:
        if data is None:
            data = self.load_config()
        return list(data.get("mcpServers", {}).keys())

    def get_connected_tools(self) -> Dict[str, List[str]]:
        """返回已连接 server → 工具名列表。"""
        try:
            from entities.mcp.bridge import get_mcp_bridge
            bridge = get_mcp_bridge()
            if bridge:
                return bridge.get_connected_servers()
        except Exception as e:
            from core.log import log
            log(f"获取 MCP 已连接工具失败: {e}", "DEBUG")
        return {}

    def list_servers(self) -> List[Dict[str, Any]]:
        """返回所有 MCP 服务器的状态摘要。"""
        data = self.load_config()
        connected = self.get_connected_tools()
        result: List[Dict[str, Any]] = []
        for name, cfg in data.get("mcpServers", {}).items():
            enabled = cfg.get("enabled", True) if isinstance(cfg, dict) else True
            url = (cfg.get("url", "") or cfg.get("command", "")) if isinstance(cfg, dict) else ""
            tools = connected.get(name, [])
            result.append({
                "name": name,
                "url": url,
                "enabled": enabled,
                "connected": name in connected,
                "tool_count": len(tools),
                "tools": tools,
            })
        return result

    def get_server_tools(self, name: str) -> List[str]:
        return self.get_connected_tools().get(name, [])

    # ------------------------------------------------------------------
    # 增删 / 连接控制
    # ------------------------------------------------------------------

    def add_server(self, name: str, url: str) -> None:
        data = self.load_config()
        data.setdefault("mcpServers", {})[name] = {"url": url}
        self.save_config(data)
        self._trigger_reload()

    def remove_server(self, name: str) -> None:
        data = self.load_config()
        servers = data.get("mcpServers", {})
        if name in servers:
            del servers[name]
            self.save_config(data)
            self._trigger_reload()

    def toggle_server(self, name: str) -> Dict[str, Any]:
        """连接或断开 MCP 服务器，同时持久化 enabled 状态。返回结构化结果。"""
        from entities.mcp.bridge import get_mcp_bridge
        bridge = get_mcp_bridge()
        if not bridge:
            return {"success": False, "message": "MCP Bridge 未初始化"}
        try:
            if name in bridge.get_connected_servers():
                bridge.disconnect_server_by_name(name)
                self._set_enabled(name, False)
                return {"success": True, "message": f"已断开 {name}"}
            count = bridge.connect_server_by_name(name)
            self._set_enabled(name, True)
            return {
                "success": True,
                "message": f"已连接 {name}，发现 {count} 个工具",
                "tool_count": count,
            }
        except (TimeoutError, concurrent.futures.TimeoutError):
            return {"success": False, "message": f"连接 {name} 超时，请检查服务器是否可用"}
        except ValueError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": f"操作失败: {e}"}

    def _set_enabled(self, name: str, enabled: bool) -> None:
        """更新配置文件中指定 server 的 enabled 字段。"""
        data = self.load_config()
        servers = data.get("mcpServers", {})
        if name in servers and isinstance(servers[name], dict):
            servers[name]["enabled"] = enabled
            self.save_config(data)

    @staticmethod
    def _trigger_reload() -> None:
        """触发 MCP Bridge 配置热重载（静默失败）。"""
        try:
            from entities.mcp.bridge import get_mcp_bridge
            bridge = get_mcp_bridge()
            if bridge:
                bridge.reload_config()
        except Exception as e:
            from core.log import log
            log(f"MCP 配置热重载失败: {e}", "WARNING")
