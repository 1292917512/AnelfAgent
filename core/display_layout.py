"""本机截屏和输入工作进程共用的显示器布局快照。"""

from __future__ import annotations

import sys
from typing import Any


def display_layout(monitors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保留 MSS 的边界与设备标识，在 Windows 补充独立于分辨率的显示缩放。"""
    layout = [dict(monitor) for monitor in monitors]
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        try:
            factor = ctypes.windll.shcore.GetScaleFactorForMonitor
        except (AttributeError, OSError):
            for monitor in layout[1:]:
                monitor.setdefault("scale_percent", None)
            return layout
        monitor_at = ctypes.windll.user32.MonitorFromPoint
        monitor_at.argtypes = (wintypes.POINT, wintypes.DWORD)
        monitor_at.restype = wintypes.HMONITOR
        factor.argtypes = (wintypes.HMONITOR, ctypes.POINTER(ctypes.c_int))
        factor.restype = ctypes.c_long
        for monitor in layout[1:]:
            if "scale_percent" in monitor:
                continue
            point = wintypes.POINT(monitor["left"] + monitor["width"] // 2,
                                   monitor["top"] + monitor["height"] // 2)
            handle = monitor_at(point, 0)
            scale = ctypes.c_int()
            measured = handle and factor(handle, ctypes.byref(scale)) == 0
            monitor["scale_percent"] = scale.value if measured and scale.value > 0 else None
    return layout
