"""飞书用户昵称解析 — contact API + TTL 缓存（fail-open）。

应用未开通 ``contact:user.base:readonly`` 权限时静默降级：
返回空串（调用方回退 open_id），且只记一次 WARNING 不再重复请求，
避免每条入站消息刷错误日志。
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Tuple

import lark_oapi as lark

from core.log import log

_TTL_SECONDS = 3600.0
_MAX_ENTRIES = 2000
_NO_SCOPE_CODE = 99991672


class UserNameCache:
    """open_id → 用户昵称 的 TTL 缓存。"""

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[str, float]] = {}
        self._denied = False

    async def get_name(self, client: lark.Client, open_id: str) -> str:
        """查询用户昵称；权限缺失/查询失败返回空串（不抛异常）。"""
        if not open_id or self._denied:
            return ""
        cached = self._cache.get(open_id)
        if cached and time.time() - cached[1] < _TTL_SECONDS:
            return cached[0]
        name = await self._fetch(client, open_id)
        if name:
            if len(self._cache) >= _MAX_ENTRIES:
                oldest = min(self._cache, key=lambda k: self._cache[k][1])
                self._cache.pop(oldest, None)
            self._cache[open_id] = (name, time.time())
        return name

    async def _fetch(self, client: lark.Client, open_id: str) -> str:
        from lark_oapi.api.contact.v3 import GetUserRequest

        def _do() -> str:
            req = GetUserRequest.builder() \
                .user_id(open_id) \
                .user_id_type("open_id") \
                .build()
            resp = client.contact.v3.user.get(req)
            if not resp.success():
                if resp.code == _NO_SCOPE_CODE:
                    self._denied = True
                    log(
                        "飞书: 缺少 contact:user.base:readonly 权限，发送者昵称解析已停用"
                        "（可在飞书开放平台开通该权限后重启频道恢复）",
                        "WARNING",
                    )
                else:
                    log(f"飞书: 查询用户信息失败 code={resp.code} msg={resp.msg}", "DEBUG")
                return ""
            user = resp.data.user if resp.data else None
            return str(getattr(user, "name", "") or "")

        try:
            return await asyncio.to_thread(_do)
        except Exception as exc:
            log(f"飞书: 查询用户信息异常 ({open_id}): {exc}", "DEBUG")
            return ""
