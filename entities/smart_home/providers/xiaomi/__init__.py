"""小爱音箱平台接入 — 小米云 MiNA 通道（miservice）。

小爱音箱映射为 ``xiaomi.<deviceID>`` 的 media_player 设备：状态/音量经
周期轮询同步，播放控制映射 MiNA ubus，语音播报走小米云原生 TTS
（supports_speak，不经 HA tts_service）。登录凭据为小米账号密码
（password 脱敏），登录态 token 缓存于 ConfigPaths 数据目录，按账号隔离。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from core.log import log

from ...models import ActionSpec, DeviceState
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
        }

    # ---- 生命周期 ----

    async def connect(self) -> None:
        """启动后台连接与重试，避免登录阻塞应用生命周期。"""
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(self._supervise())

    async def _supervise(self) -> None:
        delay = 1.0
        while True:
            try:
                await self._client.open(
                    str(self.get_config("username", "")).strip(),
                    str(self.get_config("password", "")).strip(),
                )
                await self._sync_devices()
                self._connected = True
                self._connected_at = time.time()
                self._last_error = ""
                self._emit_connection()
                delay = 1.0
                await self._poll_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                log(f"小米云连接异常: {exc}", "WARNING", tag=_LOG_TAG)
            finally:
                self._connected = False
                await self._client.close()
                self._emit_connection()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    def max_state_age(self) -> Optional[float]:
        return _POLL_INTERVAL_SECONDS * 3

    def actions_for(self, device: DeviceState) -> Dict[str, ActionSpec]:
        allowed = {"media_play", "media_pause", "turn_off", "set_volume", "speak"}
        return {key: spec for key, spec in super().actions_for(device).items() if key in allowed}

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
                state=previous.state if previous else "unknown",
                attributes=dict(previous.attributes) if previous else {},
                updated_at=previous.updated_at if previous else 0,
                stale=previous.stale if previous else True,
            )
            device_ids[entity_id] = device_id
        self._devices = devices
        self._device_ids = device_ids
        if self.on_sync is not None:
            self.on_sync()

    async def _poll_loop(self) -> None:
        """周期轮询音箱播放状态（MiNA 无推送通道）。"""
        while True:
            failures = 0
            for entity_id, device_id in list(self._device_ids.items()):
                try:
                    await self._refresh_device(entity_id, device_id)
                except Exception as exc:
                    failures += 1
                    device = self._devices.get(entity_id)
                    if device is not None:
                        device.stale = True
                        if self.on_device_update:
                            self.on_device_update(device)
                    log(f"小爱音箱状态轮询失败: {entity_id} - {exc}",
                        "DEBUG", tag=_LOG_TAG)
            if self._device_ids and failures == len(self._device_ids):
                raise ConnectionError("全部小爱音箱状态同步失败")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            await self._sync_devices()

    async def _refresh_device(self, entity_id: str, device_id: str) -> None:
        """刷新单台音箱的播放状态与音量。"""
        device = self._devices.get(entity_id)
        if device is None:
            return
        status = await self._client.speaker_status(device_id)
        if not isinstance(status, dict):
            raise ConnectionError("小米云未返回有效播放状态")
        playback = status.get("status")
        device.state = "playing" if playback == 1 else "paused" if playback == 2 else "idle"
        device.updated_at = time.time()
        device.stale = False
        volume = status.get("volume")
        if isinstance(volume, (int, float)):
            device.attributes["volume_level"] = max(0.0, min(volume / 100, 1.0))
        detail = status.get("play_song_detail")
        if isinstance(detail, dict) and detail.get("title"):
            device.attributes["media_title"] = str(detail["title"])
        else:
            device.attributes.pop("media_title", None)
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
        if service == "set_volume":
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
            except Exception as exc:
                log(f"小米连接通知失败: {exc}", "DEBUG", tag=_LOG_TAG)
