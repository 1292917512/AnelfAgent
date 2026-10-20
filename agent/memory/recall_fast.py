"""低延迟召回：精确标签和本地混合检索，不调用规划模型或远端图谱。

模型可见：沿用统一记忆格式与归属标签；固定记忆始终保留。
Token 影响：结果复用 top_k 与记忆预算；缓存前缀不变。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from core.log import log

from .memory_types import MemorySearchResult
from .recall_policy import RecallPolicy

if TYPE_CHECKING:
    from .memory_retriever import MemoryRetriever


async def search_local(retriever: MemoryRetriever, query: str, *,
                       tags: tuple[str, ...] = ()) -> list[MemorySearchResult]:
    """有界向量等待后执行本地混合检索，精确标签同时约束全文与向量结果。"""
    k, min_score = retriever.recall_limits()
    try:
        async with asyncio.timeout(2):
            vector = await retriever._embedder.embed_query(query)
    except Exception as exc:
        log(f"快速召回向量不可用，采用本地文本检索: {type(exc).__name__}", "DEBUG", tag="记忆")
        vector = None
    return await retriever._store.search_unified(
        query=query, query_vec=vector, query_tags=list(tags) or None,
        require_tags=list(tags) or None, limit=k, min_score=min_score,
    )


async def recall_local(retriever: MemoryRetriever, conversation: list[dict], *, scope: str,
                       related_scopes: list[str], policy: RecallPolicy) -> tuple[list[dict], list[dict]]:
    """按强度选择本地召回路径，标签采用精确交集并保留来源。"""
    store = retriever._store
    profiles: list[dict] = []
    tagged: list[MemorySearchResult] = []
    found: list[MemorySearchResult] = []
    k, _ = retriever.recall_limits()

    async def load_profiles() -> None:
        nonlocal profiles
        profiles = await retriever._load_entity_profiles([scope, *related_scopes])

    async def load_tags() -> None:
        entries = await store.search_by_tags(list(policy.tags), limit=k)
        tagged.extend(MemorySearchResult.from_entry(entry, 1) for entry in entries)

    async def search() -> None:
        query = retriever._extract_query(conversation)
        if query and not retriever._is_trivial_turn(conversation):
            found.extend(await search_local(retriever, query, tags=policy.tags))

    async with asyncio.TaskGroup() as tasks:
        if policy.level != "off":
            tasks.create_task(load_tags() if policy.tags else load_profiles())
            if policy.level == "fast":
                tasks.create_task(search())
    results = list({item.id: item for item in [*tagged, *found]}.values())[:k]
    pins = await retriever._load_permanent_pins(results)
    messages: list[dict[str, Any]] = await retriever._format_unified_results([*pins, *results], scope=scope)
    return profiles, messages
