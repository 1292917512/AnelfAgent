"""Windows 实体输入接管：只辨别非注入事件，不收集键值、不拦截用户输入。

低级钩子在专用消息循环中运行；回调仅置位事件，避免阻塞桌面消息链。
其他系统提供显式接管入口，不声称支持自动识别。
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes


def physical_input(kind: str, flags: int) -> bool:
    """LLKHF_INJECTED=0x10，LLMHF_INJECTED=0x01。"""
    return not bool(flags & (0x10 if kind == "keyboard" else 0x01))


class InputMonitor:
    """动作期间的输入来源监测；只记录是否发生接管。"""

    def __init__(self, stop: threading.Event) -> None:
        self.stop = stop
        self.taken = threading.Event()
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.error = ""
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        if sys.platform == "win32":
            self.thread.start()
            if not self.ready.wait(2) or self.error:
                self.close()
                raise RuntimeError(self.error or "输入接管监听启动超时")

    def close(self) -> None:
        self.closed.set()
        if self.thread.is_alive():
            self.thread.join(1)

    def _run(self) -> None:
        if sys.platform == "win32":
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p)

            class Keyboard(ctypes.Structure):
                _fields_ = [("vk", wintypes.DWORD), ("scan", wintypes.DWORD), ("flags", wintypes.DWORD),
                            ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

            class Mouse(ctypes.Structure):
                _fields_ = [("point", wintypes.POINT), ("data", wintypes.DWORD), ("flags", wintypes.DWORD),
                            ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, ctypes.c_void_p, wintypes.DWORD]
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p]
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
            user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT,
                                          wintypes.UINT, wintypes.UINT]

            def keyboard(code: int, message: int, address: int) -> int:
                if code >= 0 and physical_input("keyboard", Keyboard.from_address(address).flags):
                    self.taken.set()
                    self.stop.set()
                return user32.CallNextHookEx(None, code, message, address)

            def mouse(code: int, message: int, address: int) -> int:
                if code >= 0 and physical_input("mouse", Mouse.from_address(address).flags):
                    self.taken.set()
                    self.stop.set()
                return user32.CallNextHookEx(None, code, message, address)

            callbacks = (callback_type(keyboard), callback_type(mouse))
            hooks: list[int] = []
            try:
                for kind, callback in zip((13, 14), callbacks, strict=True):
                    handle = user32.SetWindowsHookExW(kind, callback, None, 0)
                    if not handle:
                        raise ctypes.WinError(ctypes.get_last_error())
                    hooks.append(handle)
                self.ready.set()
                msg = wintypes.MSG()
                while not self.closed.wait(.005):
                    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
            except Exception as exc:
                self.error = str(exc)
                self.stop.set()
                self.ready.set()
            finally:
                for handle in hooks:
                    user32.UnhookWindowsHookEx(handle)
        else:
            self.error = "自动输入接管仅支持 Windows"
            self.ready.set()
