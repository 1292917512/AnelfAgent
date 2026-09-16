"""桌面执行器 — pyautogui 动作的探测与执行。

pyautogui 为可选运行时依赖（find_spec 探测，缺失时动作返回带安装提示
的结构化错误）；全部动作经 to_thread 执行（pyautogui 阻塞且有逐字/滑动
时长）。FAILSAFE 保持默认开启——鼠标猛移屏幕左上角即中止执行，这是
失控时的物理刹车。
"""

from __future__ import annotations

import asyncio
import importlib.util
from typing import Any, Dict, Tuple

_MISSING_HINT = (
    "pyautogui 不可用——它是声明依赖，环境未同步时执行 uv sync 恢复；"
    "macOS 还需在系统设置→隐私与安全性→辅助功能中授权本进程，"
    "否则动作执行成功但系统不响应"
)


def runtime_ready() -> bool:
    return importlib.util.find_spec("pyautogui") is not None


def install_hint() -> str:
    return _MISSING_HINT


def screen_size() -> Tuple[int, int]:
    """屏幕分辨率（pyautogui 缺失时抛 RuntimeError）。"""
    import pyautogui

    w, h = pyautogui.size()
    return int(w), int(h)


async def run_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """执行一个桌面动作（desktop.* 去前缀），返回 {ok, result/error}。

    参数契约见 framework.DESKTOP_ACTIONS 各条 description。
    """
    if not runtime_ready():
        return {"ok": False, "error": _MISSING_HINT, "missing_runtime": True}

    def _run() -> str:
        import pyautogui

        x = int(params.get("x") or 0)
        y = int(params.get("y") or 0)
        button = str(params.get("button") or "left")
        duration = float(params.get("duration") or 0.0)
        if action == "click":
            pyautogui.click(x, y, button=button)
            return f"已单击 ({x}, {y}) button={button}"
        if action == "double_click":
            pyautogui.doubleClick(x, y)
            return f"已双击 ({x}, {y})"
        if action == "right_click":
            pyautogui.rightClick(x, y)
            return f"已右键 ({x}, {y})"
        if action == "move":
            pyautogui.moveTo(x, y, duration=max(0.0, min(duration, 3.0)))
            return f"已移动到 ({x}, {y})"
        if action == "drag":
            pyautogui.moveTo(int(params.get("x") or 0), int(params.get("y") or 0))
            pyautogui.dragTo(
                int(params.get("x2") or 0), int(params.get("y2") or 0),
                duration=max(0.2, min(duration, 3.0)), button=button,
            )
            return f"已拖拽 ({x}, {y}) → ({params.get('x2')}, {params.get('y2')})"
        if action == "scroll":
            amount = int(params.get("amount") or 0)
            if params.get("x") is not None and params.get("y") is not None:
                pyautogui.scroll(amount, x, y)
            else:
                pyautogui.scroll(amount)
            return f"已滚动 {amount} 格"
        if action == "type":
            text = str(params.get("text") or "")
            if not text:
                raise ValueError("type 动作需要 text 参数")
            if not text.isascii():
                raise ValueError(
                    "pyautogui 仅支持 ASCII 输入：非 ASCII 文本请先写入剪贴板，"
                    "再用 desktop.hotkey 粘贴（macOS cmd+v / Windows ctrl+v）",
                )
            pyautogui.typewrite(
                text, interval=max(0.0, min(float(params.get("interval") or 0.0), 0.5)))
            return f"已输入 {len(text)} 字符"
        if action == "hotkey":
            keys = str(params.get("keys") or "")
            if not keys:
                raise ValueError("hotkey 动作需要 keys 参数")
            pyautogui.hotkey(*[k.strip() for k in keys.split("+") if k.strip()])
            return f"已按组合键 {keys}"
        if action == "key":
            keys = str(params.get("keys") or "")
            if not keys:
                raise ValueError("key 动作需要 keys 参数")
            pyautogui.press(keys, presses=int(params.get("presses") or 1))
            return f"已按 {keys}"
        raise ValueError(f"未知桌面动作: {action}")

    try:
        result = await asyncio.wait_for(asyncio.to_thread(_run), timeout=30.0)
        return {"ok": True, "result": result}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "桌面动作执行超时（30s）"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
