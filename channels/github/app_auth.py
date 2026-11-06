"""GitHub App 认证和仓库安装定位。"""

from __future__ import annotations

import asyncio

import httpx
from githubkit import AppAuthStrategy, GitHub
from githubkit.cache import MemCacheStrategy
from githubkit.utils import UNSET
from githubkit.versions.latest.types import AppPermissionsType
from pydantic import TypeAdapter

from core.network import AsyncNetworkTransport


class GitHubAppAuth:
    """使用 GitHubKit 签发和缓存隔离的 App 与安装认证。"""

    def __init__(self, *, app_id: str, private_key: str, base_url: str = "https://api.github.com",
                 timeout: float = 15.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not app_id or not private_key:
            raise ValueError("GitHub App 鉴权需要 app_id 与 private_key")
        self.strategy = AppAuthStrategy(app_id, private_key)
        self.sdk = GitHub(self.strategy, base_url=base_url, auto_retry=False, http_cache=False,
                          cache_strategy=MemCacheStrategy(), trust_env=False)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/", auth=self.strategy.get_auth_flow(self.sdk),
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            timeout=timeout, transport=transport if transport is not None else AsyncNetworkTransport(), trust_env=False,
        )
        self._installations: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        """关闭安装定位客户端。"""
        await self._client.aclose()

    async def installation_for(self, repo: str = "") -> int:
        """按仓库定位安装；无仓库时仅接受唯一安装。"""
        async with self._lock:
            if repo in self._installations:
                return self._installations[repo]
            path = f"repos/{repo}/installation" if repo else "app/installations"
            response = await self._client.get(path, params=None if repo else {"per_page": 2})
            response.raise_for_status()
            data = response.json()
            if not repo:
                if not data:
                    raise ValueError("该 GitHub App 尚未安装")
                if len(data) != 1:
                    raise ValueError("存在多个安装，请指定仓库或 installation_id")
                data = data[0]
            installation_id = int(data["id"])
            self._installations[repo] = installation_id
            return installation_id

    async def auth_for(self, *, repo: str = "", installation_id: int = 0, as_app: bool = False,
                       repositories: list[str] | None = None, repository_ids: list[int] | None = None,
                       permissions: dict[str, str] | None = None) -> httpx.Auth:
        """创建当前请求的 App 或指定安装的权限上下文。"""
        if as_app:
            return self.strategy.get_auth_flow(self.sdk)
        installation_id = installation_id or await self.installation_for(repo)
        strategy = self.strategy.as_installation(
            installation_id, repositories=repositories if repositories is not None else UNSET,
            repository_ids=repository_ids if repository_ids is not None else UNSET,
            permissions=TypeAdapter(AppPermissionsType).validate_python(permissions) if permissions is not None else UNSET,
        )
        return strategy.get_auth_flow(self.sdk)
