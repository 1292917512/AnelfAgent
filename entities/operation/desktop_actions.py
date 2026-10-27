"""子进程内的有界输入动作；每个可中断步骤之间检查停止，并释放自己按下的键。"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from typing import Any


class ActionStopped(Exception):
    """停止后不再发出输入。"""


class DesktopActions:
    """将动作拆成输入步骤；进度只证明已调用输入接口，不证明目标应用接受。"""

    def __init__(self, backend: Any, stop: threading.Event, emit: Callable[[dict[str, Any]], None]) -> None:
        self.backend = backend
        self.stop = stop
        self.emit = emit
        self.keys: list[str] = []
        self.buttons: list[str] = []
        self.completed = 0
        self.total = 0
        self.started = False

    def report(self) -> None:
        self.emit({"event": "progress", "completed": self.completed, "total": self.total,
                   "held_keys": list(self.keys), "held_buttons": list(self.buttons)})

    def check(self, delay: float = 0) -> None:
        if self.stop.wait(delay):
            raise ActionStopped
        self.started = True

    def step(self, fn: Callable[[], Any]) -> None:
        self.check()
        fn()
        self.completed += 1
        self.report()

    def release(self) -> None:
        """释放只限本动作持有的输入；FAILSAFE 不得阻止释放修饰键或拖拽键。"""
        self.backend.FAILSAFE = False
        try:
            for key in list(reversed(self.keys)):
                self.backend.keyUp(key)
                self.keys.remove(key)
                self.report()
            for button in list(self.buttons):
                self.backend.mouseUp(button=button)
                self.buttons.remove(button)
                self.report()
        finally:
            self.backend.FAILSAFE = True

    def key_down(self, key: str) -> None:
        self.check()
        self.keys.append(key)
        self.report()
        self.backend.keyDown(key)

    def mouse_down(self, button: str) -> None:
        self.check()
        self.buttons.append(button)
        self.report()
        self.backend.mouseDown(button=button)

    def run(self, action: str, params: dict[str, Any]) -> None:
        """校验完整参数后才开始输入；字符、按键、滚动和拖动都有有限步数。"""
        b = self.backend
        x, y = int(params.get("x", 0)), int(params.get("y", 0))
        button = str(params.get("button", "left"))
        if button not in {"left", "right", "middle"}:
            raise ValueError("button 必须为 left/right/middle")
        duration = float(params.get("duration", 0))
        interval = float(params.get("interval", 0))
        if not math.isfinite(duration) or not math.isfinite(interval):
            raise ValueError("时长必须为有限数字")
        duration, interval = max(0, min(duration, 3)), max(0, min(interval, .5))
        text = str(params.get("text", ""))
        keys = [k.strip().lower() for k in str(params.get("keys", "")).split("+") if k.strip()]
        if action == "type" and (not text or not text.isascii() or len(text) > 10000):
            raise ValueError("type 需要 1–10000 个 ASCII 字符；其他文本请使用剪贴板粘贴")
        if action in {"key", "hotkey"} and (not keys or len(keys) > 8 or any(k not in b.KEYBOARD_KEYS for k in keys)):
            raise ValueError("keys 必须为有效键名，组合键最多 8 个")
        if action == "key" and len(keys) != 1:
            raise ValueError("key 仅接受一个键；组合键使用 hotkey")
        amount = int(params.get("amount", 0))
        if abs(amount) > 1000:
            raise ValueError("滚动格数不能超过 1000")
        b.PAUSE = 0
        if action in {"move", "drag"}:
            end_x, end_y = (int(params.get("x2", 0)), int(params.get("y2", 0))) if action == "drag" else (x, y)
            start_x, start_y = (x, y) if action == "drag" else b.position()
            steps = max(1, math.ceil(max(.2 if action == "drag" else 0, duration) / .02))
            self.total = steps
            if action == "drag":
                self.check()
                b.moveTo(x, y)
                self.mouse_down(button)
            for i in range(1, steps + 1):
                self.step(lambda: b.moveTo(round(start_x + (end_x - start_x) * i / steps),
                                          round(start_y + (end_y - start_y) * i / steps)))
                self.check(duration / steps)
        elif action == "type":
            self.total = len(text)
            for char in text:
                self.step(lambda: b.write(char))
                self.check(interval)
        elif action in {"key", "hotkey"}:
            self.total = 1
            for key in keys:
                self.key_down(key)
            self.release()
            self.completed = 1
            self.report()
        elif action == "scroll":
            self.total = abs(amount)
            for _ in range(abs(amount)):
                self.step(lambda: b.scroll(1 if amount > 0 else -1, x, y))
        elif action in {"click", "double_click", "right_click"}:
            self.total = 2 if action == "double_click" else 1
            if action == "right_click":
                button = "right"
            self.check()
            b.moveTo(x, y)
            for _ in range(self.total):
                self.mouse_down(button)
                self.release()
                self.completed += 1
                self.report()
        else:
            raise ValueError(f"未知桌面动作: {action}")
