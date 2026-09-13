"""场景域 — Home Assistant 场景的激活。"""

from __future__ import annotations

from typing import Dict

from ..framework import DeviceDomain, device_domain
from ..models import ActionSpec, DeviceState


@device_domain
class SceneDomain(DeviceDomain):
    """预设场景（仅激活；state 为上次激活时间戳，注入只展示名称）。"""

    key = "scene"
    display_name = "场景"
    description = "预设场景（激活一组设备状态）"
    priority = 50
    ha_domains = ("scene",)

    def format_device(self, device: DeviceState) -> str:
        return device.name

    def actions(self) -> Dict[str, ActionSpec]:
        return {
            "activate": ActionSpec("turn_on", "激活场景"),
        }
