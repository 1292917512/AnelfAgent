"""后台子进程的跨平台窗口策略；不改变交互式 start 入口的终端行为。"""

from __future__ import annotations

import subprocess
import sys
from typing import TypedDict

if sys.platform == "win32":
    class BackgroundOptions(TypedDict):
        """供 subprocess 调用展开的 Windows 启动参数。"""

        creationflags: int
        startupinfo: subprocess.STARTUPINFO | None
else:
    class BackgroundOptions(TypedDict):
        """POSIX 无需 Windows 启动属性。"""

        creationflags: int
        startupinfo: None


def background_options() -> BackgroundOptions:
    """Windows 禁止创建控制台并隐藏子窗口；POSIX 使用原生进程行为。"""
    if sys.platform != "win32":
        return {"creationflags": 0, "startupinfo": None}
    else:
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startup}
