"""本地运行控制：所有启动入口发布同一份状态并复用宿主关停流程。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable

from core.host_protocol import write_json
from core.lifecycle import Lifecycle
from core.log import log


class HostControl:
    """以每次进程独有令牌接受命令，重启由组合根注入既有运维入口。"""

    def __init__(self, path: Path, token: str, *, connection: Path | None = None,
                 restart: Callable[[], dict[str, Any]] | None = None,
                 web_url: Callable[[], str] | None = None) -> None:
        self.path = path.resolve()
        self.token = token
        self.connection = connection.resolve() if connection is not None else None
        self.restart = restart
        self.web_url = web_url
        self._task: asyncio.Task[None] | None = None
        self._started_at = time.time()
        self._phase = "starting"
        self._published_at = 0.0

    def publish(self, phase: str) -> None:
        """原子发布运行态，供 Electron 与管理网页共同观测。"""
        self._phase = phase
        if self.connection is not None:
            value = {
                "pid": os.getpid(), "token": self.token, "control_file": str(self.path),
                "phase": phase, "updated_at": time.time(), "started_at": self._started_at,
                "web_url": self.web_url() if self.web_url is not None else "",
            }
            # The PID-specific record survives another process overwriting the shared pointer.
            record = self.connection.with_name(f"{self.connection.stem}-{os.getpid()}{self.connection.suffix}")
            write_json(record, value)
            record.chmod(0o600)
            write_json(self.connection, value)
            self.connection.chmod(0o600)
            self._published_at = time.monotonic()

    def start(self) -> None:
        self.publish("starting")
        self._task = asyncio.create_task(self._watch(), name="host.control")

    def quiesce(self) -> None:
        self.publish("restarting" if Lifecycle.restart_requested() else "stopping")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.publish("restarting" if Lifecycle.restart_requested() else "stopped")
        self.path.unlink(missing_ok=True)
        if self.connection is not None:
            self.connection.with_name(f"{self.connection.stem}-{os.getpid()}{self.connection.suffix}").unlink(missing_ok=True)

    async def _watch(self) -> None:
        announced = False
        while True:
            try:
                if not announced and self._phase == "starting" and Lifecycle.started():
                    self.publish("running")
                    announced = True
                elif time.monotonic() - self._published_at >= 2:
                    self.publish(self._phase)
                if self.path.exists():
                    request = json.loads(self.path.read_text("utf-8"))
                    if (isinstance(request, dict) and request.get("token") == self.token
                            and request.get("pid") == os.getpid()):
                        self.path.unlink(missing_ok=True)
                        result: dict[str, Any]
                        if request.get("action") == "stop":
                            self.publish("stopping")
                            Lifecycle.request_shutdown()
                            result = {"ok": True}
                        elif request.get("action") == "restart" and self.restart is not None:
                            result = self.restart()
                            if result.get("ok"):
                                self.publish("restarting")
                        else:
                            result = {"ok": False, "message": "Unsupported host action"}
                        write_json(self.path.with_suffix(".result.json"), {**result, "id": request.get("id")})
            except (OSError, ValueError) as exc:
                log(f"宿主控制信箱暂不可读: {exc}", "DEBUG", tag="宿主")
            await asyncio.sleep(0.25)


def register_host_control(root: Path, *, restart: Callable[[], dict[str, Any]],
                          web_url: Callable[[], str]) -> None:
    """应用实例持锁后注册；直接 start 与 APP 启动都使用同一控制面。"""
    state = root / "logs" / "manager"
    service = HostControl(state / f"control-{os.getpid()}-{secrets.token_hex(8)}.json", secrets.token_hex(32),
                          connection=state / "runtime.json", restart=restart, web_url=web_url)
    service.publish("starting")
    Lifecycle.register("host_control", service, on_start=service.start, cleanup=service.stop,
                       on_quiesce=service.quiesce)
