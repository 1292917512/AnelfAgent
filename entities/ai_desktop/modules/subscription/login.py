"""有界扫码授权会话；仅向 Web 返回二维码及状态，不向模型注入凭据。"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from core.lifecycle import Lifecycle
from core.log import log
from entities._sdk import async_network_client

from .bailian import cli_credential, save_console_session
from .login_protocol import HEADERS, AliyunQrLogin, LoginError

_TTL = 600
_POLL_INTERVAL = 2


class ConsoleLogin:
    """一次扫码对应一个隔离 HTTP 会话，关闭、到期及热卸载均回收。"""

    def __init__(self, on_complete: Callable[[], Awaitable[None]]) -> None:
        self._on_complete = on_complete
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._confirm = asyncio.Event()
        self._image = b""
        self._id = ""
        self._state = "idle"
        self._error = ""
        self._expires_at = 0.0

    def status(self, session_id: str) -> dict[str, Any]:
        """只返回状态，不包含授权 URL、Cookie 或 token。"""
        if not self._id or not secrets.compare_digest(self._id, session_id):
            raise LookupError("登录会话不存在")
        return {"id": self._id, "state": self._state, "error": self._error,
                "expires_at": self._expires_at}

    async def start(self) -> dict[str, Any]:
        """复用仍在等待的扫码会话，终态后再次点击才申请新二维码。"""
        async with self._lock:
            if self._task and not self._task.done():
                return self.status(self._id)
            if not cli_credential()[0]:
                raise ValueError("请先安装 Bailian CLI")
            self._id = secrets.token_urlsafe(24)
            self._state, self._error, self._image = "starting", "", b""
            self._expires_at = time.time() + _TTL
            self._confirm.clear()
            self._task = asyncio.create_task(self._run())
            return self.status(self._id)

    async def _run(self) -> None:
        """申请二维码、等待手机确认与界面授权，保存成功后刷新额度。"""
        try:
            async with asyncio.timeout(_TTL), async_network_client(
                headers=HEADERS, timeout=20, follow_redirects=True,
            ) as client:
                protocol = AliyunQrLogin(client)
                self._image = await protocol.create()
                self._state = "waiting"
                failures = 0
                while self._state in {"waiting", "scanned"}:
                    await asyncio.sleep(_POLL_INTERVAL)
                    try:
                        self._state = await protocol.poll()
                        failures = 0
                    except httpx.TransportError:
                        failures += 1
                        if failures >= 3:
                            raise
                self._image = b""
                if self._state == "expired":
                    return
                await self._confirm.wait()
                payload = await protocol.authorize()
                save_console_session(payload)
            self._state = "refreshing"
            try:
                await self._on_complete()
            except Exception as exc:
                log(f"百炼授权已保存，额度刷新失败（{type(exc).__name__}）", "WARNING")
                self._error = "quota_refresh_failed"
            self._state = "complete"
        except TimeoutError:
            self._state = "expired"
        except asyncio.CancelledError:
            self._state = "cancelled"
            raise
        except Exception as exc:
            log(f"百炼扫码登录失败（阶段 {self._state}，类型 {type(exc).__name__}）", "WARNING")
            self._state = "error"
            self._error = str(exc) if isinstance(exc, LoginError) else "login_failed"
        finally:
            self._image = b""

    async def image(self, session_id: str) -> bytes:
        """返回当前二维码，不重复生成、不展示账号登录页面。"""
        self.status(session_id)
        if not self._image or self._state not in {"waiting", "scanned"}:
            raise ValueError("二维码尚未就绪或已过期")
        return self._image

    async def confirm(self, session_id: str) -> None:
        """手机确认登录后，由用户显式授权百炼控制台访问。"""
        self.status(session_id)
        if self._state != "confirming":
            raise ValueError("请先扫码并在手机确认登录")
        self._state = "authorizing"
        self._confirm.set()

    async def close(self, session_id: str | None = None) -> None:
        """取消未完成会话并等待网络资源回收。"""
        async with self._lock:
            if session_id is not None:
                self.status(session_id)
            if self._task and not self._task.done():
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self._state, self._image = "cancelled", b""
            self._task = None


def get_console_login(on_complete: Callable[[], Awaitable[None]]) -> ConsoleLogin:
    """通过宿主生命周期管理组件授权会话。"""
    service = Lifecycle.get("ai_desktop_subscription_login")
    if not isinstance(service, ConsoleLogin):
        service = ConsoleLogin(on_complete)
        Lifecycle.register("ai_desktop_subscription_login", service, cleanup=service.close)
    return service
