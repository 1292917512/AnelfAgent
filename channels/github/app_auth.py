"""GitHub App 鉴权 — JWT RS256 签发 + installation token 缓存(pyjwt,零新依赖)。

语义照抄 githubkit(nonebot-adapter-github 的委托层):
- JWT: ``{iss: app_id, iat: now-60s, exp: now+9min}``,RS256 签名,缓存 8 分钟;
- installation token: ``POST /app/installations/{id}/access_tokens``(Bearer JWT),
  缓存至 ``expires_at - 60s``;
- installation id 缺省取 ``GET /app/installations`` 的第一个(单 App 单主体场景)。
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx
import jwt

from core.log import log

_JWT_TTL = 540.0        # JWT 有效期 9 分钟(GitHub 上限 10 分钟)
_JWT_CACHE = 480.0      # 缓存 8 分钟
_TOKEN_MARGIN = 60.0    # installation token 过期前 60s 视为失效


class GitHubAppAuth:
    """GitHub App 鉴权:为 client.py 提供 installation token。"""

    def __init__(self, *, app_id: str, private_key: str, base_url: str = "https://api.github.com",
                 timeout: float = 15.0, transport: Any = None) -> None:
        if not app_id or not private_key:
            raise ValueError("GitHub App 鉴权需要 app_id 与 private_key")
        self._app_id = app_id
        self._private_key = private_key
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "AnelfAgent-GitHubChannel",
            },
            timeout=timeout,
            transport=transport,
        )
        self._jwt_cached: str = ""
        self._jwt_at: float = 0.0
        self._installation_id: Optional[int] = None
        self._token: str = ""
        self._token_expires_at: float = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------

    def _jwt(self) -> str:
        now = time.time()
        if self._jwt_cached and now - self._jwt_at < _JWT_CACHE:
            return self._jwt_cached
        payload = {
            "iss": self._app_id,
            "iat": int(now) - 60,   # 容忍本机时钟漂移
            "exp": int(now + _JWT_TTL),
        }
        self._jwt_cached = jwt.encode(payload, self._private_key, algorithm="RS256")
        self._jwt_at = now
        return self._jwt_cached

    async def _get_installation_id(self) -> int:
        if self._installation_id is not None:
            return self._installation_id
        resp = await self._client.get(
            "/app/installations", headers={"Authorization": f"Bearer {self._jwt()}"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"获取 App installations 失败: {resp.status_code} {resp.text[:200]}")
        installs = resp.json()
        if not installs:
            raise RuntimeError("该 GitHub App 尚未安装到任何账号/组织")
        self._installation_id = int(installs[0]["id"])
        return self._installation_id

    async def installation_token(self) -> str:
        """取 installation token(缓存至过期前 60s)。"""
        now = time.time()
        if self._token and now < self._token_expires_at - _TOKEN_MARGIN:
            return self._token
        inst_id = await self._get_installation_id()
        resp = await self._client.post(
            f"/app/installations/{inst_id}/access_tokens",
            headers={"Authorization": f"Bearer {self._jwt()}"},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"换取 installation token 失败: {resp.status_code} {resp.text[:200]}")
        data: Any = resp.json()
        self._token = str(data.get("token") or "")
        expires = str(data.get("expires_at") or "")
        try:
            from datetime import datetime
            self._token_expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            self._token_expires_at = now + 3600  # installation token 标准有效期 1h
        log("GitHub: installation token 已更新", "DEBUG", tag="通道")
        return self._token
