"""按请求隔离的多账户、用户、OAuth App 和安装认证。"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Iterator

import httpx
from githubkit import GitHub, OAuthAppAuthStrategy, TokenAuthStrategy

from agent.channel import get_provider_key

from .app_auth import GitHubAppAuth
from .config import GitHubAccount, GitHubConfig
from .credentials import register_accounts
from .oauth import OAuthService


@dataclass(frozen=True)
class AuthContext:
    """单次操作的认证覆盖和安装权限范围。"""

    account: str = ""
    installation_id: int = 0
    user_token_ref: str = ""
    as_oauth_app: bool = False
    as_app: bool = False
    repositories: list[str] | None = None
    repository_ids: list[int] | None = None
    permissions: dict[str, str] | None = None


class GitHubAuth:
    """根据配置和任务局部上下文选择认证，热更凭据不串账户。"""

    def __init__(self, config: Callable[[], GitHubConfig]) -> None:
        self._config = config
        self._context: ContextVar[AuthContext | None] = ContextVar("github_auth", default=None)
        self._apps: dict[tuple[str, str, str, str], GitHubAppAuth] = {}
        self._lock = asyncio.Lock()
        self.oauth = OAuthService(config)
        self.register_credentials()

    def register_credentials(self) -> None:
        """将命名账户的凭据引用加入统一凭据中心。"""
        register_accounts(self._config().accounts)

    @contextmanager
    def using(self, context: AuthContext) -> Iterator[None]:
        """仅在当前异步任务内应用认证覆盖。"""
        token = self._context.set(context)
        try:
            yield
        finally:
            self._context.reset(token)

    def account(self, name: str = "") -> GitHubAccount:
        """返回命名账户或兼容的默认账户。"""
        cfg = self._config()
        if name:
            account = next((entry for entry in cfg.accounts if entry.name == name), None)
            if account is None:
                raise ValueError(f"未知 GitHub 账户: {name}")
            return account
        return GitHubAccount(name="default", auth_mode=cfg.auth_mode, token_ref=cfg.token_ref,
                             app_id=cfg.app_id, app_private_key_ref=cfg.app_private_key_ref,
                             installation_id=cfg.installation_id)

    def selection(self, path: str) -> tuple[GitHubAccount, str, int]:
        """根据仓库订阅和显式上下文选择账户与安装。"""
        parts = path.strip("/").split("/")
        repo = "/".join(parts[1:3]) if len(parts) >= 3 and parts[0] == "repos" else ""
        sub = self._config().subscription_for(repo)
        context = self._context.get() or AuthContext()
        account = self.account(context.account or (sub.account if sub else ""))
        installation_id = context.installation_id or (sub.installation_id if sub else 0) or account.installation_id
        return account, repo, installation_id

    async def resolve(self, path: str) -> httpx.Auth | None:
        """构造当前 API 请求的认证流。"""
        account, repo, installation_id = self.selection(path)
        context = self._context.get() or AuthContext()
        if context.user_token_ref:
            token = get_provider_key(context.user_token_ref)
            if not token:
                raise ValueError("用户令牌凭据为空")
            strategy = TokenAuthStrategy(token)
            return strategy.get_auth_flow(GitHub(strategy))
        if context.as_oauth_app:
            secret = get_provider_key(account.client_secret_ref)
            if not account.client_id or not secret:
                raise ValueError("OAuth App client_id/client_secret 未配置")
            oauth = OAuthAppAuthStrategy(account.client_id, secret)
            return oauth.get_auth_flow(GitHub(oauth, base_url=self._config().api_base_url))
        if account.auth_mode in ("pat", "oauth"):
            token = await self.oauth.token(account) if account.auth_mode == "oauth" else get_provider_key(account.token_ref)
            return _bearer(token) if token else None
        if account.auth_mode == "none":
            return None
        key = (account.name, account.app_id, get_provider_key(account.app_private_key_ref), self._config().api_base_url)
        async with self._lock:
            if key not in self._apps:
                self._apps[key] = GitHubAppAuth(app_id=key[1], private_key=key[2], base_url=key[3])
            app = self._apps[key]
        return await app.auth_for(repo=repo, installation_id=installation_id,
                                  as_app=context.as_app or path.startswith("/app"),
                                  repositories=context.repositories, repository_ids=context.repository_ids,
                                  permissions=context.permissions)

    def anonymous(self, path: str) -> bool:
        """判断请求是否以匿名账户运行。"""
        account, _, _ = self.selection(path)
        return account.auth_mode == "none" or (account.auth_mode in ("pat", "oauth") and not get_provider_key(account.token_ref))

    async def close(self) -> None:
        """关闭所有账户的安装定位客户端。"""
        for app in self._apps.values():
            await app.close()
        self._apps.clear()
        await self.oauth.close()


def _bearer(token: str) -> httpx.Auth:
    """创建用户令牌认证流。"""
    strategy = TokenAuthStrategy(token)
    return strategy.get_auth_flow(GitHub(strategy))
