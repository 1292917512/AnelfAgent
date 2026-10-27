"""独立桌面工作进程；stdin 关闭或取消即停止，退出前释放本动作持有的输入。"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any

# 以脚本启动，避免导入实体包触发宿主注册与模型依赖。
from desktop_actions import ActionStopped, DesktopActions
from desktop_takeover import InputMonitor


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
        import pyautogui

        engine = DesktopActions(pyautogui, stop, emit)
        action, params = request["action"], request.get("params", {})
        if action == "probe":
            outcome.update(status="completed", screen=list(pyautogui.size()))
        elif action == "release":
            engine.keys = params.get("held_keys", [])
            engine.buttons = params.get("held_buttons", [])
        else:
            if request.get("takeover", True):
                monitor.start()
            engine.run(action, params)
        outcome["status"] = "completed"
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
