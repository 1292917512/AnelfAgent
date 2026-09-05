"""频道管理服务 -- 列表、启停、接口开关与测试。

频道配置的读写已全部收口到统一配置系统（core.config）：
schema 注册见 agent/channel/config.py，Web 经 /api/config/meta、AI 经 entity 组工具，
本服务不再维护独立的 channel_config.json 读写路径。
"""

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

        from agent.channel.manager import list_configured_channels
        for channel_name, enabled in list_configured_channels().items():
            if channel_name not in seen_keys and channel_name not in ("cli",):
                result.append({
                    "key": channel_name,
                    "name": channel_name,
                    "status": "stopped",
                    "status_display": "⚪ 未启用" if not enabled else "⚪ 已停止",
                })
        return result

    async def toggle_adapter(self, key: str) -> None:
        """启动或停止指定频道（挂起直到完成或超时）。

        启停意图持久化到统一配置的 <channel_id>_enabled 键（重启后保持）；
        未注册的频道（启动期 enabled=false 被跳过）由
        ChannelManager.activate_channel 动态实例化、注册并启动。
        """
        from agent.channel import get_channel_manager
        from agent.channel.manager import set_channel_enabled
        mgr = get_channel_manager()
        channel = mgr.get(key)

        if channel and channel.status.value == "running":
            await self._with_timeout(mgr.stop_channel(key), 10, f"停止频道超时: {key}")
            set_channel_enabled(key, False)
            return

        set_channel_enabled(key, True)
        if channel:
            await self._with_timeout(mgr.start_channel(key), 15, f"启动频道超时: {key}")
            return

        # 频道未注册：动态实例化、注册、启动
        await self._with_timeout(mgr.activate_channel(key), 20, f"激活频道超时: {key}")

    @staticmethod
    async def _with_timeout(coro: Any, timeout: float, message: str) -> Any:
        """await 协程并施加超时，超时时抛出带明确原因的错误。"""
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(message) from None

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
    def get_channel_webui_url(channel_id: str) -> Optional[str]:
        """解析频道配置的内嵌 WebUI 地址（统一配置实际值优先，回退 schema 默认值）。

        匹配 napcat_webui_url / webui_url / dashboard_url 配置项，
        供频道 WebUI 同源代理确定转发目标。
        """
        from agent.channel.config import config_key
        from core.config import ConfigManager, ConfigRegistry

        for suffix in ("napcat_webui_url", "webui_url", "dashboard_url"):
            key = config_key(channel_id, suffix)
            value = ConfigManager.get(key)
            if not value:
                item = ConfigRegistry.get_item(key)
                value = item.default_value if item else None
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
