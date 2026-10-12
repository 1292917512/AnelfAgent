"""低延迟召回：精确标签和本地混合检索，不调用规划模型或远端图谱。

模型可见：沿用统一记忆格式与归属标签；固定记忆始终保留。
Token 影响：结果复用 top_k 与记忆预算；缓存前缀不变。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from core.log import log

from .memory_types import MemorySearchResult
from .probe import deep_probe_hub
from .recall_policy import RecallPolicy

if TYPE_CHECKING:
    from .memory_retriever import MemoryRetriever


async def recall_local(retriever: MemoryRetriever, conversation: list[dict], *, scope: str,
                       related_scopes: list[str], policy: RecallPolicy) -> tuple[list[dict], list[dict]]:
    """按强度选择本地召回路径，标签采用精确交集并保留来源。"""
    store = retriever._store
    profiles: list[dict] = []
    results: list[MemorySearchResult] = []
    k, min_score = retriever.recall_limits()
    if policy.level != "off":
        if not policy.tags:
            profiles = await retriever._load_entity_profiles([scope, *related_scopes])
        if policy.tags:
            entries = await store.search_by_tags(list(policy.tags), limit=k)
            results = [MemorySearchResult(
                id=f"mem:{entry.id}", snippet=entry.content, score=1,
                tags=entry.tags, memory_type=entry.memory_type.value, timestamp=entry.timestamp,
                sensitivity=str(entry.metadata.get("sensitivity", "normal")),
            ) for entry in entries]
        if policy.level == "fast":
            query = retriever._extract_query(conversation)
            if query and not retriever._is_trivial_turn(conversation):
                try:
                    async with asyncio.timeout(2):
                        vector = await retriever._embedder.embed_query(query)
                except Exception as exc:
                    log(f"快速召回向量不可用，采用本地文本检索: {type(exc).__name__}", "DEBUG", tag="记忆")
                    vector = None
                found = await store.search_unified(query=query, query_vec=vector,
                    query_tags=list(policy.tags) or None, require_tags=list(policy.tags) or None,
                    limit=k, min_score=min_score)
                results = list({item.id: item for item in [*results, *found]}.values())[:k]
    pins = await retriever._load_permanent_pins(results)
    deep_probe_hub.record(scope, results=[*results, *pins])
    messages: list[dict[str, Any]] = await retriever._format_unified_results([*pins, *results])
    return profiles, messages
