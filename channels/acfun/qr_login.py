"""AcFun 扫码登录 — scan.acfun.cn QR 登录流程的会话管理器。

流程（对齐 AcFun 官网登录页 JS 逆向确认的端点，已实测可用）：
1. ``GET scan.acfun.cn/rest/pc-direct/qr/start?type=WEB_LOGIN``
   → {qrLoginToken, qrLoginSignature, imageData(base64 PNG), expireTime}
2. ``GET .../qr/scanResult?qrLoginToken&qrLoginSignature``（服务端长轮询，本地 8s 超时）：
   result=0 已确认（响应带新 qrLoginSignature）/ 100400002 已扫码待确认 / 其他 失效
3. 确认后 ``GET .../qr/acceptResult``（token + 新签名）→ result=0 时从 Set-Cookie 采集会话
   cookie（plain dict 形式，与 acfunsdk cookie 注入路径同构）

状态机词汇与微信扫码一致：wait / scaned / confirmed / timeout / error，前端可直接复用
微信扫码组件的轮询心智。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx

from core.log import log

_QR_BASE = "https://scan.acfun.cn/rest/pc-direct/qr"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.acfun.cn/login/",
    "Origin": "https://www.acfun.cn",
}
_SESSION_TTL = 480.0          # 会话总 TTL（二维码本身 expireTime 120s，过期由前端重取）
_SCAN_TIMEOUT = 8.0           # scanResult 本地等待（服务端长轮询 120s，本地短轮询快速反馈前端）
_RESULT_CONFIRMED = 0
_RESULT_SCANNED = 100400002   # 已扫码，等待手机确认
_RESULT_SIGN_ERROR = 21


@dataclass
class QrSession:
    """一次扫码登录会话。"""

    session_id: str
    token: str
    signature: str
    image_data: str
    created_at: float = field(default_factory=time.time)
    status: str = "wait"        # wait / scaned / confirmed / timeout / error
    error: str = ""
    cookies: Dict[str, str] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > _SESSION_TTL


class AcfunQrLoginManager:
    """扫码登录会话管理器（模块级单例，见 get_qr_manager）。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, QrSession] = {}

    def _gc(self) -> None:
        dead = [sid for sid, s in self._sessions.items() if s.expired]
        for sid in dead:
            self._sessions.pop(sid, None)

    async def start(self) -> Dict[str, Any]:
        """拉取登录二维码 → {session_id, qr_png(data URL), expire_seconds}。"""
        self._gc()
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15.0) as client:
            resp = await client.get(f"{_QR_BASE}/start", params={"type": "WEB_LOGIN"})
            data = resp.json()
        if data.get("result") != _RESULT_CONFIRMED or not data.get("qrLoginToken"):
            raise RuntimeError(f"二维码获取失败: {data.get('error_msg') or data.get('result')}")
        session = QrSession(
            session_id=uuid.uuid4().hex[:16],
            token=str(data["qrLoginToken"]),
            signature=str(data["qrLoginSignature"]),
            image_data=str(data.get("imageData") or ""),
        )
        self._sessions[session.session_id] = session
        return {
            "session_id": session.session_id,
            "qr_png": f"data:image/png;base64,{session.image_data}",
            "expire_seconds": int(data.get("expireTime", 120000)) // 1000,
        }

    async def poll(self, session_id: str) -> Dict[str, Any]:
        """推进一次扫码状态检查；确认后采集 cookie 并附 credential 返回。"""
        session = self._sessions.get(session_id)
        if session is None:
            return {"status": "error", "error": "会话不存在或已过期，请重新获取二维码"}
        if session.status in ("confirmed", "timeout", "error"):
            return self._result(session)
        if session.expired:
            session.status = "timeout"
            session.error = "二维码已过期，请重新获取"
            return self._result(session)

        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=_SCAN_TIMEOUT) as client:
                resp = await client.get(f"{_QR_BASE}/scanResult", params={
                    "qrLoginToken": session.token,
                    "qrLoginSignature": session.signature,
                })
                data = resp.json()
        except (httpx.TimeoutException, httpx.ConnectError):
            return self._result(session)  # 长轮询超时/网络抖动 → 维持当前状态
        except Exception as exc:
            log(f"AcFun扫码: scanResult 异常: {exc}", "DEBUG", tag="通道")
            return self._result(session)

        result = data.get("result")
        if result == _RESULT_SCANNED:
            session.status = "scaned"
            return self._result(session)
        if result == _RESULT_CONFIRMED:
            new_signature = str(data.get("qrLoginSignature") or session.signature)
            cookies = await self._accept(session.token, new_signature)
            if cookies is None:
                session.status = "error"
                session.error = "确认登录失败，请重试"
                return self._result(session)
            session.cookies = cookies
            session.status = "confirmed"
            return self._result(session, credential={"cookies": cookies})
        # result==21 签名错误 / 其他（二维码过期等）
        session.status = "timeout"
        session.error = str(data.get("error_msg") or "二维码已过期，请重新获取")
        return self._result(session)

    async def _accept(self, token: str, signature: str) -> Optional[Dict[str, str]]:
        """确认登录并采集会话 cookie（Set-Cookie 原文解析，与 acfunsdk cookie 注入同构）。"""
        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=15.0) as client:
                resp = await client.get(f"{_QR_BASE}/acceptResult", params={
                    "qrLoginToken": token,
                    "qrLoginSignature": signature,
                })
                data = resp.json()
                if data.get("result") != _RESULT_CONFIRMED:
                    log(f"AcFun扫码: acceptResult 被拒: {data.get('error_msg') or data.get('result')}",
                        "WARNING", tag="通道")
                    return None
                cookies: Dict[str, str] = {}
                for header in resp.headers.get_list("set-cookie"):
                    pair = header.split(";", 1)[0]
                    if "=" in pair:
                        name, value = pair.split("=", 1)
                        if value:
                            cookies[name.strip()] = value.strip()
                if not cookies:
                    # 兜底：httpx cookie jar（带域规则）
                    cookies = dict(client.cookies.items())
                return cookies or None
        except Exception as exc:
            log(f"AcFun扫码: acceptResult 异常: {exc}", "WARNING", tag="通道")
            return None

    async def discard(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _result(self, session: QrSession, credential: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {"status": session.status}
        if session.error:
            out["error"] = session.error
        if credential:
            out["credential"] = credential
        return out


_QR_MANAGER: Optional[AcfunQrLoginManager] = None


def get_qr_manager() -> AcfunQrLoginManager:
    """扫码登录管理器单例。"""
    global _QR_MANAGER
    if _QR_MANAGER is None:
        _QR_MANAGER = AcfunQrLoginManager()
    return _QR_MANAGER
