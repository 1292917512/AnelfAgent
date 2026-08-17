"""本地直连提供者：本机抓取 + 正文提取（网页读取能力，无需凭据）。

实现底座在 entities/web/fetcher.py（SSRF 防护 / robots 合规 / 正文提取管线）。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities.web.providers.base import Provider


class BuiltinProvider(Provider):
    """本地直连网页读取。"""

    name = "builtin"
    display_name = "本地直连"
    description = "本机直接抓取 + 正文提取（BS4/Readability），无需凭据"
    key_hint = ""
    requires_credential = False

    def credential(self) -> Tuple[str, str]:
        return "", ""

    def set_api_key(self, api_key: str) -> None:
        raise NotImplementedError("本地直连无需凭据")

    def read(
        self,
        url: str,
        *,
        timeout: int = 15,
        extract_mode: str = "markdown",
        use_proxy: bool = False,
        respect_robots: bool = False,
    ) -> Dict[str, Any]:
        from entities.web.fetcher import read_page
        return read_page(
            url, timeout=timeout, extract_mode=extract_mode,
            use_proxy=use_proxy, respect_robots=respect_robots,
        )

    def error_response(self, exc: Exception, action: str, hint: str = "") -> str:
        from entities.web.fetcher import RobotsDisallowed
        if isinstance(exc, RobotsDisallowed):
            return tool_error(
                str(exc),
                cause=ErrorCause.PERMISSION, retryable=False,
                robots_disallowed=True,
                hint="如需强制抓取可传 respect_robots=false，请自行确认合规性",
            )
        return error_from_exception(exc, action=action, hint=hint or None)
