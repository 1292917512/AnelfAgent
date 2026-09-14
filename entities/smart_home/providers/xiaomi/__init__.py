"""小爱音箱平台接入 — 小米云 MiNA 通道（miservice）。

小爱音箱映射为 ``xiaomi.<deviceID>`` 的 media_player 设备：状态/音量经
周期轮询同步，播放控制映射 MiNA ubus，语音播报走小米云原生 TTS
（supports_speak，不经 HA tts_service）。登录凭据为小米账号密码
（password 脱敏），登录态 token 缓存于组件目录 .mi.token。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from core.log import log

from ...models import DeviceState
from .. import home_provider
from ..base import SmartHomeProvider
from .client import XiaomiClient

_LOG_TAG = "智能家居"

_POLL_INTERVAL_SECONDS = 30.0

# 域动作 → MiNA 控制（media_player 域服务名 → XiaomiClient 方法名）
_SERVICE_METHODS = {
    "media_play": "play",
    "media_pause": "pause",
    "media_stop": "stop",
    "turn_off": "stop",
}


@home_provider
class XiaomiProvider(SmartHomeProvider):
    """小米生态接入（小爱音箱：状态同步 / 播放控制 / 云端 TTS 播报）。"""

    key = "xiaomi"
    display_name = "小米（小爱音箱）"
    entity_prefix = "xiaomi."
    config_schema = {
        "username": {
            "description": "小米账号（手机号/邮箱；登录态 token 缓存复用，401 自动重登）",
            "default": "",
        },
        "password": {
            "description": "小米账号密码（支持 ${ENV_VAR} 引用；登录触发二步验证时暂不支持交互，会如实报错）",
            "default": "",
            "value_type": "password",
        },
    }

    def __init__(self) -> None:
        super().__init__()
        self._client = XiaomiClient()
        self._devices: Dict[str, DeviceState] = {}
        self._device_ids: Dict[str, str] = {}  # entity_id → 小米 deviceID
        self._connected = False
        self._last_error = ""
        self._connected_at = 0.0
        self._poll_task: Optional[asyncio.Task[None]] = None

    # ---- 状态 ----

    def is_configured(self) -> bool:
        return bool(
            str(self.get_config("username", "")).strip()
            and str(self.get_config("password", "")).strip()
        )

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
            "connected_at": self._connected_at or None,
            "discovery": self.discovery_supported,
        }

    # ---- 生命周期 ----

    async def connect(self) -> None:
        """登录小米云并同步音箱清单（幂等；失败仅记录错误，状态经 status 可见）。"""
        if self._connected:
            return
        try:
            await self._client.open(
                str(self.get_config("username", "")).strip(),
                str(self.get_config("password", "")).strip(),
            )
            await self._sync_devices()
            self._connected = True
            self._connected_at = time.time()
            self._last_error = ""
        except Exception as exc:
            self._connected = False
            self._last_error = str(exc)
            log(f"小米云登录失败: {exc}", "WARNING", tag=_LOG_TAG)
            await self._client.close()
        self._emit_connection()
        if self._connected and (self._poll_task is None or self._poll_task.done()):
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def close(self) -> None:
        """断开连接并停止轮询（幂等；保留末次设备缓存供展示）。"""
        task, self._poll_task = self._poll_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._client.close()
        if self._connected:
            self._connected = False
            self._emit_connection()

    # ---- 设备同步 ----

    async def _sync_devices(self) -> None:
        """拉取音箱清单并重建设备缓存。"""
        speakers = await self._client.speakers()
        devices: Dict[str, DeviceState] = {}
        device_ids: Dict[str, str] = {}
        for speaker in speakers:
            device_id = str(speaker.get("deviceID") or "")
            if not device_id:
                continue
            entity_id = f"{self.entity_prefix}{device_id}"
            name = str(speaker.get("name") or speaker.get("alias") or entity_id)
            previous = self._devices.get(entity_id)
            devices[entity_id] = DeviceState(
                entity_id=entity_id,
                domain="media_player",
                name=name,
                state=previous.state if previous else "idle",
                attributes=previous.attributes if previous else {},
            )
            device_ids[entity_id] = device_id
        self._devices = devices
        self._device_ids = device_ids
        if self.on_sync is not None:
            self.on_sync()

    async def _poll_loop(self) -> None:
        """周期轮询音箱播放状态（MiNA 无推送通道）。"""
        while True:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            for entity_id, device_id in list(self._device_ids.items()):
                try:
                    await self._refresh_device(entity_id, device_id)
                except Exception as exc:
                    log(f"小爱音箱状态轮询失败: {entity_id} - {exc}",
                        "DEBUG", tag=_LOG_TAG)

    async def _refresh_device(self, entity_id: str, device_id: str) -> None:
        """刷新单台音箱的播放状态与音量。"""
        device = self._devices.get(entity_id)
        if device is None:
            return
        status = await self._client.speaker_status(device_id)
        if not isinstance(status, dict):
            return
        playing = status.get("status") == 1
        device.state = "playing" if playing else "idle"
        volume = status.get("volume")
        if isinstance(volume, (int, float)):
            device.attributes["volume_level"] = max(0.0, min(volume / 100, 1.0))
        detail = status.get("play_song_detail")
        if isinstance(detail, dict) and detail.get("title"):
            device.attributes["media_title"] = str(detail["title"])
        if self.on_device_update is not None:
            self.on_device_update(device)

    # ---- 控制 ----

    async def call_service(
        self, domain: str, service: str, entity_id: str, data: Dict[str, Any],
    ) -> None:
        if not self._connected:
            raise ConnectionError("未连接到小米云")
        device_id = self._device_ids.get(entity_id)
        if device_id is None:
            raise ValueError(f"未知设备: {entity_id}")
        if service == "volume_set":
            level = float(data.get("volume_level", 0))
            await self._client.set_volume(device_id, round(level * 100))
            return
        method = _SERVICE_METHODS.get(service)
        if method is None:
            raise ValueError(f"小爱音箱不支持服务: {domain}.{service}")
        await getattr(self._client, method)(device_id)

    # ---- 原生语音播报 ----

    def supports_speak(self, device: DeviceState) -> bool:
        return device.entity_id in self._device_ids

    async def speak(self, device: DeviceState, text: str) -> None:
        if not self._connected:
            raise ConnectionError("未连接到小米云")
        device_id = self._device_ids.get(device.entity_id)
        if device_id is None:
            raise ValueError(f"未知设备: {device.entity_id}")
        await self._client.speak(device_id, text)

    def _emit_connection(self) -> None:
        if self.on_connection is not None:
            try:
                self.on_connection(self.status())
            except Exception:
                pass
