"""微信扫码登录 — iLink QR 登录流程。

两种使用方式：
- ``qr_login()``：交互式终端流程，供 ``scripts/weixin_setup.py`` 使用；
- ``QrLoginManager``：会话式流程，供 WebUI 扫码登录（start → 前端轮询 poll）。
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
import uuid
from typing import Any, Dict, Optional

from core.log import log

from .ilink_client import (
    AIOHTTP_AVAILABLE,
    EP_GET_BOT_QR,
    EP_GET_QR_STATUS,
    ILINK_BASE_URL,
    QR_TIMEOUT_MS,
    _api_get,
    _make_ssl_connector,
)
from .state import save_weixin_account


def _print_qr(scan_data: str) -> None:
    """终端 ASCII 渲染二维码，失败时降级为提示手动打开链接。"""
    try:
        import qrcode

        qr = qrcode.QRCode()
        qr.add_data(scan_data)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception as exc:
        print(f"（终端二维码渲染失败: {exc}，请直接打开上面的二维码链接）")


def _qr_png_data_url(scan_data: str) -> str:
    """生成二维码 PNG 的 data URL（WebUI 展示用）。"""
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(scan_data)
    qr.make(fit=True)
    img = qr.make_image()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# ======================================================================
# 会话式扫码登录（WebUI）
# ======================================================================

QR_SESSION_TTL_SECONDS = 480
QR_MAX_REFRESH = 3


class QrLoginSession:
    """一次扫码登录会话的状态。"""

    def __init__(self, http_session: Any, qrcode_value: str, qrcode_url: str, bot_type: str):
        self.id = uuid.uuid4().hex[:12]
        self.http_session = http_session
        self.qrcode_value = qrcode_value
        self.qrcode_url = qrcode_url
        self.bot_type = bot_type
        self.current_base_url = ILINK_BASE_URL
        self.refresh_count = 0
        self.status = "wait"  # wait/scaned/confirmed/timeout/error
        self.credential: Optional[Dict[str, str]] = None
        self.error = ""
        self.created_at = time.monotonic()

    @property
    def scan_data(self) -> str:
        # 微信必须扫完整 liteapp URL，而不是裸 hex token
        return self.qrcode_url if self.qrcode_url else self.qrcode_value

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created_at > QR_SESSION_TTL_SECONDS


class QrLoginManager:
    """管理进行中的扫码会话（模块级单例，WebUI 路由调用）。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, QrLoginSession] = {}

    def _gc(self) -> None:
        stale = [sid for sid, s in self._sessions.items() if s.expired and s.status != "confirmed"]
        for sid in stale:
            self._sessions.pop(sid, None)

    async def start(self, *, bot_type: str = "3") -> Dict[str, Any]:
        """拉取二维码，返回 {session_id, qr_png, qr_url}。"""
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp is required for Weixin QR login")
        import aiohttp

        self._gc()
        http_session = aiohttp.ClientSession(trust_env=True, connector=_make_ssl_connector())
        try:
            qr_resp = await _api_get(
                http_session,
                base_url=ILINK_BASE_URL,
                endpoint=f"{EP_GET_BOT_QR}?bot_type={bot_type}",
                timeout_ms=QR_TIMEOUT_MS,
            )
        except Exception as exc:
            await http_session.close()
            raise RuntimeError(f"获取二维码失败: {exc}") from exc

        qrcode_value = str(qr_resp.get("qrcode") or "")
        qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
        if not qrcode_value:
            await http_session.close()
            raise RuntimeError("二维码响应缺少 qrcode 字段")

        session = QrLoginSession(http_session, qrcode_value, qrcode_url, bot_type)
        self._sessions[session.id] = session
        return {
            "session_id": session.id,
            "qr_png": _qr_png_data_url(session.scan_data),
            "qr_url": qrcode_url,
        }

    async def poll(self, session_id: str) -> Dict[str, Any]:
        """推进一次扫码状态检查。

        返回 {status, qr_png?, qr_url?, account_id?, error?}：
        - wait/scaned：继续等待；二维码过期自动刷新时带新的 qr_png
        - confirmed：带 account_id，凭据已落盘
        - timeout/error：流程结束
        """
        session = self._sessions.get(session_id)
        if session is None:
            return {"status": "error", "error": "会话不存在或已过期，请重新获取二维码"}
        if session.status == "confirmed":
            return {
                "status": "confirmed",
                "account_id": (session.credential or {}).get("account_id", ""),
            }
        if session.status in {"timeout", "error"}:
            return {"status": session.status, "error": session.error}
        if session.expired:
            session.status = "timeout"
            await self._close(session)
            return {"status": "timeout", "error": "二维码已超时，请重新获取"}

        try:
            status_resp = await _api_get(
                session.http_session,
                base_url=session.current_base_url,
                endpoint=f"{EP_GET_QR_STATUS}?qrcode={session.qrcode_value}",
                timeout_ms=QR_TIMEOUT_MS,
            )
        except asyncio.TimeoutError:
            return {"status": session.status}
        except Exception as exc:
            log(f"微信: WebUI 二维码状态轮询异常: {exc}", "WARNING", tag="通道")
            return {"status": session.status}

        status = str(status_resp.get("status") or "wait")
        if status == "scaned":
            session.status = "scaned"
        elif status == "scaned_but_redirect":
            redirect_host = str(status_resp.get("redirect_host") or "")
            if redirect_host:
                session.current_base_url = f"https://{redirect_host}"
        elif status == "expired":
            session.refresh_count += 1
            if session.refresh_count > QR_MAX_REFRESH:
                session.status = "error"
                session.error = "二维码多次过期，请重新获取"
                await self._close(session)
                return {"status": "error", "error": session.error}
            # 自动刷新二维码并在本次响应中带回新图
            try:
                qr_resp = await _api_get(
                    session.http_session,
                    base_url=ILINK_BASE_URL,
                    endpoint=f"{EP_GET_BOT_QR}?bot_type={session.bot_type}",
                    timeout_ms=QR_TIMEOUT_MS,
                )
                session.qrcode_value = str(qr_resp.get("qrcode") or "")
                session.qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
                session.status = "wait"
                return {
                    "status": "wait",
                    "qr_png": _qr_png_data_url(session.scan_data),
                    "qr_url": session.qrcode_url,
                    "refreshed": True,
                }
            except Exception as exc:
                session.status = "error"
                session.error = f"二维码刷新失败: {exc}"
                await self._close(session)
                return {"status": "error", "error": session.error}
        elif status == "confirmed":
            account_id = str(status_resp.get("ilink_bot_id") or "")
            token = str(status_resp.get("bot_token") or "")
            base_url = str(status_resp.get("baseurl") or ILINK_BASE_URL)
            user_id = str(status_resp.get("ilink_user_id") or "")
            if not account_id or not token:
                session.status = "error"
                session.error = "扫码确认成功但凭据不完整"
                await self._close(session)
                return {"status": "error", "error": session.error}
            save_weixin_account(
                account_id=account_id,
                token=token,
                base_url=base_url,
                user_id=user_id,
            )
            session.credential = {
                "account_id": account_id,
                "token": token,
                "base_url": base_url,
                "user_id": user_id,
            }
            session.status = "confirmed"
            log(f"微信: WebUI 扫码登录成功 account={account_id[:8]}", tag="通道")
            await self._close(session)
            return {"status": "confirmed", "account_id": account_id, "credential": session.credential}

        return {"status": session.status}

    async def _close(self, session: QrLoginSession) -> None:
        try:
            if not session.http_session.closed:
                await session.http_session.close()
        except Exception:
            pass

    async def discard(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            await self._close(session)


_qr_manager: Optional[QrLoginManager] = None


def get_qr_manager() -> QrLoginManager:
    global _qr_manager
    if _qr_manager is None:
        _qr_manager = QrLoginManager()
    return _qr_manager


async def qr_login(
    *,
    bot_type: str = "3",
    timeout_seconds: int = 480,
) -> Optional[Dict[str, str]]:
    """执行交互式 iLink 扫码登录。

    成功返回凭据 dict（account_id/token/base_url/user_id），失败或超时返回 None。
    """
    if not AIOHTTP_AVAILABLE:
        raise RuntimeError("aiohttp is required for Weixin QR login")
    import aiohttp

    async with aiohttp.ClientSession(trust_env=True, connector=_make_ssl_connector()) as session:
        try:
            qr_resp = await _api_get(
                session,
                base_url=ILINK_BASE_URL,
                endpoint=f"{EP_GET_BOT_QR}?bot_type={bot_type}",
                timeout_ms=QR_TIMEOUT_MS,
            )
        except Exception as exc:
            log(f"微信: 获取二维码失败: {exc}", "ERROR", tag="通道")
            return None

        qrcode_value = str(qr_resp.get("qrcode") or "")
        qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
        if not qrcode_value:
            log("微信: 二维码响应缺少 qrcode 字段", "ERROR", tag="通道")
            return None

        # qrcode_url 是完整可扫的 liteapp URL；qrcode_value 只是 hex token。
        # 微信必须扫完整 URL，而不是裸 hex 字符串。
        qr_scan_data = qrcode_url if qrcode_url else qrcode_value

        print("\n请使用微信扫描以下二维码：")
        if qrcode_url:
            print(qrcode_url)
        _print_qr(qr_scan_data)

        deadline = time.monotonic() + timeout_seconds
        current_base_url = ILINK_BASE_URL
        refresh_count = 0

        while time.monotonic() < deadline:
            try:
                status_resp = await _api_get(
                    session,
                    base_url=current_base_url,
                    endpoint=f"{EP_GET_QR_STATUS}?qrcode={qrcode_value}",
                    timeout_ms=QR_TIMEOUT_MS,
                )
            except asyncio.TimeoutError:
                await asyncio.sleep(1)
                continue
            except Exception as exc:
                log(f"微信: 二维码状态轮询异常: {exc}", "WARNING", tag="通道")
                await asyncio.sleep(1)
                continue

            status = str(status_resp.get("status") or "wait")
            if status == "wait":
                print(".", end="", flush=True)
            elif status == "scaned":
                print("\n已扫码，请在微信里确认...")
            elif status == "scaned_but_redirect":
                redirect_host = str(status_resp.get("redirect_host") or "")
                if redirect_host:
                    current_base_url = f"https://{redirect_host}"
            elif status == "expired":
                refresh_count += 1
                if refresh_count > 3:
                    print("\n二维码多次过期，请重新执行登录。")
                    return None
                print(f"\n二维码已过期，正在刷新... ({refresh_count}/3)")
                try:
                    qr_resp = await _api_get(
                        session,
                        base_url=ILINK_BASE_URL,
                        endpoint=f"{EP_GET_BOT_QR}?bot_type={bot_type}",
                        timeout_ms=QR_TIMEOUT_MS,
                    )
                    qrcode_value = str(qr_resp.get("qrcode") or "")
                    qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
                    qr_scan_data = qrcode_url if qrcode_url else qrcode_value
                    if qrcode_url:
                        print(qrcode_url)
                    _print_qr(qr_scan_data)
                except Exception as exc:
                    log(f"微信: 二维码刷新失败: {exc}", "ERROR", tag="通道")
                    return None
            elif status == "confirmed":
                account_id = str(status_resp.get("ilink_bot_id") or "")
                token = str(status_resp.get("bot_token") or "")
                base_url = str(status_resp.get("baseurl") or ILINK_BASE_URL)
                user_id = str(status_resp.get("ilink_user_id") or "")
                if not account_id or not token:
                    log("微信: 扫码确认成功但凭据不完整", "ERROR", tag="通道")
                    return None
                save_weixin_account(
                    account_id=account_id,
                    token=token,
                    base_url=base_url,
                    user_id=user_id,
                )
                print(f"\n微信连接成功，account_id={account_id}")
                return {
                    "account_id": account_id,
                    "token": token,
                    "base_url": base_url,
                    "user_id": user_id,
                }
            await asyncio.sleep(1)

        print("\n微信登录超时。")
        return None
