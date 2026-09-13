"""播放器域 — 媒体播放器的播放控制与音量调节。"""

from __future__ import annotations

from typing import Dict

from ..framework import DeviceDomain, device_domain
from ..models import ActionSpec, DeviceState

_PLAYER_STATES = {
    "playing": "播放中",
    "paused": "已暂停",
    "idle": "待机",
    "off": "关",
    "on": "开",
}


def _volume(raw: str) -> float:
    """解析音量百分比并转为 0-1 电平。"""
    value = int(float(raw))
    if not 0 <= value <= 100:
        raise ValueError("超出 0-100 范围")
    return value / 100


@device_domain
class MediaPlayerDomain(DeviceDomain):
    """媒体播放器（播放/暂停/音量）。"""

    key = "media_player"
    display_name = "播放器"
    description = "音箱、电视等媒体播放器（播放/暂停/音量）"
    priority = 30
    ha_domains = ("media_player",)

    def format_state(self, device: DeviceState) -> str:
        text = _PLAYER_STATES.get(device.state, device.state)
        if device.state == "playing":
            title = str(device.attributes.get("media_title") or "").strip()
            if title:
                text = f"{text}《{title}》"
        volume = device.attributes.get("volume_level")
        if device.state != "off" and isinstance(volume, (int, float)):
            text = f"{text} · 音量 {round(volume * 100)}%"
        return text

    def actions(self) -> Dict[str, ActionSpec]:
        return {
            "turn_on": ActionSpec("turn_on", "打开"),
            "turn_off": ActionSpec("turn_off", "关闭"),
            "media_play": ActionSpec("media_play", "播放"),
            "media_pause": ActionSpec("media_pause", "暂停"),
            "set_volume": ActionSpec(
                "volume_set", "设置音量", value_param="volume_level",
                value_hint="音量百分比 0-100", convert=_volume,
            ),
        }
