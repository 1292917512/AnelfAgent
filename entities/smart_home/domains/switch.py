"""开关域 — 墙壁开关 / 智能插座等通断设备。"""

from __future__ import annotations

from ..framework import DeviceDomain, device_domain


@device_domain
class SwitchDomain(DeviceDomain):
    """开关与智能插座（仅通断控制）。"""

    key = "switch"
    display_name = "开关"
    description = "墙壁开关、智能插座等通断设备"
    priority = 20
    ha_domains = ("switch",)
