"""OAuth 用户授权、一次性回调和令牌刷新。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

import httpx

from agent.channel import get_provider_key
from core.network import AsyncNetworkTransport
from core.provider_keys import set_provider_fields

from .config import GitHubAccount, GitHubConfig


class OAuthService:
    """保存有限期授权状态，将用户令牌写入凭据中心。"""

    def __init__(self, config: Callable[[], GitHubConfig], *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._config = config
        self._pending: dict[str, tuple[GitHubAccount, str, str, float]] = {}
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(transport=transport if transport is not None else AsyncNetworkTransport(), trust_env=False, timeout=15)

    @property
    def base_url(self) -> str:
        """返回当前 GitHub 服务的 OAuth 根地址。"""
        url = urlsplit(self._config().api_base_url)
        host = "github.com" if url.netloc == "api.github.com" else url.netloc
        return f"{url.scheme}://{host}"

    def begin(self, account_name: str, redirect_uri: str, scopes: list[str] | None = None) -> dict[str, str]:
        """创建带状态校验和 PKCE 的授权地址。"""
        account = next((entry for entry in self._config().accounts if entry.name == account_name), None)
        if account is None or not account.client_id or not get_provider_key(account.client_secret_ref):
            raise ValueError("请先配置账户的 OAuth client ID 和 client secret 凭据")
        self._pending = {key: value for key, value in self._pending.items() if value[3] > time.time()}
        if len(self._pending) >= 50:
            raise ValueError("待完成的授权请求过多")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self._pending[state] = (account, redirect_uri, verifier, time.time() + 600)
        query = {"client_id": account.client_id, "redirect_uri": redirect_uri, "state": state,
                 "code_challenge": challenge, "code_challenge_method": "S256"}
        if scopes:
            query["scope"] = " ".join(scopes)
        return {"authorize_url": self.base_url + "/login/oauth/authorize?" + urlencode(query), "account": account.name}

    async def finish(self, state: str, code: str) -> str:
        """消费一次性状态并保存授权令牌。"""
        pending = self._pending.pop(state, None)
        if pending is None or pending[3] <= time.time() or not code:
            raise ValueError("授权状态无效或已过期，请重新发起授权")
        account, redirect_uri, verifier, _ = pending
        data = await self._exchange({"client_id": account.client_id, "client_secret": get_provider_key(account.client_secret_ref),
                                     "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier})
        self._save(account, data)
        return account.name

    async def token(self, account: GitHubAccount) -> str:
        """读取用户令牌，必要时串行刷新过期令牌。"""
        async with self._lock:
            token = get_provider_key(account.token_ref)
            expires = get_provider_key(account.token_ref, "expires_at")
            refresh = get_provider_key(account.token_ref, "refresh_token")
            if token and expires and float(expires) < time.time() + 60 and refresh:
                data = await self._exchange({"client_id": account.client_id, "client_secret": get_provider_key(account.client_secret_ref),
                                             "grant_type": "refresh_token", "refresh_token": refresh})
                self._save(account, data)
                token = str(data["access_token"])
            return token

    async def _exchange(self, body: dict[str, str]) -> dict[str, Any]:
        """执行一次令牌交换，不自动重复消费授权码。"""
        response = await self._client.post(self.base_url + "/login/oauth/access_token", json=body, headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not data.get("access_token"):
            raise ValueError("GitHub 未返回授权令牌，请重新授权并检查应用配置")
        return data

    @staticmethod
    def _save(account: GitHubAccount, data: dict[str, Any]) -> None:
        """保存令牌及有效期，不将密钥放入频道配置。"""
        set_provider_fields(account.token_ref, {
            "api_key": str(data["access_token"]), "refresh_token": str(data.get("refresh_token") or ""),
            "expires_at": str(time.time() + int(data["expires_in"])) if data.get("expires_in") else "",
        })

    async def close(self) -> None:
        """清除未完成授权并关闭客户端。"""
        self._pending.clear()
        await self._client.aclose()
