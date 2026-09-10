"""MCP 服务管理服务 -- web 侧薄门面。

配置域逻辑（读写/校验/脱敏）已收敛到 ``entities.mcp.config.MCPServerStore``，
本类继承其能力并叠加 bridge 连接状态与实体注册表视图（services → entities 单向）。
"""

from __future__ import annotations

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
        """切换 MCP 服务器的启用状态（启用⇄禁用），返回结构化结果。

        以配置文件的 enabled 为准而非当前连接状态：已启用（无论是否连上）
        → 禁用并断开；已禁用 → 启用并热重载连接。此前按连接状态判断，
        已启用但连不上（目标不可用）的 server 点按钮只会反复尝试连接，
        永远无法禁用，导致每次重启都自动重连报错。
        """
        from entities.mcp.bridge import extract_exception_detail, get_mcp_bridge
        bridge = get_mcp_bridge()
        if not bridge:
            return {"success": False, "message": "MCP Bridge 未初始化"}
        try:
            cfg = self.get_server_config(name)
            if cfg is None:
                return {"success": False, "message": f"服务器 '{name}' 不存在"}
            enabling = not cfg.get("enabled", True)
            # 落盘 + 热重载：禁用走 reload 的断开分支，启用走 reload 的连接分支
            self.set_server_enabled(name, enabling)
            if not enabling:
                return {
                    "success": True, "enabled": False, "connected": False,
                    "message": f"已禁用 {name}（重启后不再自动连接）",
                }
            connected_map = bridge.get_connected_servers()
            if name in connected_map:
                tools = connected_map[name]
                return {
                    "success": True, "enabled": True, "connected": True,
                    "message": f"已启用并连接 {name}，发现 {len(tools)} 个工具",
                    "tool_count": len(tools),
                }
            err = bridge.get_last_errors().get(name, "")
            return {
                "success": True, "enabled": True, "connected": False,
                "message": f"已启用 {name}，但连接失败: {err or '目标不可用'}（重启后会自动重试）",
            }
        except Exception as e:
            return {"success": False, "message": f"操作失败: {extract_exception_detail(e)}"}

    # ------------------------------------------------------------------
    # OAuth 授权状态（entities.mcp.oauth 的 services 侧收口）
    # ------------------------------------------------------------------

    async def oauth_status(self, name: str = "") -> Dict[str, Any]:
        """OAuth 状态：单 server 或全部（凭据存在性 + 待授权链接）。"""
        import asyncio as _asyncio

        from entities.mcp.oauth import has_credentials, pending_auth

        pending = await _asyncio.to_thread(pending_auth)
        if name:
            entry = pending.get(name) or {}
            return {
                "server": name,
                "authorized": await _asyncio.to_thread(has_credentials, name),
                "pending_url": entry.get("url", ""),
            }
        result: Dict[str, Any] = {}
        for srv in self.list_servers():
            srv_name = srv.get("name", "")
            entry = pending.get(srv_name) or {}
            result[srv_name] = {
                "authorized": await _asyncio.to_thread(has_credentials, srv_name),
                "pending_url": entry.get("url", ""),
            }
        return result

    async def oauth_logout(self, name: str) -> Dict[str, Any]:
        """清除 OAuth 凭据（下次连接重新授权）。"""
        import asyncio as _asyncio

        from entities.mcp.oauth import delete_credentials

        removed = await _asyncio.to_thread(delete_credentials, name)
        return {"server": name, "removed": removed}
