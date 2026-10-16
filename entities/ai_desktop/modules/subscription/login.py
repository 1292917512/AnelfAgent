"""按需获取官方扫码画面与授权状态，不向模型或 Web 返回凭据。"""

from __future__ import annotations

import asyncio
import importlib
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from core.lifecycle import Lifecycle
from core.log import log

from .bailian import cli_credential, save_console_session
from .browser_runtime import open_browser

_ORIGIN = "https://bailian.console.aliyun.com"
_WIDTH, _HEIGHT = 900, 720
_TTL = 600


class ConsoleLogin:
    """单个有界授权会话，关闭、超时及热卸载均回收隔离浏览器。"""

    def __init__(self, on_complete: Callable[[], Awaitable[None]]) -> None:
        self._on_complete = on_complete
        self._task: asyncio.Task[None] | None = None
        self._page: Any = None
        self._qr: Any = None
        self._lock = asyncio.Lock()
        self._id = ""
        self._state = "idle"
        self._error = ""
        self._expires_at = 0.0

    def status(self, session_id: str) -> dict[str, Any]:
        """只返回会话状态；不包含授权 URL、Cookie 或 token。"""
        if not self._id or not secrets.compare_digest(self._id, session_id):
            raise LookupError("登录会话不存在")
        return {"id": self._id, "state": self._state, "error": self._error,
                "expires_at": self._expires_at}

    async def start(self) -> dict[str, Any]:
        """复用仍在等待的会话；仅显式请求才启动浏览器。"""
        async with self._lock:
            if self._task and not self._task.done():
                return self.status(self._id)
            if not cli_credential()[0]:
                raise ValueError("请先安装 Bailian CLI")
            self._id = secrets.token_urlsafe(24)
            self._state, self._error = "starting", ""
            self._expires_at = time.time() + _TTL
            self._task = asyncio.create_task(self._run())
            return self.status(self._id)

    async def _run(self) -> None:
        """通过官方授权页捕获当前会话回调，保存后刷新额度。"""
        done = asyncio.Event()
        receiving = False
        callback = f"http://127.0.0.1:38999/callback?state={secrets.token_urlsafe(32)}"

        async def receive(route: Any) -> None:
            nonlocal receiving
            request = route.request
            headers = {"Access-Control-Allow-Origin": _ORIGIN,
                       "Access-Control-Allow-Headers": "Content-Type",
                       "Access-Control-Allow-Methods": "POST, OPTIONS"}
            if request.headers.get("origin") != _ORIGIN:
                await route.abort()
                return
            if request.method == "OPTIONS":
                await route.fulfill(status=204, headers=headers)
                return
            raw = request.post_data or ""
            if request.method != "POST" or len(raw.encode()) > 65536 or receiving or done.is_set():
                await route.abort()
                return
            receiving = True
            try:
                payload = request.post_data_json
                if not isinstance(payload, dict):
                    raise ValueError("控制台授权数据无效")
                await asyncio.to_thread(save_console_session, payload)
            except (ValueError, OSError):
                self._error = "无法保存控制台授权，请重试并检查配置目录权限"
                await route.fulfill(status=400, headers=headers)
                done.set()
                return
            self._state = "refreshing"
            await route.fulfill(status=200, body="OK", headers=headers)
            done.set()

        try:
            factory = importlib.import_module("playwright.async_api").async_playwright
            async with factory() as playwright:
                browser = await open_browser(playwright, self._preparing)
                try:
                    context = await browser.new_context(
                        viewport={"width": _WIDTH, "height": _HEIGHT}, locale="zh-CN", device_scale_factor=2,
                    )
                    await context.route(callback, receive)
                    self._page = await context.new_page()
                    async with asyncio.timeout(_TTL):
                        self._state = "starting"
                        await self._page.goto(
                            f"{_ORIGIN}/console-login?notice={callback.removeprefix('http://')}",
                            wait_until="domcontentloaded", timeout=30000,
                        )
                        await self._page.get_by_role("button", name="前往登录", exact=True).click(timeout=30000)
                        self._qr = self._page.locator(".alipay-qrcode-wrapper")
                        await self._qr.locator("img.alipay-qrcode").wait_for(state="visible", timeout=30000)
                        self._expires_at = time.time() + _TTL
                        self._state = "waiting"
                        await self._page.get_by_role("button", name="同意授权 CLI 登录", exact=True).wait_for(
                            state="visible", timeout=_TTL * 1000,
                        )
                        self._qr = None
                        self._state = "confirming"
                        await done.wait()
                finally:
                    self._page = self._qr = None
                    await browser.close()
                if self._error:
                    self._state = "error"
                else:
                    self._state = "refreshing"
                    await self._on_complete()
                    self._state = "complete"
        except TimeoutError:
            self._state, self._error = ("error", "browser_prepare_failed") if self._state == "preparing" else ("expired", "")
        except asyncio.CancelledError:
            self._state = "cancelled"
            raise
        except Exception as exc:
            log(f"百炼扫码登录失败（阶段 {self._state}，类型 {type(exc).__name__}）", "WARNING")
            preparing = self._state == "preparing"
            self._state = "error"
            self._error = "browser_prepare_failed" if preparing else "login_failed"
        finally:
            self._page = self._qr = None

    def _preparing(self) -> None:
        """向界面报告首次浏览器准备，不占用扫码有效期。"""
        self._state, self._expires_at = "preparing", 0

    async def image(self, session_id: str) -> bytes:
        """只返回官方二维码及扫码状态区域，不展示整页登录表单。"""
        from playwright.async_api import Error

        async with self._lock:
            self.status(session_id)
            if self._state != "waiting" or self._qr is None:
                raise ValueError("登录页面尚未就绪或已关闭")
            try:
                return bytes(await self._qr.screenshot(type="png", timeout=1500))
            except Error as exc:
                raise ValueError("扫码页面正在切换，请刷新登录状态") from exc

    async def confirm(self, session_id: str) -> None:
        """用户确认后点击官方授权按钮，不接受任意页面操作。"""
        from playwright.async_api import Error

        async with self._lock:
            self.status(session_id)
            if self._state != "confirming" or self._page is None:
                raise ValueError("请先扫码并在手机确认登录")
            try:
                await self._page.get_by_role("button", name="同意授权 CLI 登录", exact=True).click(timeout=5000)
            except Error as exc:
                if self._state not in ("refreshing", "complete"):
                    raise ValueError("官方授权暂时无法完成，请重试") from exc

    async def close(self, session_id: str | None = None) -> None:
        """取消未完成的登录并等待浏览器资源回收。"""
        async with self._lock:
            if session_id is not None:
                self.status(session_id)
            if self._task and not self._task.done():
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


def get_console_login(on_complete: Callable[[], Awaitable[None]]) -> ConsoleLogin:
    """通过宿主生命周期管理组件授权会话。"""
    service = Lifecycle.get("ai_desktop_subscription_login")
    if not isinstance(service, ConsoleLogin):
        service = ConsoleLogin(on_complete)
        Lifecycle.register("ai_desktop_subscription_login", service, cleanup=service.close)
    return service
