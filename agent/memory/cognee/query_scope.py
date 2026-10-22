"""单次召回的跨 RPC 缓存生命周期，不跨用户请求保留查询内容。

模型看到什么：检索结果和出处不变，不增加提示词。
token 影响：相同召回的多个通道复用查询向量，减少重复嵌入。
缓存影响：只作用于本次检索，不触碰稳定提示词前缀。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import uuid4

from core.log import log


@dataclass
class QueryScope:
    identity: str = field(default_factory=lambda: uuid4().hex)
    releases: dict[int, Callable[[], Awaitable[object]]] = field(default_factory=dict)


current_query: ContextVar[QueryScope | None] = ContextVar("cognee_query_scope", default=None)


@asynccontextmanager
async def query_scope() -> AsyncIterator[None]:
    scope = QueryScope()
    token = current_query.set(scope)
    try:
        yield
    finally:
        current_query.reset(token)
        results = await asyncio.gather(*(release() for release in scope.releases.values()), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                log(f"Cognee 查询缓存随工作进程回收: {type(result).__name__}", "DEBUG")
