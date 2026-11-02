"""QQ OpenAPI，复用网络连接池与令牌，只有明确的鉴权拒绝允许重新发送请求。"""

import asyncio
import time
from typing import Any

import httpx

from core.sanitizer import sanitize_text

API = "https://api.bot.qq.com"


class QQAPIError(RuntimeError):
    """平台明确返回的错误码和追踪 ID，供调用点决定能否安全重试。"""

    def __init__(self, status: int, code: str, trace: str, detail: str) -> None:
        super().__init__(f"QQ API HTTP {status}, err_code={code or '—'}, trace={trace or '—'} {detail}")
        self.code = code
        self.status = status
        self.trace_id = trace


class DeliveryUnconfirmed(RuntimeError):
    """平台可能已经受理消息，不能自动重复投递。"""


class QQAPI:
    def __init__(self, http: httpx.AsyncClient, app_id: str, secret: str) -> None:
        self.http = http
        self.app_id = app_id
        self._secret = secret
        self._token = ""
        self._refresh_at = 0.0
        self._lock = asyncio.Lock()

    async def token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._refresh_at:
                return self._token
            response = await self.http.post(f"{API}/app/getAppAccessToken", json={
                "appId": self.app_id, "clientSecret": self._secret,
            })
            data = self._result(response, auth=True)
            if not isinstance(data, dict):
                raise RuntimeError("QQ 令牌响应格式错误")
            token = data.get("access_token")
            ttl = int(data.get("expires_in") or 0)
            if not isinstance(token, str) or not token or ttl <= 0:
                raise RuntimeError("QQ 未返回有效的 Access Token 或有效期")
            self._token = token
            self._refresh_at = time.monotonic() + ttl - min(45, ttl / 2)
            return token

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        result = await self._request(method, path, **kwargs)
        if not isinstance(result, dict):
            raise ValueError("QQ 接口未返回预期的 JSON 对象")
        return result

    async def get_list(self, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        result = await self._request("GET", path, **kwargs)
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise ValueError("QQ 列表接口未返回对象数组")
        return result

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        for attempt in range(2):
            token = await self.token()
            response = await self.http.request(method, API + path, headers={
                "Authorization": f"QQBot {token}", "X-Union-Appid": self.app_id,
            }, **kwargs)
            if not attempt and response.status_code == 401:
                if self._token == token:
                    self._refresh_at = 0.0
                continue
            try:
                return self._result(response)
            except QQAPIError as exc:
                if method != "GET" or attempt or exc.code not in {
                    "11242", "11252", "11263", "11281", "306003", "306006", "501004",
                }:
                    raise
        raise AssertionError("unreachable")

    def _result(self, response: httpx.Response, *, auth: bool = False) -> dict[str, Any] | list[Any]:
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = None
        obj = data if isinstance(data, dict) else {}
        code = str(obj.get("err_code", obj.get("code", "")))
        trace = response.headers.get("X-Tps-trace-ID") or str(obj.get("trace_id") or "")
        nested = obj.get("data")
        audit = nested.get("message_audit") if isinstance(nested, dict) else None
        if code in {"304023", "304024"} or isinstance(audit, dict) and audit.get("audit_id"):
            audit_id = audit.get("audit_id") if isinstance(audit, dict) else ""
            raise DeliveryUnconfirmed(f"QQ 消息等待审核 (err_code={code}, audit_id={audit_id}, trace={trace})")
        if response.status_code in (201, 202) and code in ("", "0"):
            raise DeliveryUnconfirmed(f"QQ 操作异步受理 (HTTP {response.status_code}, trace={trace})")
        if response.is_success and isinstance(data, (dict, list)) and code in ("", "0"):
            return data
        detail = "" if auth else str(obj.get("message") or "")
        for credential in (self._secret, self._token):
            if credential:
                detail = detail.replace(credential, "***")
        raise QQAPIError(response.status_code, code, trace, sanitize_text(detail)[:240])
