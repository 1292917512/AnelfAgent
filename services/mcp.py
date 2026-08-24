"""MCP 服务管理服务 -- web 侧薄门面。

配置域逻辑（读写/校验/脱敏）已收敛到 ``entities.mcp.config.MCPServerStore``，
本类继承其能力并叠加 bridge 连接状态与实体注册表视图（services → entities 单向）。
"""

from __future__ import annotations

import concurrent.futures
from typing import Any, Dict, List

from core.log import log
from core.sanitizer import is_sanitize_enabled, sanitize_text
from entities.mcp.config import MCPServerStore, _mcp_sleep_enabled
from entities.mcp.config import apply_sleep_policy as _apply_sleep_policy


def _mask_display(text: str) -> str:
    """展示用脱敏：遮盖 URL 等文本中可能内嵌的密钥。"""
    if not text or not is_sanitize_enabled():
        return text
    return sanitize_text(text)


def _reload_bridge() -> None:
    """触发 MCP Bridge 配置热重载（静默失败）。"""
    try:
        from entities.mcp.bridge import get_mcp_bridge
        bridge = get_mcp_bridge()
        if bridge:
            bridge.reload_config()
    except Exception as e:
        log(f"MCP 配置热重载失败: {e}", "WARNING")


class MCPService(MCPServerStore):
    """web 侧 MCP 门面：配置域能力继承 MCPServerStore，连接状态读 bridge。"""

    def __init__(self) -> None:
        super().__init__(on_reload=_reload_bridge)

    def get_connected_tools(self) -> Dict[str, List[str]]:
        """返回已连接 server → 工具名列表。"""
        try:
            from entities.mcp.bridge import get_mcp_bridge
            bridge = get_mcp_bridge()
            if bridge:
                return bridge.get_connected_servers()
        except Exception as e:
            log(f"获取 MCP 已连接工具失败: {e}", "DEBUG")
        return {}

    def get_last_errors(self) -> Dict[str, str]:
        """返回各 server 最近一次连接错误（name → 错误详情）。"""
        try:
            from entities.mcp.bridge import get_mcp_bridge
            bridge = get_mcp_bridge()
            if bridge:
                return bridge.get_last_errors()
        except Exception as e:
            log(f"获取 MCP 连接错误信息失败: {e}", "DEBUG")
        return {}

    def list_servers(self) -> List[Dict[str, Any]]:
        """返回所有 MCP 服务器的状态摘要（url 为展示用，已脱敏）。"""
        data = self.load_config()
        connected = self.get_connected_tools()
        errors = self.get_last_errors()
        result: List[Dict[str, Any]] = []
        for name, cfg in data.get("mcpServers", {}).items():
            if not isinstance(cfg, dict):
                cfg = {}
            enabled = cfg.get("enabled", True)
            raw_url = cfg.get("url", "") or cfg.get("command", "")
            tools = connected.get(name, [])
            stay_awake = bool(cfg.get("stay_awake", False))
            result.append({
                "name": name,
                "url": _mask_display(raw_url),
                "transport": self._infer_transport(cfg),
                "enabled": enabled,
                "connected": name in connected,
                "tool_count": len(tools),
                "tools": tools,
                "last_error": errors.get(name, ""),
                # 常驻开关（不沉睡，schema 常驻）+ 当前生效的沉睡状态
                "stay_awake": stay_awake,
                "sleeping": self._effective_sleeping(name),
            })
        return result

    @staticmethod
    def _effective_sleeping(name: str) -> bool:
        """该服务当前生效的沉睡状态（策略求值；未连接时按配置策略返回）。"""
        try:
            return _mcp_sleep_enabled(name)
        except Exception:
            return False

    @staticmethod
    def apply_sleep_policy(name: str) -> bool:
        """按当前策略刷新某服务已注册工具的沉睡标记（委托 entities.mcp.config）。"""
        return _apply_sleep_policy(name)

    def get_server_tools(self, name: str) -> List[str]:
        return self.get_connected_tools().get(name, [])

    def get_server_tool_details(self, name: str) -> List[Dict[str, Any]]:
        """返回指定 server 已注册工具的详情（名称/描述/参数 schema）。"""
        from core.entity import EntityRegistry, EntityType
        details: List[Dict[str, Any]] = []
        for e in EntityRegistry.get_by_type(EntityType.TOOL):
            if e.source != "mcp" or e.group != f"mcp:{name}":
                continue
            params = [
                {
                    "name": p.name,
                    "description": p.description,
                    "type": p.type,
                    "required": p.required,
                    "enum": p.enum,
                }
                for p in e.meta.get("params", [])
            ]
            details.append({
                "name": e.name,
                "description": e.description,
                "params": params,
            })
        return sorted(details, key=lambda d: d["name"])

    # ------------------------------------------------------------------
    # 连接控制
    # ------------------------------------------------------------------

    def toggle_server(self, name: str) -> Dict[str, Any]:
        """连接或断开 MCP 服务器，同时持久化 enabled 状态。返回结构化结果。"""
        from entities.mcp.bridge import extract_exception_detail, get_mcp_bridge
        bridge = get_mcp_bridge()
        if not bridge:
            return {"success": False, "message": "MCP Bridge 未初始化"}
        try:
            if name in bridge.get_connected_servers():
                bridge.disconnect_server_by_name(name)
                self._set_enabled(name, False)
                return {"success": True, "message": f"已断开 {name}"}
            # 连接前先同步磁盘配置，避免配置被外部修改后内存副本过期导致连接失败
            self._trigger_reload()
            if name in bridge.get_connected_servers():
                self._set_enabled(name, True)
                return {"success": True, "message": f"已连接 {name}"}
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
            return {"success": False, "message": f"操作失败: {extract_exception_detail(e)}"}
