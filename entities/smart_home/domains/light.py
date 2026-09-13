"""灯光域 — 灯与灯组的开关 / 亮度 / 色温控制。"""

from __future__ import annotations

from typing import Dict

from ..framework import DeviceDomain, device_domain
from ..models import ActionSpec, DeviceState


def _percent(raw: str) -> int:
    """解析 0-100 百分比值。"""
    value = int(float(raw))
    if not 0 <= value <= 100:
        raise ValueError("超出 0-100 范围")
    return value


def _kelvin(raw: str) -> int:
    """解析色温开尔文值。"""
    value = int(float(raw))
    if not 1500 <= value <= 8000:
        raise ValueError("超出 1500-8000K 范围")
    return value


@device_domain
class LightDomain(DeviceDomain):
    """灯与灯组（开关/亮度百分比/色温开尔文）。"""

    key = "light"
    display_name = "灯光"
    description = "灯与灯组（开关/亮度/色温）"
    priority = 10
    ha_domains = ("light",)

    def format_state(self, device: DeviceState) -> str:
        if device.state != "on":
            return "关"
        parts = ["开"]
        brightness = device.attributes.get("brightness")
        if isinstance(brightness, (int, float)) and brightness > 0:
            parts.append(f"亮度 {round(brightness / 255 * 100)}%")
        kelvin = device.attributes.get("color_temp_kelvin")
        if isinstance(kelvin, (int, float)) and kelvin > 0:
            parts.append(f"色温 {round(kelvin)}K")
        return " · ".join(parts)

    def actions(self) -> Dict[str, ActionSpec]:
        return {
            **super().actions(),
            "set_brightness": ActionSpec(
                "turn_on", "设置亮度", value_param="brightness_pct",
                value_hint="亮度百分比 0-100", convert=_percent,
            ),
            "set_color_temp": ActionSpec(
                "turn_on", "设置色温", value_param="color_temp_kelvin",
                value_hint="色温开尔文 1500-8000", convert=_kelvin,
            ),
        }
