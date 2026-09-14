"""屏幕视觉源 — 本机截屏组件（mss 捕获后端），接入核心视觉框架。"""

from __future__ import annotations

from typing import Optional

from core.config import get_config_int
from entities._sdk import CapturedFrame, VisualSource

from .capture import capture_screen


class ScreenSource(VisualSource):
    """本机屏幕（macOS 需系统设置授予屏幕录制权限）。"""

    key = "screen"
    display_name = "屏幕"
    description = "本机屏幕画面（截屏/盯屏）"
    poll_interval = 1.0  # 标记轮询型（实际间隔读 vision_watch_interval_s 配置）
    can_capture = True

    async def capture(self) -> Optional[CapturedFrame]:
        return await capture_screen(get_config_int("screen_monitor", 1))
