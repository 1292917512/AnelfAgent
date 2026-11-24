"""独立桌面工作进程；stdin 关闭或取消即停止，退出前释放本动作持有的输入。"""

from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any

# 以脚本启动，避免导入实体包触发宿主注册与模型依赖。
from desktop_actions import ActionStopped, DesktopActions
from desktop_takeover import InputMonitor

if TYPE_CHECKING:
    from mss import MSS


class FrameChanged(Exception):
    """定位用的截屏几何信息已经失效。"""


def map_frame_points(screen: MSS, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """在首次输入前校验显示布局、截图像素尺寸和两个拖动端点。"""
    from core.display_layout import display_layout
    frame = params["_screen_frame"]
    monitors = display_layout(screen.monitors)
    if any("scale_percent" in m and m["scale_percent"] is None for m in monitors[1:]):
        raise FrameChanged("无法核实显示器缩放，请重新调用 vision_look")
    if monitors != frame["monitors"]:
        raise FrameChanged("显示器布局已变化，请重新调用 vision_look")
    bounds = monitors[frame["monitor"]]
    width, height = frame["width"], frame["height"]
    if width <= 0 or height <= 0 or screen.grab(bounds).size != (width, height):
        raise FrameChanged("截屏像素尺寸已变化，请重新调用 vision_look")
    mapped = dict(params)
    for suffix in (("", "2") if action == "drag" else ("",)):
        x_key, y_key = "x" + suffix, "y" + suffix
        x, y = int(params.get(x_key, 0)), int(params.get(y_key, 0))
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError("坐标超出截图原图尺寸")
        x = bounds["left"] + x * bounds["width"] // width
        y = bounds["top"] + y * bounds["height"] // height
        if not any(m["left"] <= x < m["left"] + m["width"] and
                   m["top"] <= y < m["top"] + m["height"] for m in monitors[1:]):
            raise ValueError("坐标位于显示器之间的空白区域")
        mapped[x_key], mapped[y_key] = x, y
    return mapped


def main() -> None:
    stop = threading.Event()
    done = threading.Event()

    def emit(payload: dict[str, Any]) -> None:
        print(json.dumps(payload, ensure_ascii=True), flush=True)

    def listen() -> None:
        sys.stdin.readline()
        stop.set()

    def watchdog() -> None:
        stop.wait()
        if not done.wait(2):
            os._exit(2)

    monitor = InputMonitor(stop)
    engine: DesktopActions | None = None
    outcome: dict[str, Any] = {"event": "result", "status": "failed", "input_released": False}
    try:
        request = json.loads(sys.stdin.readline())
        threading.Thread(target=listen, daemon=True).start()
        threading.Thread(target=watchdog, daemon=True).start()
        action, params = request["action"], request.get("params", {})
        with ExitStack() as resources:
            screen = None
            if "_screen_frame" in params:
                # Windows 在 pyautogui 导入前建立与截屏端一致的 per-monitor DPI 模式。
                from mss import MSS
                screen = resources.enter_context(MSS())
            import pyautogui

            engine = DesktopActions(pyautogui, stop, emit)
            if action == "probe":
                outcome.update(status="completed", screen=list(pyautogui.size()))
            elif action == "release":
                engine.keys = params.get("held_keys", [])
                engine.buttons = params.get("held_buttons", [])
            else:
                if request.get("takeover", True):
                    monitor.start()
                if screen is not None:
                    params = map_frame_points(screen, action, params)
                engine.run(action, params)
        outcome["status"] = "completed"
    except FrameChanged as exc:
        outcome.update(status="not_started", cause="state", error=str(exc))
    except ActionStopped:
        outcome["status"] = "cancelled"
    except Exception as exc:
        outcome["error"] = f"{type(exc).__name__}: {exc}"[:300]
        outcome["cause"] = "param" if isinstance(exc, (ValueError, TypeError)) else "internal"
        if type(exc).__name__ == "FailSafeException":
            outcome.update(status="cancelled", takeover=True)
    finally:
        try:
            if engine is not None:
                engine.release()
                outcome.update(completed=engine.completed, total=engine.total, input_released=True)
        except Exception as exc:
            outcome.update(status="failed", error=f"输入释放失败: {exc}"[:300])
        monitor.close()
        outcome["takeover"] = bool(outcome.get("takeover")) or monitor.taken.is_set()
        outcome["input_outcome"] = (
            "completed" if outcome["status"] == "completed" else
            "not_started" if engine is None or not engine.started else
            "partial" if outcome["status"] == "cancelled" else "unknown"
        )
        emit(outcome)
        done.set()


if __name__ == "__main__":
    main()
