"""GitHub REST 薄客户端 — httpx 封装:鉴权 / ETag 条件请求 / 限流退避 / 5xx 重试。

设计(参照 nonebot-adapter-github 的 githubkit 委托思想,但不引入重依赖):
- 只实现频道所需的端点面,schema 靠少量手写解析,不拖 pydantic 模型全家桶;
- 鉴权三形态:PAT(Bearer)/ GitHub App(installation token,见 app_auth)/ 匿名;
- ETag 条件请求:``If-None-Match`` 命中 304 **不计入限流配额**,是高频轮询的预算根基;
- 限流:响应头 ``X-RateLimit-Remaining/Reset`` 全程跟踪;403/429 抛 RateLimitError
  由调用方长退避;5xx 指数退避 (n+1)² 最多重试 3 次(githubkit RetryServerError 语义)。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import httpx

from core.log import log

_DEFAULT_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "AnelfAgent-GitHubChannel",
}


class GitHubApiError(Exception):
    """GitHub API 错误基类。"""

    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class AuthError(GitHubApiError):
    """401/403(非限流):凭据失效或权限不足。"""


class NotFoundError(GitHubApiError):
    """404:仓库不存在或对 token 不可见。"""


class RateLimitError(GitHubApiError):
    """403/429 限流:携带 retry_after 秒数(调用方据此长退避)。"""

    def __init__(self, message: str, *, retry_after: float = 60.0, reset_at: float = 0.0) -> None:
        super().__init__(message, status=429)
        self.retry_after = retry_after
        self.reset_at = reset_at


@dataclass
class ApiResponse:
    """统一响应:数据 + ETag + 限流余量(调用方需要的全部元信息)。"""

    status: int
    data: Any
    etag: str = ""
    not_modified: bool = False
    rate_remaining: int = -1
    rate_reset: float = 0.0


class GitHubClient:
    """薄客户端。token_provider 每次请求时取新值(凭据热更免重启)。"""

    def __init__(
        self,
        *,
        base_url: str = "https://api.github.com",
        auth_mode: str = "none",
        token_provider: Optional[Callable[[], str]] = None,
        app_auth: Any = None,
        timeout: float = 15.0,
        max_retries: int = 3,
        transport: Any = None,
    ) -> None:
        self._auth_mode = auth_mode
        self._token_provider = token_provider
        self._app_auth = app_auth
        self._max_retries = max(1, max_retries)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=_DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self.rate_remaining: int = -1
        self.rate_reset_at: float = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # 鉴权
    # ------------------------------------------------------------------

    async def _auth_header(self) -> Dict[str, str]:
        if self._auth_mode == "pat":
            token = self._token_provider() if self._token_provider else ""
            return {"Authorization": f"Bearer {token}"} if token else {}
        if self._auth_mode == "app" and self._app_auth is not None:
            token = await self._app_auth.installation_token()
            return {"Authorization": f"Bearer {token}"} if token else {}
        return {}

    # ------------------------------------------------------------------
    # 请求
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Any = None,
        etag: str = "",
        headers: Optional[Dict[str, str]] = None,
    ) -> ApiResponse:
        """统一请求出口:鉴权注入 → 重试 → 错误分类 → 限流跟踪。"""
        req_headers = dict(await self._auth_header())
        if etag:
            req_headers["If-None-Match"] = etag
        if headers:
            req_headers.update(headers)

        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.request(
                    method, path, params=params, json=json_body, headers=req_headers,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt + 1 < self._max_retries:
                    await asyncio.sleep((attempt + 1) ** 2)
                    continue
                raise GitHubApiError(f"网络错误: {exc}") from exc

            self._track_rate_limit(resp.headers)

            if resp.status_code == 304:
                return ApiResponse(status=304, data=None, not_modified=True,
                                   etag=etag, rate_remaining=self.rate_remaining,
                                   rate_reset=self.rate_reset_at)
            if resp.status_code in (403, 429) and self._is_rate_limited(resp):
                raise self._make_rate_limit_error(resp)
            if resp.status_code == 401:
                raise AuthError("凭据无效或已过期(401)", status=401)
            if resp.status_code == 403:
                raise AuthError(f"权限不足(403): {resp.text[:200]}", status=403)
            if resp.status_code == 404:
                raise NotFoundError(f"资源不存在或不可见(404): {path}", status=404)
            if resp.status_code >= 500:
                last_exc = GitHubApiError(f"服务端错误 {resp.status_code}", status=resp.status_code)
                if attempt + 1 < self._max_retries:
                    await asyncio.sleep((attempt + 1) ** 2)
                    continue
                raise GitHubApiError(
                    f"服务端错误 {resp.status_code}(重试 {self._max_retries} 次均失败)",
                    status=resp.status_code,
                )
            if resp.status_code >= 400:
                raise GitHubApiError(
                    f"请求失败 {resp.status_code}: {resp.text[:200]}", status=resp.status_code,
                )

            data: Any = None
            if resp.content:
                try:
                    data = resp.json()
                except ValueError:
                    data = resp.text
            return ApiResponse(
                status=resp.status_code, data=data,
                etag=resp.headers.get("etag", ""),
                rate_remaining=self.rate_remaining, rate_reset=self.rate_reset_at,
            )

        raise GitHubApiError(f"请求失败: {last_exc}")

    async def get(self, path: str, **kwargs: Any) -> ApiResponse:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> ApiResponse:
        return await self.request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> ApiResponse:
        return await self.request("PATCH", path, **kwargs)

    # ------------------------------------------------------------------
    # 限流
    # ------------------------------------------------------------------

    def _track_rate_limit(self, headers: httpx.Headers) -> None:
        remaining = headers.get("x-ratelimit-remaining")
        if remaining is not None and remaining.isdigit():
            self.rate_remaining = int(remaining)
        reset = headers.get("x-ratelimit-reset")
        if reset is not None and reset.isdigit():
            self.rate_reset_at = float(reset)

    @staticmethod
    def _is_rate_limited(resp: httpx.Response) -> bool:
        """区分限流 403 与真权限 403:有限流头且剩余为 0,或带 Retry-After。"""
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining is not None and remaining == "0":
            return True
        if resp.headers.get("retry-after"):
            return True
        return "rate limit" in resp.text[:300].lower()

    def _make_rate_limit_error(self, resp: httpx.Response) -> RateLimitError:
        retry_after = resp.headers.get("retry-after")
        wait = float(retry_after) if retry_after and retry_after.isdigit() else 60.0
        log(f"GitHub: 触发限流(remaining={self.rate_remaining}, {wait}s 后重试)", "WARNING", tag="通道")
        return RateLimitError("API 限流", retry_after=wait, reset_at=self.rate_reset_at)
