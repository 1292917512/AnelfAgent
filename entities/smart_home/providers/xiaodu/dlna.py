"""小度音箱 DLNA 控制 — async-upnp-client 封装（AVTransport / RenderingControl）。

小度固件特性（社区实证）：
- 播放前必须先 Stop，否则可能要点两次才发声；
- 即使状态已是 PLAYING 也必须再发 Play；
- eventing 不可靠，状态只能轮询 GetTransportInfo/GetVolume；
- 标识用 IP（每次重启 UDN 变动）。
"""

from __future__ import annotations

from typing import Any, Dict

from async_upnp_client.aiohttp import AiohttpRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.profiles.dlna import DmrDevice, TransportState
from yarl import URL


class XiaoduSpeaker:
    """单台小度音箱的 DLNA 控制会话。"""

    def __init__(self, location: str, device: DmrDevice) -> None:
        self.location = location
        """description.xml 地址（设备标识的一部分）。"""
        self.host = URL(location).host or ""
        """音箱 IP。"""
        self._device = device

    @property
    def name(self) -> str:
        """音箱名（description.xml 的 friendlyName）。"""
        return self._device.device.friendly_name or f"小度音箱（{self.host}）"

    @classmethod
    async def create(cls, location: str) -> "XiaoduSpeaker":
        """拉取 description.xml 并构建控制会话（失败抛异常）。"""
        factory = UpnpFactory(AiohttpRequester())
        device = await factory.async_create_device(location)
        dmr = DmrDevice(device)
        dmr.on_event = None  # 小度 eventing 不可靠，不订阅事件，纯轮询
        return cls(location, dmr)

    async def play_url(self, url: str, title: str) -> None:
        """播放音频 URL（Stop → SetAVTransportURI → 强制 Play 序列）。"""
        await self._device.async_stop()
        await self._device.async_set_transport_uri(url, title)
        await self._device.async_play()

    async def play(self) -> None:
        await self._device.async_play()

    async def pause(self) -> None:
        await self._device.async_pause()

    async def stop(self) -> None:
        await self._device.async_stop()

    async def set_volume(self, level: float) -> None:
        """设置音量（0-1 电平，量程映射由库按设备状态变量处理）。"""
        await self._device.async_set_volume_level(level)

    async def poll(self) -> Dict[str, Any]:
        """轮询播放状态与音量（无 eventing 的唯一状态来源）。"""
        await self._device.async_update(do_ping=False)
        state = self._device.transport_state
        return {
            "state": (
                "playing" if state == TransportState.PLAYING
                else "paused" if state in (
                    TransportState.PAUSED_PLAYBACK, TransportState.PAUSED_RECORDING,
                )
                else "idle"
            ),
            "volume_level": self._device.volume_level,
            "media_title": self._device.media_title or "",
        }
