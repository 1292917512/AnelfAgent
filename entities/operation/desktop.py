"""桌面独占执行、接管与停止确认；输入只在受管子进程中发生。

模型可见：有界进度、取消原因、释放状态及看屏事实；输入完成不代表业务目标完成。
Token：每次动作增加约 100–200 token 回执，无额外常驻历史。
缓存：仅工具结果和已有动态操作态势变化，不修改稳定前缀。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from core.cancellation import ToolCancelled, get_execution_signal
from core.config import get_config_bool, get_config_int
from core.tool_context import request_trace
from core.tool_errors import ErrorCause, tool_error

from .desktop_process import run_worker

Observer = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def runtime_ready() -> bool:
    return importlib.util.find_spec("pyautogui") is not None


def install_hint() -> str:
    return "桌面运行时不可用；请用 uv sync 同步依赖，并确认显示会话与辅助功能权限"


def failure(message: str, *, cause: ErrorCause = ErrorCause.STATE, **fields: Any) -> dict[str, Any]:
    return json.loads(tool_error(message, cause=cause, retryable=False, ok=False, **fields))


@dataclass
class ActiveAction:
    """一次输入及其验证共用的独占租约，不存储输入文本。"""

    operation_id: str
    scope: str
    run_id: str
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = ""
    phase: str = "starting"
    progress: dict[str, Any] = field(default_factory=lambda: {"completed": 0, "total": 0})
    receipt: dict[str, Any] = field(default_factory=dict)
    owner_stop_requested: bool = False

    def cancel(self, reason: str) -> None:
        if not self.stop.is_set():
            self.reason = reason
        self.phase = "stopping"
        self.stop.set()


class DesktopController:
    """进程内唯一桌面所有者；拒绝排队，取消收束完成前不交出控制权。"""

    def __init__(self) -> None:
        self.active: ActiveAction | None = None
        self.human = False
        self.closed = False
        self.revision = 0
        self._probe: dict[str, Any] = {}
        self._probe_at = 0.0

    def state(self) -> dict[str, Any]:
        active = self.active
        return {"state": active.phase if active else ("human" if self.human else "idle"),
                "human": self.human, "revision": self.revision,
                "automatic_takeover": sys.platform == "win32" and get_config_bool("operation_user_takeover", True),
                "active": {"operation_id": active.operation_id, "scope": active.scope, "run_id": active.run_id,
                           "completed": active.progress["completed"], "total": active.progress["total"]} if active else None}

    def quiesce(self) -> None:
        self.closed = True
        if self.active:
            self.active.cancel("系统正在关闭")

    async def close(self) -> None:
        self.quiesce()
        if self.active:
            await self.active.done.wait()

    async def control(self, action: str, revision: int) -> dict[str, Any]:
        if revision != self.revision:
            return failure("桌面状态已变化，请重新查询后控制", control=self.state())
        active = self.active
        if action == "release":
            if active:
                return failure("仍在收束桌面动作，不能交回控制权", control=self.state())
            self.human = False
        elif action in {"stop", "takeover"}:
            if action == "takeover":
                self.human = True
            if active:
                active.cancel("用户接管桌面" if action == "takeover" else "请求停止桌面操作")
        else:
            return failure("action 必须为 stop/takeover/release", cause=ErrorCause.PARAM)
        self.revision += 1
        if active:
            await self._stop_owner(active)
            await active.done.wait()
        return {"ok": True, "control": self.state(), "receipt": active.receipt if active else None}

    async def _stop_owner(self, active: ActiveAction) -> None:
        if active.run_id and not active.owner_stop_requested:
            active.owner_stop_requested = True
            from entities._sdk import request_execution_stop
            await request_execution_stop(run_id=active.run_id, reason=active.reason)

    async def probe(self) -> dict[str, Any]:
        if time.monotonic() - self._probe_at < 30 and self._probe:
            return self._probe
        try:
            result = await asyncio.wait_for(run_worker("probe", {}, stop=asyncio.Event(), update=lambda _: None), 3)
            self._probe = {"available": result.get("status") == "completed", "screen": result.get("screen"),
                           "hint": result.get("error", "")}
        except Exception:
            self._probe = {"available": False, "screen": None, "hint": install_hint()}
        self._probe_at = time.monotonic()
        return self._probe

    async def run(self, action: str, params: dict[str, Any], observer: Observer | None = None) -> dict[str, Any]:
        signal = get_execution_signal()
        if self.closed or self.human or self.active or (signal and signal.cancelled):
            return failure("桌面关闭、被占用、由用户接管或执行已停止；请先查询 operation_status",
                           status="not_started", control=self.state())
        if not runtime_ready():
            return failure(install_hint(), status="not_started", missing_runtime=True)
        active = ActiveAction(uuid.uuid4().hex, signal.scope if signal else request_trace().get("scope", ""),
                              signal.run_id if signal else "")
        self.active = active
        self.revision += 1
        task = asyncio.create_task(self._perform(active, action, params, observer))
        cancelled = False
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
                    active.cancel(signal.reason if signal and signal.cancelled else "调用已取消或超时")
            result = task.result()
            if cancelled:
                raise ToolCancelled(json.dumps(result, ensure_ascii=False))
            return result
        finally:
            self.active = None
            self.revision += 1
            active.done.set()

    async def _perform(self, active: ActiveAction, action: str, params: dict[str, Any],
                       observer: Observer | None) -> dict[str, Any]:
        signal = get_execution_signal()

        async def watch() -> None:
            if signal is not None:
                await signal.wait()
                active.cancel(signal.reason or "执行已停止")

        def update(data: dict[str, Any]) -> None:
            active.progress.update(data)
            if data.get("takeover"):
                self.human = True
                active.cancel("检测到用户输入，已接管桌面")

        watcher = asyncio.create_task(watch())
        timer = asyncio.get_running_loop().call_later(
            get_config_int("operation_action_timeout_seconds", 30), active.cancel, "桌面动作超时")
        active.phase = "running"
        try:
            try:
                raw = await run_worker(action, params, stop=active.stop, update=update,
                                       takeover=get_config_bool("operation_user_takeover", True))
            except Exception as exc:
                raw = {"status": "unknown", "input_released": False, "error": str(exc)[:300]}
            if not raw.get("input_released"):
                try:
                    released = await asyncio.wait_for(run_worker(
                        "release", {key: active.progress.get(key, []) for key in ("held_keys", "held_buttons")},
                        stop=asyncio.Event(), update=lambda _: None, takeover=False), 2)
                    raw["input_released"] = released.get("input_released", False)
                except Exception:
                    raw["input_released"] = False
            status = "cancelled" if active.stop.is_set() else raw.get("status", "unknown")
            result: dict[str, Any] = {
                "ok": status == "completed", "status": status, "operation_id": active.operation_id,
                "completed": active.progress["completed"], "total": active.progress["total"],
                "process_stopped": True, "input_released": bool(raw.get("input_released")),
                "input_outcome": raw.get("input_outcome", raw.get("status", "unknown")), "verification": "skipped",
            }
            if not result["input_released"]:
                self.human = True
            if not result["ok"]:
                cause = (ErrorCause.TIMEOUT if active.reason == "桌面动作超时" else
                         ErrorCause.USER_CANCEL if active.stop.is_set() else ErrorCause(raw.get("cause", "internal")))
                result.update(failure(active.reason or raw.get("error") or "桌面执行结果未知",
                                      cause=cause,
                                      hint="先看屏核对部分结果，再由 AI 决定后续动作；不要直接重放"))
            else:
                result["result"] = "输入步骤已发出；目标应用是否达到预期需看屏确认"
            if observer and result["ok"] and not active.stop.is_set():
                active.phase = "verifying"
                observing = asyncio.ensure_future(observer(result))
                stopping = asyncio.create_task(active.stop.wait())
                try:
                    completed, _ = await asyncio.wait({observing, stopping}, return_when=asyncio.FIRST_COMPLETED)
                    if observing in completed:
                        result = observing.result()
                    else:
                        result["verification"] = "cancelled"
                finally:
                    for pending in (observing, stopping):
                        if not pending.done():
                            pending.cancel()
                    await asyncio.gather(observing, stopping, return_exceptions=True)
            if active.stop.is_set() and result.get("ok"):
                result.update(failure(active.reason, cause=ErrorCause.USER_CANCEL,
                                      status="cancelled", hint="输入可能已全部发出，先核对目标状态再继续"))
            active.receipt = result
            if self.human and active.reason:
                await self._stop_owner(active)
            return result
        except Exception as exc:
            self.human = True
            active.receipt = failure(f"桌面执行故障: {type(exc).__name__}: {exc}",
                                     cause=ErrorCause.INTERNAL, status="unknown", process_stopped=True,
                                     completed=active.progress["completed"], total=active.progress["total"],
                                     input_released=False, verification="skipped")
            return active.receipt
        finally:
            timer.cancel()
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)


_controller = DesktopController()


def get_controller() -> DesktopController:
    return _controller


async def run_action(action: str, params: dict[str, Any], observer: Observer | None = None) -> dict[str, Any]:
    return await get_controller().run(action, params, observer)
