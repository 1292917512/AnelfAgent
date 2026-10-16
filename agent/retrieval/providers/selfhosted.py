"""自托管提供者的配置与有界 HTTP 传输。

模型可见：服务错误包含提供者、状态码、脱敏摘要及实际服务连接路径。
Token 影响：仅失败或工具结果增加少量诊断字段，不注入服务端原始大响应。
缓存影响：仅工具结果，不修改稳定上下文前缀。
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from core.config import ConfigManager
from core.network import ProxyMode, network_client
from core.sanitizer import sanitize_text
from core.tool_errors import ErrorCause, tool_error

from .base import Provider


def validate_service_url(value: str) -> str:
    """校验由管理员配置的服务根地址，允许私网与子路径，禁止内嵌凭据。"""
    value = value.strip().rstrip("/")
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError
        _ = parts.port
    except ValueError:
        raise ValueError("服务地址须为 http(s)://host[:port][/path]，不含凭据、查询参数或片段") from None
    return value


class ServiceError(Exception):
    """区分服务响应异常与目标网页失败，并携带有限诊断信息。"""

    def __init__(self, message: str, *, code: str, hint: str = "", **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.details = details


class SelfHostedProvider(Provider):
    """配置驱动的本地服务适配基类；不拥有服务进程或浏览器生命周期。"""

    def setting(self, suffix: str, default: Any = "") -> Any:
        return ConfigManager.get(f"retrieval_{self.name}_{suffix}", default)

    def credential(self) -> tuple[str, str]:
        key = str(self.setting("api_key") or "").strip()
        return (key, "config") if key else ("", "")

    def set_api_key(self, api_key: str) -> None:
        from agent.retrieval.config import set_provider_key
        set_provider_key(self.name, api_key)

    def configured(self) -> bool:
        try:
            endpoint = validate_service_url(str(self.setting("base_url") or ""))
        except ValueError:
            return False
        return bool(endpoint) and (not self.requires_credential or bool(self.credential()[0]))

    def request_json(
        self, path: str, *, method: str = "GET", params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None, timeout: float = 30,
        proxy_mode: ProxyMode | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """请求受信服务地址，限制响应体且禁止携带凭据跟随重定向。"""
        endpoint = validate_service_url(str(self.setting("base_url") or ""))
        if not endpoint:
            raise ValueError(f"请配置 {self.config_group} 的服务地址")
        key = self.credential()[0]
        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        mode = proxy_mode if proxy_mode is not None else self.setting("proxy_mode", "direct")
        with network_client(proxy_mode=mode, timeout=timeout, follow_redirects=False) as client:
            with client.stream(method, f"{endpoint}/{path}", params=params, json=payload, headers=headers) as response:
                route = dict(response.extensions.get("network_route") or {})
                limit = 8 * 1024 * 1024
                buf = bytearray()
                for chunk in response.iter_bytes(65536):
                    buf.extend(chunk)
                    if len(buf) > limit:
                        raise ServiceError("服务响应超过 8 MiB 上限，请缩小抓取区域", code="RESPONSE_TOO_LARGE", service_route=route)
                body = bytes(buf).decode("utf-8", errors="replace")
                if not 200 <= response.status_code < 300:
                    detail = body.replace(key, "[redacted]") if key else body
                    raise ServiceError(
                        f"{self.display_name} 服务返回 HTTP {response.status_code}",
                        code=f"SERVICE_HTTP_{response.status_code}",
                        hint=self.key_hint,
                        status_code=response.status_code, service_route=route,
                        body=sanitize_text(detail)[:500],
                    )
        try:
            result = json.loads(body)
        except (ValueError, TypeError):
            raise ServiceError("服务未返回 JSON，请检查地址是否指向 API", code="INVALID_RESPONSE") from None
        if not isinstance(result, dict):
            raise ServiceError("服务响应必须为 JSON 对象", code="INVALID_RESPONSE")
        return result, route

    def error_response(self, exc: Exception, action: str, hint: str = "") -> str:
        if isinstance(exc, ServiceError):
            return tool_error(
                sanitize_text(str(exc)), cause=ErrorCause.NETWORK,
                code=exc.code, retryable=False, hint=hint or exc.hint or self.key_hint,
                provider=self.name, **exc.details,
            )
        return super().error_response(exc, action, hint or self.key_hint)
