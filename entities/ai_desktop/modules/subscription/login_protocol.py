"""阿里云扫码票据与百炼控制台授权；会话 Cookie 仅留在当前 HTTP 客户端。"""

from __future__ import annotations

import io
import re
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
import qrcode

ACCOUNT = "https://account.aliyun.com"
CONSOLE = "https://bailian.console.aliyun.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Origin": ACCOUNT,
           "Referer": f"{ACCOUNT}/login/login.htm"}
_QR_PARAMS = {"isTracing": "true", "useOneStepVersion": "true", "ignoreRam": "false",
              "ft": "pc_alipay_qr", "log_channel": "indep", "log_platform": "pc",
              "log_biz": "aliyun", "register_method": "mobile_reg", "login_log_entrance": "official"}


class LoginError(ValueError):
    """只向界面传递已定义的错误码，禁止回显上游响应或凭据。"""


def _data(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is False:
        raise LoginError("login_failed")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LoginError("login_failed")
    return data


class AliyunQrLogin:
    """与官方扫码页相同的申请、轮询、登录票据交换及显式授权流程。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._csrf = ""
        self._ticket = ""

    async def create(self) -> bytes:
        """申请当前会话二维码，凭据不离开服务端。"""
        response = await self._client.get(f"{ACCOUNT}/login/login.htm", params={
            "oauth_callback": f"{CONSOLE}/console-login",
        })
        response.raise_for_status()
        match = re.search(r"\bCSRF_TOKEN\s*:\s*['\"]([^'\"]{1,1024})['\"]", response.text)
        if match is None:
            raise LoginError("login_failed")
        self._csrf = match[1]
        data = _data(await self._client.post(f"{ACCOUNT}/st/apply.htm", data={
            **_QR_PARAMS, "_csrf_token_": self._csrf,
        }))
        ticket = data.get("st")
        if not isinstance(ticket, str) or not ticket or len(ticket) > 4096:
            raise LoginError("login_failed")
        self._ticket = ticket
        params = {"check_ua": "", "yst": ticket, "isTracing": "true", "ignoreRam": "false"}
        if data.get("qrShortLink") is not True:
            params.update({**_QR_PARAMS, "ft": "m_alipay_qr"})
        image = qrcode.make(f"{ACCOUNT}/login/uni_qr_login.htm?{urlencode(params)}", border=4)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    async def poll(self) -> Literal["waiting", "scanned", "confirming", "expired"]:
        """读取扫码状态；额外身份验证交由官方登录处理。"""
        response = await self._client.post(f"{ACCOUNT}/st/validate.htm", data={
            "_csrf_token_": self._csrf, "yst": self._ticket, "needLoginTicket": "1",
        })
        data = _data(response)
        state = data.get("status")
        if state in {"AUTHORIZED", "SUCCESS"}:
            return "confirming"
        if state in {"INVALID", "ILLEGAL_ST"}:
            return "expired"
        payload = response.json()
        if state == "USER_IP_CHECK_FAILED" or payload.get("ding2Ram"):
            raise LoginError("additional_verification")
        if state == "AUTHORIZING":
            return "scanned"
        if state == "UNAUTHORIZED":
            return "waiting"
        raise LoginError("login_failed")

    async def authorize(self) -> dict[str, Any]:
        """换取当前扫码账号的控制台授权，不创建模型 API Key。"""
        response = await self._client.get(f"{ACCOUNT}/login/st_login.htm", params={
            "yst": self._ticket, "oauth_callback": f"{CONSOLE}/console-login",
        })
        response.raise_for_status()
        return _data(await self._client.post("https://bailian-cs.console.aliyun.com/cli/authorize", headers={
            "Origin": CONSOLE, "Referer": f"{CONSOLE}/console-login",
        }, follow_redirects=False))
