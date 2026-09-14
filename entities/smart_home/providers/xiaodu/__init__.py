"""小度音箱平台接入 — 局域网 DLNA 直连（发现 / 播放控制 / TTS 播报）。

小度音箱映射为 ``xiaodu.<ip>`` 的 media_player 设备：状态经周期轮询
（小度 eventing 不可靠），语音播报走 edge-tts 合成 + 内嵌音频服务 +
DLNA 播放的完整链路（supports_speak，不经任何云平台）。
设备标识用 IP——小度每次重启 UDN 会变动。
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from core.log import log

from ...models import DeviceState
from .. import home_provider
from ..base import SmartHomeProvider
from . import discovery
from .dlna import XiaoduSpeaker
from .server import AudioServer
from .tts import TtsSynthesizer, get_synthesizer

_LOG_TAG = "智能家居"


@home_provider
class XiaoduProvider(SmartHomeProvider):
    """小度音箱接入（DLNA 直连：状态轮询 / 音量控制 / 本地 TTS 播报）。"""

    key = "xiaodu"
    display_name = "小度音箱"
    entity_prefix = "xiaodu."
    discovery_supported = True
    config_schema = {
        "hosts": {
            "description": "手动指定的小度音箱 IP（逗号分隔；跨网段/发现不到时使用，留空纯靠局域网自动发现）",
            "default": "",
        },
        "audio_port": {
            "description": "播报音频服务端口（内嵌 HTTP 服务，供音箱拉取 TTS 音频）",
            "default": 8195,
            "min": 1024,
            "max": 65535,
            "advanced": True,
        },
        "tts_voice": {
            "description": "播报语音（edge-tts 音色，如 zh-CN-XiaoxiaoNeural / zh-CN-YunxiNeural）",
            "default": "zh-CN-XiaoxiaoNeural",
            "advanced": True,
        },
        "poll_interval": {
            "description": "音箱状态轮询间隔（小度 eventing 不可靠，只能轮询）",
            "default": 30,
            "min": 5,
            "max": 300,
            "unit": "秒",
            "advanced": True,
        },
    }

    def __init__(self) -> None:
        super().__init__()
        self._speakers: Dict[str, XiaoduSpeaker] = {}
        self._devices: Dict[str, DeviceState] = {}
        self._server: Optional[AudioServer] = None
        self._synthesizer: Optional[TtsSynthesizer] = None
        self._connected = False
        self._last_error = ""
        self._connected_at = 0.0
        self._poll_task: Optional[asyncio.Task[None]] = None

    # ---- 状态 ----

    def is_configured(self) -> bool:
        """小度为局域网直连，无需凭据——始终可启动（无音箱时为空载）。"""
        return True

    def is_connected(self) -> bool:
        return self._connected

    def snapshot(self) -> List[DeviceState]:
        return list(self._devices.values())

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.key,
            "provider_name": self.display_name,
            "configured": True,
            "connected": self._connected,
            "device_count": len(self._devices),
            "last_error": self._last_error or None,
            "connected_at": self._connected_at or None,
            "discovery": self.discovery_supported,
        }

    # ---- 生命周期 ----

    async def connect(self) -> None:
        """发现并接入音箱（幂等；音频服务懒启动——有音箱才占用端口）。"""
        if self._connected:
            return
        self._synthesizer = get_synthesizer(
            str(self.get_config("tts_voice", "zh-CN-XiaoxiaoNeural")).strip()
            or "zh-CN-XiaoxiaoNeural",
        )
        locations = self._manual_locations() + await self._safe_discover()
        await self._add_locations(locations)
        await self._ensure_server()
        self._connected = True
        self._connected_at = time.time()
        self._last_error = ""
        self._emit_connection()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def _ensure_server(self) -> None:
        """音频服务懒启动（仅在有已接入音箱时占用端口）。"""
        if self._server is not None or not self._devices:
            return
        server = AudioServer(
            TtsSynthesizer.cache_dir(),
            int(self.get_config("audio_port", 8195) or 8195),
        )
        try:
            await server.start()
        except Exception as exc:
            self._last_error = f"音频服务启动失败: {exc}"
            log(self._last_error, "WARNING", tag=_LOG_TAG)
            return
        self._server = server

    async def close(self) -> None:
        """停止轮询并关停音频服务（幂等；保留末次设备缓存供展示）。"""
        task, self._poll_task = self._poll_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        server, self._server = self._server, None
        if server is not None:
            await server.stop()
        if self._connected:
            self._connected = False
            self._emit_connection()

    # ---- 发现与接入 ----

    def _manual_locations(self) -> List[str]:
        """手动 hosts 配置转 description.xml 地址。"""
        raw = str(self.get_config("hosts", "")).strip()
        locations: List[str] = []
        for host in raw.split(","):
            host = host.strip()
            if not host:
                continue
            if host.startswith("http://") or host.startswith("https://"):
                locations.append(host)
            else:
                locations.append(f"http://{host}:49494/description.xml")
        return locations

    async def _safe_discover(self) -> List[str]:
        """SSDP 发现（异常仅记日志，不阻断手动 hosts）。"""
        try:
            locations = await discovery.discover_renderers()
        except Exception as exc:
            log(f"SSDP 发现失败: {exc}", "WARNING", tag=_LOG_TAG)
            return []
        return [loc for loc in locations if discovery.is_xiaodu_location(loc)]

    async def discover(self) -> List[Dict[str, Any]]:
        """发现局域网小度音箱并接入新发现的（面板「发现音箱」按钮）。"""
        if not self._connected:
            raise ConnectionError("小度平台未启动")
        locations = self._manual_locations() + await self._safe_discover()
        added = await self._add_locations(locations)
        return [
            {"entity_id": entity_id, "name": self._devices[entity_id].name}
            for entity_id in added
        ]

    async def _add_locations(self, locations: List[str]) -> List[str]:
        """按 description.xml 地址接入音箱（已接入跳过；失败仅记日志）。"""
        added: List[str] = []
        for location in dict.fromkeys(locations):
            speaker = await self._create_speaker(location)
            if speaker is None:
                continue
            entity_id = f"{self.entity_prefix}{speaker.host.replace('.', '_')}"
            if entity_id in self._devices:
                self._speakers[entity_id] = speaker
                continue
            self._speakers[entity_id] = speaker
            self._devices[entity_id] = DeviceState(
                entity_id=entity_id,
                domain="media_player",
                name=speaker.name,
                state="idle",
            )
            added.append(entity_id)
        if added:
            await self._ensure_server()
            if self.on_sync is not None:
                self.on_sync()
        return added

    async def _create_speaker(self, location: str) -> Optional[XiaoduSpeaker]:
        """构建单台音箱控制会话（拉取 description.xml，失败记日志返回 None）。"""
        try:
            return await XiaoduSpeaker.create(location)
        except Exception as exc:
            log(f"小度音箱接入失败: {location} - {exc}", "WARNING", tag=_LOG_TAG)
            return None

    # ---- 状态轮询 ----

    async def _poll_loop(self) -> None:
        """周期轮询全部音箱状态（无 eventing 的唯一状态来源）。"""
        while True:
            await asyncio.sleep(
                float(self.get_config("poll_interval", 30) or 30),
            )
            for entity_id in list(self._speakers):
                try:
                    await self._refresh_device(entity_id)
                except Exception as exc:
                    log(f"小度音箱状态轮询失败: {entity_id} - {exc}",
                        "DEBUG", tag=_LOG_TAG)

    async def _refresh_device(self, entity_id: str) -> None:
        """刷新单台音箱的播放状态与音量。"""
        speaker = self._speakers.get(entity_id)
        device = self._devices.get(entity_id)
        if speaker is None or device is None:
            return
        status = await speaker.poll()
        device.state = status["state"]
        volume = status.get("volume_level")
        if isinstance(volume, (int, float)):
            device.attributes["volume_level"] = max(0.0, min(volume, 1.0))
        if status.get("media_title"):
            device.attributes["media_title"] = status["media_title"]
        if self.on_device_update is not None:
            self.on_device_update(device)

    # ---- 控制 ----

    async def call_service(
        self, domain: str, service: str, entity_id: str, data: Dict[str, Any],
    ) -> None:
        speaker = self._require_speaker(entity_id)
        if service == "volume_set":
            level = float(data.get("volume_level", 0))
            await speaker.set_volume(max(0.0, min(level, 1.0)))
        elif service in ("media_stop", "turn_off"):
            await speaker.stop()
        elif service in ("media_play", "turn_on"):
            await speaker.play()
        elif service == "media_pause":
            await speaker.pause()
        else:
            raise ValueError(f"小度音箱不支持服务: {domain}.{service}")

    # ---- 原生语音播报 ----

    def supports_speak(self, device: DeviceState) -> bool:
        return device.entity_id in self._speakers

    async def speak(self, device: DeviceState, text: str) -> None:
        """TTS 播报全链路：合成 → 内嵌服务 URL → DLNA 播放。"""
        speaker = self._require_speaker(device.entity_id)
        if self._server is None or self._synthesizer is None:
            raise ConnectionError("小度播报服务未启动")
        path = await self._synthesizer.synthesize(text)
        url = self._server.url_for(os.path.basename(path), speaker.host)
        await speaker.play_url(url, "语音播报")

    def _require_speaker(self, entity_id: str) -> XiaoduSpeaker:
        speaker = self._speakers.get(entity_id)
        if speaker is None:
            raise ValueError(f"未知设备: {entity_id}")
        return speaker

    def _emit_connection(self) -> None:
        if self.on_connection is not None:
            try:
                self.on_connection(self.status())
            except Exception:
                pass
