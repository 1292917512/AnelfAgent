"""频道管理服务 -- 列表、启停、配置管理。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from services._runtime import is_ready


class AdapterService:
    """频道管理服务"""

    def is_ready(self) -> bool:
        return is_ready()

    def list_adapters(self) -> Optional[List[Dict[str, Any]]]:
        """返回频道列表含状态（包括已配置但未启用的频道）。"""
        if not is_ready():
            return None
        from agent.channel import get_channel_manager
        mgr = get_channel_manager()
        channels = mgr.list_channels()
        status_map = {
            "running": "🟢 运行中",
            "stopped": "⚪ 已停止",
            "starting": "🟡 启动中",
            "reconnecting": "🟡 重连中",
            "error": "🔴 错误",
        }
        result: List[Dict[str, Any]] = []
        seen_keys: set = set()
        for key, channel in channels.items():
            info = channel.get_status_info()
            status = info.get("status", "unknown")
            item: Dict[str, Any] = {
                "key": key,
                "name": info.get("name", key),
                "status": status,
                "status_display": status_map.get(status, status),
            }
            if "detail" in info:
                item["detail"] = info["detail"]
            if "ws_mode" in info:
                item["ws_mode"] = info["ws_mode"]
            if "ws_connected" in info:
                item["ws_connected"] = info["ws_connected"]
            if "online" in info:
                item["online"] = info["online"]
            if "self_id" in info:
                item["self_id"] = info["self_id"]
            item["capabilities"] = info.get("capabilities", [])
            result.append(item)
            seen_keys.add(key)

        all_configs = self._scan_channel_configs()
        for channel_name in all_configs:
            if channel_name not in seen_keys and channel_name not in ("cli",):
                cfg = all_configs[channel_name]
                result.append({
                    "key": channel_name,
                    "name": channel_name,
                    "status": "stopped",
                    "status_display": "⚪ 未启用" if not cfg.get("enabled") else "⚪ 已停止",
                })
        return result

    def toggle_adapter(self, key: str, loop: asyncio.AbstractEventLoop) -> None:
        """启动或停止指定频道（同步阻塞直到完成）。

        对于未注册的频道（config 中 enabled=false），先动态实例化并注册，
        再启动。这样前端点"激活"时可以启用一个之前未加载的频道。
        """
        from agent.channel import get_channel_manager
        mgr = get_channel_manager()
        channel = mgr.get(key)

        if channel and channel.status.value == "running":
            asyncio.run_coroutine_threadsafe(
                mgr.stop_channel(key), loop,
            ).result(timeout=10)
            self._set_channel_enabled(key, False)
            return

        if channel:
            asyncio.run_coroutine_threadsafe(
                mgr.start_channel(key), loop,
            ).result(timeout=15)
            return

        # 频道未注册：动态实例化、注册、启动
        asyncio.run_coroutine_threadsafe(
            self._activate_unregistered_channel(key, mgr), loop,
        ).result(timeout=20)

    @staticmethod
    async def _activate_unregistered_channel(key: str, mgr: Any) -> None:
        """动态加载并启动一个未注册的频道。"""
        import importlib
        import json
        from pathlib import Path
        from core.log import log

        channel_dir = Path("channels") / key
        if not channel_dir.is_dir():
            log(f"频道目录不存在: {channel_dir}", "WARNING")
            return

        # 更新 channel_config.json 设为 enabled
        cfg_file = channel_dir / "channel_config.json"
        cfg: dict = {}
        if cfg_file.exists():
            try:
                cfg = json.loads(cfg_file.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        cfg["enabled"] = True
        cfg_file.write_bytes(json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8"))

        # 动态导入频道模块
        module_path = f"channels.{key}.adapter"
        try:
            mod = importlib.import_module(module_path)
        except Exception as exc:
            log(f"频道模块加载失败: {key} - {exc}", "ERROR")
            return

        from agent.channel.base import BaseChannel
        channel_cls = getattr(mod, "CHANNEL_CLASS", None)
        if channel_cls is None:
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, type) and issubclass(attr, BaseChannel) and attr is not BaseChannel:
                    channel_cls = attr
                    break
        if channel_cls is None:
            log(f"频道类未找到: {key}", "ERROR")
            return

        try:
            instance = channel_cls()
            instance.load_channel_config(str(channel_dir))
            mgr.register(instance)
            await mgr.start_channel(key)
            log(f"频道动态启用: {key}")
        except Exception as exc:
            log(f"频道动态启用失败: {key} - {exc}", "ERROR")

    @staticmethod
    async def test_channel_health(key: str) -> Dict[str, Any]:
        """频道连接健康检查：健康探针 + Bot 身份 + 能力列表。

        Args:
            key: 频道标识。

        Returns:
            包含 running/healthy/latency_ms/self_info/capabilities 的结果字典，
            频道未注册或未运行时返回带 error 的结构。
        """
        if not is_ready():
            return {"ready": False, "error": "runtime not ready"}
        from agent.channel import get_channel_manager
        channel = get_channel_manager().get(key)
        if channel is None:
            return {"ready": True, "running": False, "error": "channel not registered"}

        info = channel.get_status_info()
        result: Dict[str, Any] = {
            "ready": True,
            "running": info.get("status") == "running",
            "status": info.get("status", "unknown"),
            "detail": info.get("detail", ""),
            "capabilities": info.get("capabilities", []),
        }
        if not result["running"]:
            result["error"] = "channel not running"
            return result

        health = await channel.check_health()
        result["healthy"] = health.healthy
        result["health_detail"] = health.detail
        result["latency_ms"] = health.latency_ms
        result["last_error"] = health.last_error

        try:
            self_user = await channel.get_self_info()
            if self_user is not None:
                result["self_info"] = {
                    "user_id": self_user.user_id,
                    "user_name": self_user.user_name,
                    "platform": self_user.platform,
                }
        except Exception:
            pass  # Bot 身份获取失败不影响健康检查主结果
        return result

    @staticmethod
    async def test_channel_send(key: str, chat_id: str, text: str) -> Dict[str, Any]:
        """向指定会话发送频道测试消息。

        Args:
            key: 频道标识。
            chat_id: 目标会话 ID。
            text: 测试文本内容。

        Returns:
            包含 success/message_id 或 error 的结果字典。
        """
        import json

        if not is_ready():
            return {"ready": False, "success": False, "error": "runtime not ready"}
        from agent.channel import get_channel_manager
        channel = get_channel_manager().get(key)
        if channel is None:
            return {"ready": True, "success": False, "error": "channel not registered"}
        if channel.status.value != "running":
            return {"ready": True, "success": False, "error": "channel not running"}

        try:
            raw = await asyncio.wait_for(channel.send_text(chat_id, text), timeout=15)
            data = json.loads(raw)
            return {"ready": True, **data}
        except asyncio.TimeoutError:
            return {"ready": True, "success": False, "error": "send timeout after 15s"}
        except (json.JSONDecodeError, TypeError) as exc:
            return {"ready": True, "success": False, "error": f"invalid channel response: {exc}"}
        except Exception as exc:
            return {"ready": True, "success": False, "error": str(exc)}

    @staticmethod
    def _set_channel_enabled(key: str, enabled: bool) -> None:
        """Update enabled flag in channel_config.json."""
        import json
        from pathlib import Path
        cfg_file = Path("channels") / key / "channel_config.json"
        if not cfg_file.exists():
            return
        try:
            cfg = json.loads(cfg_file.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            cfg = {}
        cfg["enabled"] = enabled
        cfg_file.write_bytes(json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8"))

    @staticmethod
    def get_channel_webui_url(channel_id: str) -> Optional[str]:
        """解析频道配置的内嵌 WebUI 地址（实际值优先，回退元数据默认值）。

        匹配 napcat_webui_url / webui_url / dashboard_url 配置项，
        供频道 WebUI 同源代理确定转发目标。
        """
        import json
        from pathlib import Path

        cfg: Dict[str, Any] = {}
        cfg_file = Path("channels") / channel_id / "channel_config.json"
        if cfg_file.exists():
            try:
                cfg = json.loads(cfg_file.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                cfg = {}
        meta = AdapterService._load_all_config_meta().get(channel_id, {})
        for suffix in ("napcat_webui_url", "webui_url", "dashboard_url"):
            value = cfg.get(suffix) or meta.get(suffix, {}).get("default")
            if value:
                return str(value)
        return None

    # ------------------------------------------------------------------
    # 频道接口（channel_tool）开关与测试
    # ------------------------------------------------------------------

    @staticmethod
    def get_channel_tools(key: str) -> Dict[str, Any]:
        """返回指定频道的接口列表（专属工具 + 其参与的公共能力工具）。"""
        if not is_ready():
            return {"ready": False, "tools": []}
        from agent.channel import get_channel_manager
        from agent.channel.tool_bridge import get_channel_tool_info
        channel = get_channel_manager().get(key)
        if channel is None:
            return {"ready": True, "running": False, "tools": []}
        return {"ready": True, "running": True, "tools": get_channel_tool_info(key)}

    @staticmethod
    def toggle_channel_tool(key: str, tool_name: str) -> Dict[str, Any]:
        """翻转指定频道某接口的开关状态并持久化。

        专属工具同步翻转实体 enabled；公共能力工具仅按频道持久化，
        由 PFC schema 过滤与 handler 守卫生效。
        """
        from agent.channel.tool_bridge import get_channel_tool_info, set_channel_tool_state
        from core.entity import EntityRegistry

        tools = {t["name"]: t for t in get_channel_tool_info(key)}
        info = tools.get(tool_name)
        if info is None:
            raise KeyError(f"接口不存在: {key}/{tool_name}")

        new_state = not info["enabled"]
        set_channel_tool_state(key, tool_name, new_state)
        if not info["common"]:
            # 实体 enabled = 全局状态 AND 按频道状态，保持与注册回读逻辑一致
            if new_state and info["globally_enabled"]:
                EntityRegistry.enable(tool_name)
            else:
                EntityRegistry.disable(tool_name)
        return {"name": tool_name, "enabled": new_state, "common": info["common"]}

    @staticmethod
    async def test_channel_tool(key: str, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """以管理员身份直接调用频道接口（不受 AI 侧开关限制）。

        Args:
            key: 频道标识。
            tool_name: 接口名（专属工具或公共能力名）。
            args: 调用参数（公共能力自动注入 channel_id）。

        Returns:
            包含 success/result/latency_ms 或 error 的结果字典。
        """
        import inspect
        import json
        import time

        if not is_ready():
            return {"ready": False, "success": False, "error": "runtime not ready"}
        from agent.channel import get_channel_manager
        from agent.channel.tool_bridge import get_channel_tool_info
        from core.entity import EntityRegistry

        if get_channel_manager().get(key) is None:
            return {"ready": True, "success": False, "error": "channel not registered"}

        tools = {t["name"]: t for t in get_channel_tool_info(key)}
        info = tools.get(tool_name)
        if info is None:
            return {"ready": True, "success": False, "error": f"tool not found: {tool_name}"}

        entity = EntityRegistry.get(tool_name)
        if entity is None or entity.func is None:
            return {"ready": True, "success": False, "error": "tool not registered"}

        call_args = dict(args)
        if info["common"]:
            call_args["channel_id"] = key

        started = time.perf_counter()
        try:
            raw = entity.func(**call_args)
            if inspect.isawaitable(raw):
                raw = await asyncio.wait_for(raw, timeout=30)
            result = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            try:
                parsed = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            success = bool(parsed.get("success", True)) if isinstance(parsed, dict) else True
            return {"ready": True, "success": success, "result": result, "latency_ms": latency_ms}
        except asyncio.TimeoutError:
            return {"ready": True, "success": False, "error": "tool call timeout after 30s"}
        except Exception as exc:
            return {"ready": True, "success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # 适配器配置（从各频道的 channel_config.json 读写）
    # ------------------------------------------------------------------

    @staticmethod
    def _scan_channel_configs() -> Dict[str, Dict[str, Any]]:
        """扫描所有频道目录的 channel_config.json。"""
        import json
        from pathlib import Path
        result: Dict[str, Dict[str, Any]] = {}
        channels_dir = Path("channels")
        if not channels_dir.is_dir():
            return result
        for item in sorted(channels_dir.iterdir()):
            if not item.is_dir() or item.name.startswith("_"):
                continue
            cfg_file = item / "channel_config.json"
            if cfg_file.exists():
                try:
                    result[item.name] = json.loads(cfg_file.read_text("utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
        return result

    def get_adapter_configs(self) -> Dict[str, Dict[str, Any]]:
        """收集所有频道的配置项。

        从 channel_config.json 读取实际值，
        从频道 config 模块的定义中获取元数据（description、value_type、enum_options、tag）。
        """
        all_configs = self._scan_channel_configs()
        meta_cache = self._load_all_config_meta()
        result: Dict[str, Dict[str, Any]] = {}

        for channel_name, cfg in all_configs.items():
            group = f"adapter/{channel_name}"
            channel_meta = meta_cache.get(channel_name, {})

            for key, value in cfg.items():
                full_key = f"{group}.{key}"
                meta = channel_meta.get(key)

                if meta:
                    vtype = meta.get("value_type", "auto")
                    if hasattr(vtype, "value"):
                        vtype = vtype.value
                    description = meta.get("description", key)
                    enum_options = meta.get("options")
                    tag = meta.get("tag", "")
                    default = meta.get("default", value)
                else:
                    vtype = "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "string"
                    if isinstance(value, str) and ("token" in key or "secret" in key):
                        vtype = "password"
                    description = key
                    enum_options = None
                    tag = ""
                    default = value

                result[full_key] = {
                    "description": description,
                    "default": default,
                    "value": value,
                    "group": group,
                    "value_type_str": str(vtype),
                    "enum_options": enum_options,
                    "tag": tag,
                }
        return result

    @staticmethod
    def _load_all_config_meta() -> Dict[str, Dict[str, Dict[str, Any]]]:
        """加载所有频道的配置元数据定义。

        返回 {channel_name: {config_key: {description, value_type, options, tag, ...}}}
        """
        import importlib
        from pathlib import Path

        result: Dict[str, Dict[str, Dict[str, Any]]] = {}
        channels_dir = Path("channels")
        if not channels_dir.is_dir():
            return result

        for item in sorted(channels_dir.iterdir()):
            if not item.is_dir() or item.name.startswith("_"):
                continue
            config_file = item / "config.py"
            if not config_file.exists():
                continue

            try:
                mod = importlib.import_module(f"channels.{item.name}.config")
            except Exception:
                continue

            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if not isinstance(attr, dict):
                    continue
                for group_key, fields in attr.items():
                    if not isinstance(fields, dict) or not group_key.startswith("adapter/"):
                        continue
                    channel_name = group_key.replace("adapter/", "")
                    result[channel_name] = fields

        return result

    def save_adapter_configs(self, values: Dict[str, Any]) -> int:
        """保存适配器配置值到各频道的 channel_config.json，并热重载运行中的频道配置。"""
        import json
        from pathlib import Path

        updates: Dict[str, Dict[str, Any]] = {}
        for full_key, val in values.items():
            parts = full_key.split(".", 1)
            if len(parts) != 2:
                continue
            group, key = parts
            channel_name = group.replace("adapter/", "")
            if channel_name not in updates:
                updates[channel_name] = {}
            updates[channel_name][key] = val

        changed = 0
        affected_channels: list[str] = []
        for channel_name, new_values in updates.items():
            cfg_file = Path("channels") / channel_name / "channel_config.json"
            if not cfg_file.exists():
                continue
            try:
                existing = json.loads(cfg_file.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {}

            for k, v in new_values.items():
                if existing.get(k) != v:
                    existing[k] = v
                    changed += 1

            with open(str(cfg_file), "wb") as f:
                f.write(json.dumps(existing, indent=2, ensure_ascii=False).encode("utf-8"))

            if changed:
                affected_channels.append(channel_name)

        self._reload_affected_channels(affected_channels)
        return changed

    @staticmethod
    def _reload_affected_channels(channel_names: list[str]) -> None:
        """通知运行中的频道重新加载配置。"""
        if not channel_names:
            return
        try:
            from agent.channel import get_channel_manager
            from core.log import log
            mgr = get_channel_manager()
            channels = mgr.list_channels()
            for name in channel_names:
                ch = channels.get(name)
                if ch and hasattr(ch, 'reload_config'):
                    ch.reload_config()
                    log(f"频道配置已热重载: {name}", "DEBUG")
        except Exception:
            pass
