"""GitHub HTTP 请求、认证、配额跟踪与安全重试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import httpx

from core.log import log
from core.network import AsyncNetworkTransport

from .auth import GitHubAuth

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


class OutcomeUnknownError(GitHubApiError):
    """写请求结果未确认，必须查询状态后再决定是否重试。"""


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
    headers: dict[str, str] = field(default_factory=dict)


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
        auth: GitHubAuth | None = None,
    ) -> None:
        self._auth_mode = auth_mode
        self._token_provider = token_provider
        self._app_auth = app_auth
        self.auth = auth
        self._max_retries = max(1, max_retries)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=_DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            transport=transport if transport is not None else AsyncNetworkTransport(),
            trust_env=False,
        )
        self.rate_remaining: int = -1
        self.rate_reset_at: float = 0.0
        self.rate_limit: int = 5000
        self._quotas: dict[str, tuple[int, int, float]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    def quota_key(self, path: str) -> str:
        """按认证账户和安装区分配额。"""
        if self.auth is None:
            return "default"
        account, repo, installation = self.auth.selection(path)
        return f"{account.name}:{installation or repo if account.auth_mode == 'app' else ''}"

    def quota_for(self, path: str) -> tuple[int, int, float]:
        """读取请求对应账户最近的核心配额。"""
        return self._quotas.get(self.quota_key(path), (self.rate_limit, -1, 0.0))

    @staticmethod
    def validate_path(path: str) -> str:
        """只接受 API 根地址下的相对路径。"""
        from urllib.parse import unquote, urlsplit

        parsed = urlsplit(path)
        decoded = unquote(path)
        if (not path.startswith("/") or path.startswith("//") or parsed.scheme or parsed.netloc
                or parsed.query or parsed.fragment or "\\" in decoded
                or any(part in (".", "..") for part in decoded.split("/"))
                or any(ord(character) < 32 for character in decoded)):
            raise ValueError("API path 必须是根地址下的路径；查询参数请放入 params")
        return path


    async def _auth_header(self) -> Dict[str, str]:
        if self._auth_mode == "pat":
            token = self._token_provider() if self._token_provider else ""
            return {"Authorization": f"Bearer {token}"} if token else {}
        return {}


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
        path = self.validate_path(path)
        method = method.upper()
        retry_safe = method in ("GET", "HEAD", "OPTIONS")
        attempts = self._max_retries if retry_safe else 1
        try:
            auth = await self.auth.resolve(path) if self.auth else None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 404:
                raise NotFoundError("找不到仓库对应的 App 安装", status=status) from exc
            if status in (401, 403):
                raise AuthError("App 认证失败，请检查私钥和安装权限", status=status) from exc
            raise GitHubApiError("App 安装查询失败", status=status) from exc
        if self._app_auth is not None and auth is None:
            parts = path.strip("/").split("/")
            repo = "/".join(parts[1:3]) if len(parts) >= 3 and parts[0] == "repos" else ""
            auth = await self._app_auth.auth_for(repo=repo, as_app=path.startswith("/app"))
        req_headers = dict(await self._auth_header()) if self.auth is None else {}
        if etag:
            req_headers["If-None-Match"] = etag
        if headers:
            if any(key.lower() in ("authorization", "host", "cookie") for key in headers):
                raise ValueError("认证头由频道管理")
            req_headers.update(headers)

        last_exc: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                url: str | httpx.URL = path.lstrip("/")
                if path == "/graphql" and self._client.base_url.path.rstrip("/").endswith("/api/v3"):
                    url = self._client.base_url.copy_with(path=self._client.base_url.path.rstrip("/")[:-7] + "/api/graphql")
                resp = await self._client.request(
                    method, url, params=params, json=json_body, headers=req_headers, auth=auth,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if not retry_safe:
                    raise OutcomeUnknownError("写请求响应未确认，请先查询目标状态，勿直接重复写入") from exc
                if attempt + 1 < attempts:
                    await asyncio.sleep((attempt + 1) ** 2)
                    continue
                raise GitHubApiError(f"网络错误: {exc}") from exc

            self._track_rate_limit(resp.headers)
            if resp.headers.get("x-ratelimit-resource", "core") == "core":
                self._quotas[self.quota_key(path)] = (self.rate_limit, self.rate_remaining, self.rate_reset_at)

            if resp.status_code == 304:
                return ApiResponse(status=304, data=None, not_modified=True,
                                   etag=etag, rate_remaining=self.rate_remaining,
                                   rate_reset=self.rate_reset_at, headers=dict(resp.headers))
            if resp.status_code in (403, 429) and self._is_rate_limited(resp):
                raise self._make_rate_limit_error(resp)
            if resp.status_code == 401:
                raise AuthError("凭据无效或已过期(401)", status=401)
            if resp.status_code == 403:
                raise AuthError(f"权限不足(403): {resp.text[:200]}", status=403)
            if resp.status_code == 404:
                raise NotFoundError(f"资源不存在或不可见(404): {path}", status=404)
            if resp.status_code >= 500:
                if not retry_safe:
                    raise OutcomeUnknownError("写请求返回服务端错误，结果未确认，请先查询目标状态", status=resp.status_code)
                last_exc = GitHubApiError(f"服务端错误 {resp.status_code}", status=resp.status_code)
                if attempt + 1 < attempts:
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
                headers=dict(resp.headers),
            )

        raise GitHubApiError(f"请求失败: {last_exc}")

    async def get(self, path: str, **kwargs: Any) -> ApiResponse:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> ApiResponse:
        return await self.request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> ApiResponse:
        return await self.request("PATCH", path, **kwargs)


    def _track_rate_limit(self, headers: httpx.Headers) -> None:
        if headers.get("x-ratelimit-resource", "core") != "core":
            return
        limit = headers.get("x-ratelimit-limit", "")
        if limit.isdigit():
            self.rate_limit = int(limit)
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
