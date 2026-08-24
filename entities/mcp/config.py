"""MCP 配置模块：server 配置模型、配置文件加载、配置存取校验与工具组沉睡策略。

职责：
1. ``_MCP_CONFIGS`` 配置项声明（import 时经 register_configs_safe 注册一次）；
2. MCPServerConfig / MCPConfig 数据模型与 mcp_servers.json 解析加载；
3. ``MCPServerStore``：mcp_servers.json 的读写、字段校验与脱敏（纯 MCP 域，
   不依赖 bridge/services；热重载经 on_reload 钩子由上层注入）；
4. 工具组沉睡策略（全局默认 / 排除名单 / 每服务 stay_awake 覆盖三级求值）。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.config import register_configs_safe
from core.entity import EntityRegistry, EntityType
from core.log import log
from core.path import ConfigPaths

_DEFAULT_CALL_TIMEOUT = 300.0

_MCP_CONFIGS = {
    "entity/mcp": {
        "mcp_stdio_passthrough_env": {
            "description": "是否向 stdio 子进程透传全量环境变量（默认仅白名单）",
            "default": False,
        },
        "mcp_tools_sleep_default": {
            "description": "是否默认沉睡 MCP 服务工具组（需要时调 activate_tool_group 唤醒；缩小 tools 前缀提升缓存命中）",
            "default": True,
        },
        "mcp_sleep_excludes": {
            "description": "不沉睡的 MCP 服务名单（逗号分隔的服务名，如 git,excel；高频使用的服务可常驻）",
            "default": "",
        },
        "tool_activation_sticky": {
            "description": "是否让激活的分组保持粘性不再过期沉睡（避免激活/过期反复重写缓存前缀）",
            "default": True,
        },
        "mcp_tool_list_sync": {
            "description": "收到 server 的工具列表变更通知时自动重同步注册"
                           "（关闭则仅在重连/手动 reload 时刷新）",
            "default": True,
        },
        "mcp_image_passthrough": {
            "description": "MCP 工具返回的图片落盘并经多模态约定注入"
                           "（视觉模型可直接看到截图；关闭则仅保留文本占位）",
            "default": True,
        },
    },
}


def _server_stay_awake(server_name: str) -> bool:
    """读取 mcp_servers.json 中该服务的 stay_awake 覆盖（每服务常驻开关）。"""
    try:
        import json as _json
        import os as _os

        from core.path import ConfigPaths
        path = ConfigPaths.MCP_SERVERS
        if not _os.path.isfile(path):
            return False
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        cfg = (data.get("mcpServers") or {}).get(server_name) or {}
        return bool(cfg.get("stay_awake"))
    except Exception:
        return False


def _mcp_sleep_enabled(server_name: str) -> bool:
    """该 MCP 服务的工具组是否沉睡。

    优先级：每服务 stay_awake 覆盖（mcp_servers.json）> 全局排除名单 > 全局默认。
    """
    from core.config import get_config, get_config_bool
    if not get_config_bool("mcp_tools_sleep_default", True):
        return False
    if _server_stay_awake(server_name):
        return False
    excludes = str(get_config("mcp_sleep_excludes", "") or "")
    excluded = {s.strip() for s in excludes.split(",") if s.strip()}
    return server_name not in excluded


def apply_sleep_policy(server_name: str) -> bool:
    """按当前策略刷新某服务已注册工具的沉睡标记（stay_awake 切换后即时生效）。

    就地更新实体 meta 并推进注册表版本（无需重连/重启，
    下一个会话的工具集装配自动应用新策略）。
    """
    sleep = _mcp_sleep_enabled(server_name)
    group = f"mcp:{server_name}"
    tools = [
        e for e in EntityRegistry.get_by_group(group)
        if e.entity_type == EntityType.TOOL
    ]
    if not tools:
        return False
    brief = f"MCP 服务 {server_name}（{len(tools)} 个工具）"
    for e in tools:
        e.meta["allow_sleep"] = sleep
        e.meta["sleep_brief"] = brief if sleep else ""
    EntityRegistry.bump_version()
    log(
        f"MCP 沉睡策略已应用: {server_name} → {'沉睡' if sleep else '常驻'} ({len(tools)} 工具)",
        tag="MCP",
    )
    return True


register_configs_safe(_MCP_CONFIGS)


# ------------------------------------------------------------------
# Config models
# ------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """单个 MCP server 配置。

    支持三种传输方式：
    - stdio: 填 command + args（启动子进程）
    - sse: 填 url（SSE 传输，旧协议）
    - streamable_http: 填 url（Streamable HTTP 传输，新协议，默认）
    """

    name: str
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    transport: str = ""
    enabled: bool = False
    timeout: float = 5.0
    sse_read_timeout: float = 300.0
    call_timeout: float = _DEFAULT_CALL_TIMEOUT

    def fingerprint(self) -> Dict[str, Any]:
        """用于比较配置是否变更的字典（排除 name）。"""
        d = asdict(self)
        d.pop("name", None)
        return d


@dataclass
class MCPConfig:
    """MCP 全局配置。"""

    servers: List[MCPServerConfig] = field(default_factory=list)


def _resolve_config_path() -> Optional[str]:
    """定位 MCP 配置文件路径（env 优先，ConfigPaths 为唯一权威默认值）。"""
    for env_key in ("ANELF_MCP_CONFIG", "ANELF_MCP_CONFIG_PATH"):
        env = os.getenv(env_key, "")
        if env:
            return env
    for c in [Path(ConfigPaths.MCP_SERVERS), Path("mcp_servers.json")]:
        if c.exists():
            return str(c)
    return None


def _parse_mcp_data(data: Dict[str, Any]) -> List[MCPServerConfig]:
    """从 JSON dict 解析 server 列表（兼容 mcpServers 包装格式和旧版 servers 列表格式）。"""
    servers: List[MCPServerConfig] = []
    if "mcpServers" in data:
        for name, cfg in data["mcpServers"].items():
            if not isinstance(cfg, dict):
                continue
            fields = {k: v for k, v in cfg.items() if k in MCPServerConfig.__dataclass_fields__}
            fields["name"] = name
            servers.append(MCPServerConfig(**fields))
    elif "servers" in data:
        for s in data["servers"]:
            servers.append(MCPServerConfig(
                **{k: v for k, v in s.items() if k in MCPServerConfig.__dataclass_fields__}
            ))
    return servers


def load_mcp_config(path: Optional[str] = None) -> MCPConfig:
    """从 JSON 文件加载 MCP 配置。"""
    path = path or _resolve_config_path()
    if not path or not Path(path).exists():
        return MCPConfig()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return MCPConfig(servers=_parse_mcp_data(data))
    except Exception as exc:
        log(f"加载 MCP 配置失败: {exc}", "ERROR")
        return MCPConfig()


# ------------------------------------------------------------------
# mcp_servers.json 配置存取与校验（纯 MCP 域）
# ------------------------------------------------------------------

# 配置文件读-改-写串行化锁：Web API、AI 工具、热重载等多条写路径共用，
# 防止并发写互相覆盖丢更新。
_CONFIG_LOCK = threading.RLock()


def _writable_config_path() -> Path:
    """定位配置文件读写路径（env 优先，ConfigManager 持久化值次之，ConfigPaths 兜底）。

    与原 agent.config BotConfigProvider 的解析口径一致：
    ANELF_MCP_CONFIG > ANELF_MCP_CONFIG_PATH > ConfigManager["mcp_config_path"]
    > ConfigPaths.MCP_SERVERS（provider 先读 CM 再应用 env 覆盖，env 胜出）。
    """
    env = os.getenv("ANELF_MCP_CONFIG", "").strip()
    if env:
        return Path(env)
    env_path = os.getenv("ANELF_MCP_CONFIG_PATH", "").strip()
    if env_path:
        return Path(env_path)
    try:
        from core.config import ConfigManager
        val = ConfigManager.get("mcp_config_path")
        if val:
            return Path(str(val))
    except Exception:
        log("mcp_config_path 配置读取失败，使用默认路径", "DEBUG")
    return Path(ConfigPaths.MCP_SERVERS)


class MCPServerStore:
    """mcp_servers.json 的配置读写、字段校验与脱敏（纯 MCP 域）。

    本类不依赖 bridge：热重载经 ``on_reload`` 钩子外置，由持有 bridge 的
    上层（services 门面 / manage_tools）注入实现，保持本模块为叶子。
    """

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
        "stay_awake",
    })
    _SERVER_ALLOWED_TRANSPORTS = frozenset({"stdio", "streamable_http", "sse"})
    _SECRET_MASK = "********"
    _SECRET_FIELDS = ("env", "headers")

    def __init__(self, on_reload: Optional[Callable[[], None]] = None) -> None:
        self._on_reload = on_reload

    def _trigger_reload(self) -> None:
        """触发热重载钩子（未注入时为 no-op）。"""
        if self._on_reload is not None:
            self._on_reload()

    @classmethod
    def mask_secrets(cls, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """返回脱敏副本：env/headers 的值统一替换为占位符（供展示与表单回显）。"""
        masked = dict(cfg)
        for secret_field in cls._SECRET_FIELDS:
            val = masked.get(secret_field)
            if isinstance(val, dict):
                masked[secret_field] = {
                    k: (cls._SECRET_MASK if v else v) for k, v in val.items()
                }
        return masked

    @classmethod
    def _restore_masked_secrets(
        cls, patch: Dict[str, Any], existing: Dict[str, Any]
    ) -> None:
        """将 patch 中仍为占位符的 env/headers 值还原为现有真实值（原地修改）。"""
        for secret_field in cls._SECRET_FIELDS:
            new_vals = patch.get(secret_field)
            old_vals = existing.get(secret_field)
            if isinstance(new_vals, dict) and isinstance(old_vals, dict):
                for k, v in new_vals.items():
                    if v == cls._SECRET_MASK and k in old_vals:
                        new_vals[k] = old_vals[k]

    # ------------------------------------------------------------------
    # 配置读写
    # ------------------------------------------------------------------

    def load_config(self) -> Dict[str, Any]:
        """加载 MCP 服务器配置（原始 JSON dict）。"""
        p = _writable_config_path()
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except Exception:
                log(f"读取 MCP 配置失败: {p}", "ERROR")
        return {"mcpServers": {}}

    def save_config(self, data: Dict[str, Any]) -> None:
        p = _writable_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

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
        """兼容字符串/数字的布尔值解析。"""
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
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
            if key in {"enabled", "stay_awake"}:
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

    def get_server_config(self, name: str, *, mask_secrets: bool = False) -> Optional[Dict[str, Any]]:
        """返回单个 server 的配置（可选脱敏 env/headers）。"""
        data = self.load_config()
        raw = data.get("mcpServers", {}).get(name)
        if isinstance(raw, dict):
            cfg = dict(raw)
            return self.mask_secrets(cfg) if mask_secrets else cfg
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

            for remove_field in (remove_fields or []):
                f = str(remove_field).strip()
                if f:
                    current.pop(f, None)

            normalized_patch = self._normalize_server_patch(patch)
            self._restore_masked_secrets(normalized_patch, before)
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

    @staticmethod
    def _infer_transport(cfg: Dict[str, Any]) -> str:
        transport = str(cfg.get("transport", "") or "").strip()
        if transport:
            return transport
        return "stdio" if cfg.get("command") else "streamable_http"

    # ------------------------------------------------------------------
    # 增删
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

    def _set_enabled(self, name: str, enabled: bool) -> None:
        """更新配置文件中指定 server 的 enabled 字段。"""
        with _CONFIG_LOCK:
            data = self.load_config()
            servers = data.get("mcpServers", {})
            if name in servers and isinstance(servers[name], dict):
                servers[name]["enabled"] = enabled
                self.save_config(data)
