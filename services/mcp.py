"""MCP 服务管理服务 -- 配置读写、连接管理。"""

from __future__ import annotations

import concurrent.futures
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from core.path import ConfigPaths
from core.sanitizer import is_sanitize_enabled, sanitize_text

# 配置文件读-改-写串行化锁：Web API、AI 工具、热重载等多条写路径共用，
# 防止并发写互相覆盖丢更新。
_CONFIG_LOCK = threading.RLock()


def _env_config_path() -> Optional[Path]:
    """ANELF_MCP_CONFIG 环境变量指定的配置文件路径（与 bridge 路径解析对齐）。"""
    env = os.getenv("ANELF_MCP_CONFIG", "").strip()
    return Path(env) if env else None


def _mask_display(text: str) -> str:
    """展示用脱敏：遮盖 URL 等文本中可能内嵌的密钥。"""
    if not text or not is_sanitize_enabled():
        return text
    return sanitize_text(text)


class MCPService:
    _SERVER_ALLOWED_FIELDS = frozenset({
        "url",
        "command",
        "args",
        "env",
        "headers",
        "transport",
        "enabled",
        "timeout",
        "sse_read_timeout",
        "call_timeout",
    })
    _SERVER_ALLOWED_TRANSPORTS = frozenset({"stdio", "streamable_http", "sse"})

    # ------------------------------------------------------------------
    # 配置读写
    # ------------------------------------------------------------------

    def load_config(self) -> Dict[str, Any]:
        """加载 MCP 服务器配置。"""
        env_path = _env_config_path()
        if env_path is not None:
            if env_path.exists():
                return json.loads(env_path.read_text("utf-8"))
            return {"mcpServers": {}}
        try:
            from agent.config import get_config_provider
            return get_config_provider().get_mcp_config()
        except Exception:
            p = Path(ConfigPaths.MCP_SERVERS)
            if p.exists():
                return json.loads(p.read_text("utf-8"))
        return {"mcpServers": {}}

    def save_config(self, data: Dict[str, Any]) -> None:
        env_path = _env_config_path()
        if env_path is not None:
            env_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            return
        try:
            from agent.config import get_config_provider
            get_config_provider().save_mcp_config(data)
        except Exception:
            Path(ConfigPaths.MCP_SERVERS).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )

    @classmethod
    def get_server_config_schema(cls) -> Dict[str, Any]:
        """返回 MCP server 可编辑字段说明。"""
        return {
            "fields": {
                "url": "HTTP/SSE 地址（与 command 二选一）",
                "command": "stdio 模式启动命令（与 url 二选一）",
                "args": "命令参数数组（stdio）",
                "env": "环境变量对象（stdio）",
                "headers": "HTTP 请求头对象（HTTP/SSE）",
                "transport": "stdio / streamable_http / sse",
                "enabled": "是否启用（布尔）",
                "timeout": "连接超时秒数（>0）",
                "sse_read_timeout": "SSE 读取超时秒数（>0）",
                "call_timeout": "工具调用超时秒数（>0）",
            },
            "required_one_of": ["url", "command"],
            "allowed_transports": sorted(cls._SERVER_ALLOWED_TRANSPORTS),
        }

    def get_config_json(self) -> str:
        """返回 JSON 文本形式的配置。"""
        return json.dumps(self.load_config(), ensure_ascii=False, indent=2)

    def save_config_json(self, json_str: str) -> None:
        """解析 JSON 文本并保存，自动触发热重载。"""
        data = json.loads(json_str)
        if not isinstance(data, dict):
            raise ValueError("配置必须是 JSON 对象")
        with _CONFIG_LOCK:
            self.save_config(data)
        self._trigger_reload()

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if value is None:
            return False
        return bool(value)

    @staticmethod
    def _parse_object_like(value: Any, field_name: str) -> Dict[str, str]:
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError(f"{field_name} 必须是对象")
            return {str(k): str(v) for k, v in parsed.items()}
        raise ValueError(f"{field_name} 必须是对象")

    @staticmethod
    def _parse_args_like(value: Any) -> List[str]:
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text.split()
            if not isinstance(parsed, list):
                raise ValueError("args 必须是数组")
            return [str(v) for v in parsed]
        raise ValueError("args 必须是数组")

    @classmethod
    def _normalize_server_patch(cls, patch: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("patch 必须是 JSON 对象")
        unknown = sorted(set(patch.keys()) - cls._SERVER_ALLOWED_FIELDS)
        if unknown:
            raise ValueError(f"包含不支持的字段: {', '.join(unknown)}")

        normalized: Dict[str, Any] = {}
        for key, val in patch.items():
            if val is None:
                normalized[key] = None
                continue
            if key in {"url", "command"}:
                text = str(val).strip()
                normalized[key] = text or None
                continue
            if key == "transport":
                text = str(val).strip().lower()
                if not text:
                    normalized[key] = None
                elif text in cls._SERVER_ALLOWED_TRANSPORTS:
                    normalized[key] = text
                else:
                    raise ValueError(
                        f"transport 必须是 {', '.join(sorted(cls._SERVER_ALLOWED_TRANSPORTS))}"
                    )
                continue
            if key == "enabled":
                normalized[key] = cls._to_bool(val)
                continue
            if key == "args":
                normalized[key] = cls._parse_args_like(val)
                continue
            if key in {"env", "headers"}:
                normalized[key] = cls._parse_object_like(val, key)
                continue
            if key in {"timeout", "sse_read_timeout", "call_timeout"}:
                num = float(val)
                if num <= 0:
                    raise ValueError(f"{key} 必须 > 0")
                normalized[key] = num
                continue
        return normalized

    @classmethod
    def _finalize_server_config(cls, cfg: Dict[str, Any]) -> Dict[str, Any]:
        final = dict(cfg)
        if final.get("url") in ("", None):
            final.pop("url", None)
        if final.get("command") in ("", None):
            final.pop("command", None)
        if not final.get("url") and not final.get("command"):
            raise ValueError("MCP server 配置必须至少包含 url 或 command")

        transport = str(final.get("transport", "") or "").strip().lower()
        if not transport:
            transport = "stdio" if final.get("command") else "streamable_http"
            final["transport"] = transport
        elif transport not in cls._SERVER_ALLOWED_TRANSPORTS:
            raise ValueError(
                f"transport 必须是 {', '.join(sorted(cls._SERVER_ALLOWED_TRANSPORTS))}"
            )
        else:
            final["transport"] = transport

        if final.get("command") and "args" not in final:
            final["args"] = []
        if "args" in final and not isinstance(final["args"], list):
            raise ValueError("args 必须是数组")

        if "env" in final and not isinstance(final["env"], dict):
            raise ValueError("env 必须是对象")
        if "headers" in final and not isinstance(final["headers"], dict):
            raise ValueError("headers 必须是对象")

        if "enabled" not in final:
            final["enabled"] = True
        else:
            final["enabled"] = cls._to_bool(final["enabled"])

        for key in ("timeout", "sse_read_timeout", "call_timeout"):
            if key in final:
                num = float(final[key])
                if num <= 0:
                    raise ValueError(f"{key} 必须 > 0")
                final[key] = num

        return final

    # ------------------------------------------------------------------
    # 服务器列表 / 工具
    # ------------------------------------------------------------------

    def get_server_names(self, data: Optional[Dict[str, Any]] = None) -> List[str]:
        if data is None:
            data = self.load_config()
        return list(data.get("mcpServers", {}).keys())

    def get_server_config(self, name: str) -> Optional[Dict[str, Any]]:
        """返回单个 server 的原始配置。"""
        data = self.load_config()
        raw = data.get("mcpServers", {}).get(name)
        if isinstance(raw, dict):
            return dict(raw)
        return None

    def update_server_config(
        self,
        name: str,
        patch: Dict[str, Any],
        *,
        replace: bool = False,
        remove_fields: Optional[List[str]] = None,
        create_if_missing: bool = False,
        reload: bool = True,
    ) -> Dict[str, Any]:
        """更新指定 server 配置（merge 或 replace），并可选热重载。"""
        with _CONFIG_LOCK:
            data = self.load_config()
            servers = data.setdefault("mcpServers", {})
            existing_raw = servers.get(name)
            if existing_raw is None and not create_if_missing:
                raise ValueError(f"服务器 '{name}' 不存在")
            if existing_raw is not None and not isinstance(existing_raw, dict):
                raise ValueError(f"服务器 '{name}' 配置格式非法")

            before = dict(existing_raw) if isinstance(existing_raw, dict) else {}
            current = {} if replace else dict(before)

            for field in (remove_fields or []):
                f = str(field).strip()
                if f:
                    current.pop(f, None)

            normalized_patch = self._normalize_server_patch(patch)
            for key, val in normalized_patch.items():
                if val is None:
                    current.pop(key, None)
                else:
                    current[key] = val

            final_cfg = self._finalize_server_config(current)
            servers[name] = final_cfg
            self.save_config(data)
        if reload:
            self._trigger_reload()

        return {
            "name": name,
            "before": before,
            "after": final_cfg,
            "reloaded": reload,
        }

    def set_server_enabled(self, name: str, enabled: bool, *, reload: bool = True) -> Dict[str, Any]:
        """显式设置 server 的 enabled 状态。"""
        with _CONFIG_LOCK:
            data = self.load_config()
            servers = data.get("mcpServers", {})
            if name not in servers or not isinstance(servers[name], dict):
                raise ValueError(f"服务器 '{name}' 不存在")
            servers[name]["enabled"] = bool(enabled)
            self.save_config(data)
        if reload:
            self._trigger_reload()
        return {"name": name, "enabled": bool(enabled), "reloaded": reload}

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

    @staticmethod
    def _infer_transport(cfg: Dict[str, Any]) -> str:
        transport = str(cfg.get("transport", "") or "").strip()
        if transport:
            return transport
        return "stdio" if cfg.get("command") else "streamable_http"

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
            result.append({
                "name": name,
                "url": _mask_display(raw_url),
                "transport": self._infer_transport(cfg),
                "enabled": enabled,
                "connected": name in connected,
                "tool_count": len(tools),
                "tools": tools,
                "last_error": errors.get(name, ""),
            })
        return result

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
    # 增删 / 连接控制
    # ------------------------------------------------------------------

    def add_server(self, name: str, url: str) -> None:
        with _CONFIG_LOCK:
            data = self.load_config()
            data.setdefault("mcpServers", {})[name] = {"url": url}
            self.save_config(data)
        self._trigger_reload()

    def create_server(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """以完整字段创建 server（统一走校验与热重载）；已存在时拒绝覆盖。"""
        with _CONFIG_LOCK:
            data = self.load_config()
            if name in data.get("mcpServers", {}):
                raise ValueError(f"服务器 '{name}' 已存在")
        return self.update_server_config(
            name, config, replace=True, create_if_missing=True, reload=True,
        )

    def remove_server(self, name: str) -> None:
        with _CONFIG_LOCK:
            data = self.load_config()
            servers = data.get("mcpServers", {})
            if name not in servers:
                raise ValueError(f"服务器 '{name}' 不存在")
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
        with _CONFIG_LOCK:
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
            log(f"MCP 配置热重载失败: {e}", "WARNING")
