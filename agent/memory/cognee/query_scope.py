"""单次召回的跨 RPC 缓存生命周期，不跨用户请求保留查询内容。

模型看到什么：检索结果和出处不变，不增加提示词。
token 影响：同轮基础检索与深探复用相同通道的在途任务和结果，减少重复查询与嵌入。
缓存影响：只作用于本次检索，不触碰稳定提示词前缀。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from core.log import log

from .ports import RecallBackend
from .types import CogneeRecallItem
from .wire import encode


@dataclass
class QueryScope:
    identity: str = field(default_factory=lambda: uuid4().hex)
    releases: dict[int, Callable[[], Awaitable[object]]] = field(default_factory=dict)
    recalls: dict[tuple[int, str, str], asyncio.Task[list[CogneeRecallItem]]] = field(default_factory=dict)


current_query: ContextVar[QueryScope | None] = ContextVar("cognee_query_scope", default=None)


async def recall_shared(client: RecallBackend, query: str, **options: Any) -> list[CogneeRecallItem]:
    """同一读取范围内仅执行一次完全相同的后端请求，单个等待者取消不影响其他消费者。"""
    scope = current_query.get()
    if scope is None:
        return await client.recall(query, **options)
    key = (id(client), query, json.dumps(encode(options), sort_keys=True, separators=(",", ":")))
    task = scope.recalls.get(key)
    if task is None:
        task = asyncio.create_task(client.recall(query, **options), name="memory.cognee.shared_recall")
        scope.recalls[key] = task
    return await asyncio.shield(task)


@asynccontextmanager
async def query_scope() -> AsyncIterator[None]:
    if current_query.get() is not None:
        yield
        return
    scope = QueryScope()
    token = current_query.set(scope)
    try:
        yield
    finally:
        current_query.reset(token)
        for task in scope.recalls.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*scope.recalls.values(), return_exceptions=True)
        scope.recalls.clear()
        results = await asyncio.gather(*(release() for release in scope.releases.values()), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                log(f"Cognee 查询缓存随工作进程回收: {type(result).__name__}", "DEBUG")
