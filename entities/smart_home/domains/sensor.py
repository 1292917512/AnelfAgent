"""传感器域 — 温湿度 / 光照 / 电量等只读传感器的状态注入。"""

from __future__ import annotations

from typing import Dict

from ..framework import DeviceDomain, device_domain
from ..models import ActionSpec, DeviceState


@device_domain
class SensorDomain(DeviceDomain):
    """只读传感器（数值带单位展示，无控制动作）。"""

    key = "sensor"
    display_name = "传感器"
    description = "温湿度、光照、电量等只读传感器"
    priority = 40
    ha_domains = ("sensor", "binary_sensor")

    def format_state(self, device: DeviceState) -> str:
        if device.domain == "binary_sensor":
            return "触发" if device.state == "on" else "正常"
        unit = str(device.attributes.get("unit_of_measurement") or "")
        return f"{device.state}{unit}"

    def actions(self) -> Dict[str, ActionSpec]:
        return {}
