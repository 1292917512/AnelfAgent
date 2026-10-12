"""空调温控域 — 空调 / 温控器的模式与温度控制。"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Dict

from ..framework import DeviceDomain, device_domain
from ..models import ActionSpec, DeviceState

_HVAC_MODES = {
    "off": "关",
    "heat": "制热",
    "cool": "制冷",
    "heat_cool": "温控",
    "auto": "自动",
    "dry": "除湿",
    "fan_only": "送风",
}


def _temperature(raw: str) -> float:
    """解析目标温度（摄氏度）。"""
    value = float(raw)
    if not 5.0 <= value <= 40.0:
        raise ValueError("超出 5-40°C 范围")
    return value


@device_domain
class ClimateDomain(DeviceDomain):
    """空调与温控器（模式/目标温度，展示室温与湿度）。"""

    key = "climate"
    display_name = "空调温控"
    description = "空调与温控器（模式/目标温度）"
    priority = 15
    ha_domains = ("climate",)

    def format_state(self, device: DeviceState) -> str:
        parts = [_HVAC_MODES.get(device.state, "开" if device.state == "on" else device.state)]
        unit = str(device.attributes.get("temperature_unit") or "°C")
        target = device.attributes.get("temperature")
        if isinstance(target, (int, float)):
            parts.append(f"{target:g}{unit}")
        current = device.attributes.get("current_temperature")
        if isinstance(current, (int, float)):
            parts.append(f"室温 {current:g}{unit}")
        humidity = device.attributes.get("current_humidity")
        if isinstance(humidity, (int, float)):
            parts.append(f"湿度 {humidity:g}%")
        return " · ".join(parts)

    def actions(self) -> Dict[str, ActionSpec]:
        return {
            "turn_on": ActionSpec("turn_on", "打开"),
            "turn_off": ActionSpec("turn_off", "关闭"),
            "set_temperature": ActionSpec(
                "set_temperature", "设置目标温度", value_param="temperature",
                value_hint="目标温度摄氏度 5-40", convert=_temperature,
                minimum=5, maximum=40, step=1,
            ),
            "set_hvac_mode": ActionSpec(
                "set_hvac_mode", "设置模式", value_param="hvac_mode",
                value_hint="模式：cool/heat/heat_cool/auto/dry/fan_only",
                options=tuple(_HVAC_MODES),
            ),
        }

    def actions_for(self, device: DeviceState) -> Dict[str, ActionSpec]:
        actions = self.actions()
        attrs = device.attributes
        def numeric(value: Any, default: float) -> float:
            return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else default

        minimum = numeric(attrs.get("min_temp"), 5)
        maximum = numeric(attrs.get("max_temp"), 40)
        if minimum >= maximum:
            minimum, maximum = 5, 40
        step = numeric(attrs.get("target_temp_step"), 1)
        actions["set_temperature"] = replace(
            actions["set_temperature"], convert=float,
            minimum=minimum, maximum=maximum, step=step if step > 0 else 1,
            value_hint=f"目标温度 {attrs.get('temperature_unit', '°C')}",
        )
        modes = attrs.get("hvac_modes")
        if isinstance(modes, list) and modes:
            actions["set_hvac_mode"] = replace(actions["set_hvac_mode"], options=tuple(str(m) for m in modes))
        return actions
