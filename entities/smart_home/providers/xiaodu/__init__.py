"""小度（DuerOS）连接 — MCP 传输型 provider（百度官方双 MCP 为唯一通道）。

设备模型映射：``xiaodu-iot`` 的 GET_ALL_DEVICES_WITH_STATUS → 家电实体
（applianceTypes → 设备域，未列类型不进设备面）；``xiaodu`` 的 list_user_devices
→ 音箱实体（media_player，cuid/client_id 为控制前置）。控制经
IOT_CONTROL_DEVICES（名称语义）与 control_xiaodu / xiaodu_speak（语音指令语义）。

连接生命周期完全归 MCP 桥（配置/重连/状态/令牌），本层只做轮询同步、设备模型
映射与动作翻译——桥未连接时待机，不自行建连。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Dict, List, Optional, Tuple

from core.log import log
from entities._sdk import call_mcp_server_tool

from ...models import DeviceState
from .. import home_provider
from ..base import SmartHomeProvider

_LOG_TAG = "智能家居"

IOT_SERVER = "xiaodu-iot"
TERMINAL_SERVER = "xiaodu"

# 小度 IoT 家电类型 → 设备域组件 key（未列类型不进统一设备面，仍可经裸 MCP 工具控制）
_TYPE_DOMAIN: Dict[str, str] = {
    "AIR_CONDITION": "climate",
    "LIGHT": "light",
    "SWITCH": "switch",
}

# hvac 模式 → 小度中文模式值（IOT attribute=mode）
_HVAC_MODES: Dict[str, str] = {
    "cool": "制冷",
    "heat": "制热",
    "auto": "自动",
    "dry": "除湿",
    "fan_only": "送风",
}

# 音箱 media_player 动作 → 语音指令（control_xiaodu 自然语言语义）
_SPEAKER_COMMANDS: Dict[str, str] = {
    "media_play": "播放",
    "media_pause": "暂停",
    "turn_on": "开机",
    "turn_off": "关机",
}


def bridge_server_status() -> List[Dict[str, Any]]:
    """MCP 桥全部 server 状态（桥未初始化时空列表；独立函数便于测试替换）。"""
    try:
        from entities.mcp.bridge import get_mcp_bridge

        bridge = get_mcp_bridge()
        return list(bridge.list_available_servers()) if bridge else []
    except Exception:
        return []


async def _call(server: str, tool: str, arguments: Dict[str, Any]) -> Any:
    """调 MCP 工具并解析 JSON 结果（tool_error / 非 JSON 抛 RuntimeError）。"""
    raw = await call_mcp_server_tool(server, tool, arguments)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"{server}/{tool} 返回非 JSON: {raw[:120]}") from exc
    if isinstance(data, dict) and (data.get("error") or data.get("success") is False):
        raise RuntimeError(str(data.get("error") or data))
    return data


@home_provider
class XiaoduProvider(SmartHomeProvider):
    """小度平台接入（MCP 传输：设备同步轮询，控制翻译为 IoT/语音指令）。"""

    key = "xiaodu"
    display_name = "小度音箱"
    entity_prefix = "xiaodu."
    config_schema = {
        "app_key": {
            "description": "百度应用 AppKey（client_id，DuerOS MCP 控制台创建）",
            "default": "",
        },
        "secret_key": {
            "description": "百度应用 SecretKey（client_secret）",
            "default": "",
            "value_type": "password",
        },
        "refresh_token": {
            "description": "OAuth refresh_token（一次性；续期成功后自动更新）",
            "default": "",
            "value_type": "password",
        },
        "token_expires_at": {
            "description": "ACCESS_TOKEN 到期日（YYYY-MM-DD，续期时自动更新，供巡检提前续期）",
            "default": "",
        },
        "poll_interval": {
            "description": "设备状态轮询间隔（秒）",
            "default": 30,
            "value_type": "integer",
            "min": 5,
            "max": 600,
            "advanced": True,
        },
    }

    def __init__(self) -> None:
        super().__init__()
        self._devices: Dict[str, DeviceState] = {}
        self._iot_meta: Dict[str, Dict[str, str]] = {}
        self._speaker_meta: Dict[str, Dict[str, str]] = {}
        self._task: Optional[asyncio.Task[None]] = None
        self._connected = False
        self._last_error = ""

    # ---- 状态 ----

    def is_configured(self) -> bool:
        """两个小度 MCP server 均在配置中且启用（连接归桥管）。"""
        enabled = {
            str(s.get("name")): bool(s.get("enabled", True))
            for s in bridge_server_status()
        }
        return enabled.get(IOT_SERVER, False) and enabled.get(TERMINAL_SERVER, False)

    def is_connected(self) -> bool:
        return self._connected

    def snapshot(self) -> List[DeviceState]:
        return list(self._devices.values())

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.key,
            "provider_name": self.display_name,
            "configured": self.is_configured(),
            "connected": self._connected,
            "device_count": len(self._devices),
            "last_error": self._last_error or None,
            "connected_at": None,
        }

    # ---- 生命周期（轮询同步；连接态跟随 MCP 桥） ----

    async def connect(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._poll_loop())

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._connected = False
        self._emit_connection()

    def _poll_interval(self) -> float:
        return float(self.get_config("poll_interval", 30))

    async def _poll_loop(self) -> None:
        """轮询循环：桥连接态 → 设备同步 → 变更回调（异常不中断循环）。"""
        while True:
            try:
                connected = self._bridge_connected()
                if connected != self._connected:
                    self._connected = connected
                    if connected:
                        self._last_error = ""
                    self._emit_connection()
                if connected and await self._sync_devices() and self.on_sync:
                    self.on_sync()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}".rstrip(": ")
                log(f"小度同步异常: {self._last_error}", "WARNING", tag=_LOG_TAG)
            await asyncio.sleep(self._poll_interval())

    def _bridge_connected(self) -> bool:
        connected = {
            str(s.get("name")): bool(s.get("connected"))
            for s in bridge_server_status()
        }
        return connected.get(IOT_SERVER, False) and connected.get(
            TERMINAL_SERVER, False,
        )

    async def _sync_devices(self) -> bool:
        """全量拉双 MCP 设备并重建缓存（返回是否有变更）。"""
        devices: Dict[str, DeviceState] = {}
        iot_meta: Dict[str, Dict[str, str]] = {}
        speaker_meta: Dict[str, Dict[str, str]] = {}
        for device, meta in await self._sync_iot():
            devices[device.entity_id] = device
            iot_meta[device.entity_id] = meta
        for device, meta in await self._sync_terminal():
            devices[device.entity_id] = device
            speaker_meta[device.entity_id] = meta
        changed = {
            eid: d.to_dict() for eid, d in devices.items()
        } != {eid: d.to_dict() for eid, d in self._devices.items()}
        self._devices, self._iot_meta, self._speaker_meta = (
            devices, iot_meta, speaker_meta,
        )
        return changed

    async def _sync_iot(self) -> List[Tuple[DeviceState, Dict[str, str]]]:
        data = await _call(IOT_SERVER, "GET_ALL_DEVICES_WITH_STATUS", {})
        result: List[Tuple[DeviceState, Dict[str, str]]] = []
        for appliance in data.get("return") or []:
            types = appliance.get("applianceTypes") or []
            domain = _TYPE_DOMAIN.get(str(types[0]) if types else "")
            name = str(appliance.get("applianceName") or "").strip()
            if domain is None or not name:
                continue
            room = str(appliance.get("roomName") or "").strip()
            status = appliance.get("status") or {}
            reachable = str(status.get("connectivity") or "") == "REACHABLE"
            on = str(status.get("turnOnState") or "").upper() == "ON"
            attrs: Dict[str, Any] = {}
            if isinstance(status.get("targetTemperature"), (int, float)):
                attrs["temperature"] = status["targetTemperature"]
            result.append((DeviceState(
                entity_id=f"xiaodu.{name}",
                domain=domain,
                name=name,
                state=("on" if on else "off") if reachable else "unavailable",
                attributes=attrs,
                area=room,
            ), {"name": name, "room": room}))
        return result

    async def _sync_terminal(self) -> List[Tuple[DeviceState, Dict[str, str]]]:
        data = await _call(TERMINAL_SERVER, "list_user_devices", {})
        entries = data if isinstance(data, list) else ([data] if data else [])
        result: List[Tuple[DeviceState, Dict[str, str]]] = []
        for entry in entries:
            cuid = str(entry.get("cuid") or "")
            client_id = str(entry.get("client_id") or "")
            if not cuid or not client_id:
                continue
            online = bool(entry.get("online_status"))
            location = entry.get("location") or {}
            meta = {"cuid": cuid, "client_id": client_id}
            result.append((DeviceState(
                entity_id=f"xiaodu.{cuid}",
                domain="media_player",
                name=str(entry.get("device_name") or cuid),
                state="idle" if online else "unavailable",
                attributes=dict(meta),
                area=str(location.get("room") or ""),
            ), meta))
        return result

    # ---- 控制 ----

    async def call_service(
        self, domain: str, service: str, entity_id: str, data: Dict[str, Any],
    ) -> None:
        if not self._connected:
            raise ConnectionError("小度 MCP 未连接")
        if entity_id in self._speaker_meta:
            await self._call_speaker(service, entity_id, data)
        elif entity_id in self._iot_meta:
            await self._call_iot(service, entity_id, data)
        else:
            raise RuntimeError(f"未知小度设备: {entity_id}")

    async def _call_iot(
        self, service: str, entity_id: str, data: Dict[str, Any],
    ) -> None:
        meta = self._iot_meta[entity_id]
        base: Dict[str, Any] = {"applianceName": meta["name"]}
        if meta.get("room"):
            base["roomName"] = meta["room"]

        async def control(**extra: Any) -> None:
            await _call(IOT_SERVER, "IOT_CONTROL_DEVICES", {**base, **extra})

        if service in ("turn_on", "turn_off"):
            await control(action="turnOn" if service == "turn_on" else "turnOff")
        elif service == "set_brightness":
            await control(
                action="set", attribute="brightness",
                value=str(data.get("brightness_pct")),
            )
        elif service == "set_temperature":
            await control(
                action="set", attribute="temperature",
                value=str(data.get("temperature")),
            )
        elif service == "set_hvac_mode":
            mode = str(data.get("hvac_mode") or "")
            if mode == "off":
                await control(action="turnOff")
            elif mode in _HVAC_MODES:
                await control(action="set", attribute="mode", value=_HVAC_MODES[mode])
            else:
                raise RuntimeError(f"小度不支持的空调模式: {mode}")
        else:
            raise RuntimeError(f"小度 IoT 不支持动作: {service}")

    async def _call_speaker(
        self, service: str, entity_id: str, data: Dict[str, Any],
    ) -> None:
        meta = self._speaker_meta[entity_id]
        if service == "speak":
            await self.speak(self._devices[entity_id], str(data.get("message", "")))
            return
        if service == "set_volume":
            level = float(data.get("volume_level", 0))
            command = f"音量调到{round(level * 100)}"
        elif service in _SPEAKER_COMMANDS:
            command = _SPEAKER_COMMANDS[service]
        else:
            raise RuntimeError(f"小度音箱不支持动作: {service}")
        await _call(TERMINAL_SERVER, "control_xiaodu", {"command": command, **meta})

    # ---- 原生播报 ----

    def supports_speak(self, device: DeviceState) -> bool:
        return device.entity_id in self._speaker_meta

    async def speak(self, device: DeviceState, text: str) -> None:
        meta = self._speaker_meta.get(device.entity_id)
        if not meta:
            raise RuntimeError(f"非小度音箱设备: {device.entity_id}")
        await _call(TERMINAL_SERVER, "xiaodu_speak", {"text": text, **meta})

    def _emit_connection(self) -> None:
        if self.on_connection is not None:
            with contextlib.suppress(Exception):
                self.on_connection(self.status())
