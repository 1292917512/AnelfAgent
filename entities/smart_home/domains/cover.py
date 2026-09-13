"""窗帘域 — 窗帘 / 卷帘的开合与位置控制。"""

from __future__ import annotations

from typing import Dict

from ..framework import DeviceDomain, device_domain
from ..models import ActionSpec, DeviceState

_COVER_STATES = {
    "open": "开",
    "closed": "关",
    "opening": "正在开",
    "closing": "正在关",
}


def _position(raw: str) -> int:
    """解析位置百分比值。"""
    value = int(float(raw))
    if not 0 <= value <= 100:
        raise ValueError("超出 0-100 范围")
    return value


@device_domain
class CoverDomain(DeviceDomain):
    """窗帘与卷帘（开/关/停/位置百分比）。"""

    key = "cover"
    display_name = "窗帘"
    description = "窗帘、卷帘等开合设备（开/关/停/位置）"
    priority = 25
    ha_domains = ("cover",)

    def format_state(self, device: DeviceState) -> str:
        text = _COVER_STATES.get(device.state, device.state)
        position = device.attributes.get("current_position")
        if isinstance(position, (int, float)):
            text = f"{text} · 位置 {position:g}%"
        return text

    def actions(self) -> Dict[str, ActionSpec]:
        return {
            "open_cover": ActionSpec("open_cover", "打开"),
            "close_cover": ActionSpec("close_cover", "关闭"),
            "stop_cover": ActionSpec("stop_cover", "停止"),
            "set_position": ActionSpec(
                "set_cover_position", "设置位置", value_param="position",
                value_hint="位置百分比 0-100（100 为全开）", convert=_position,
            ),
        }
