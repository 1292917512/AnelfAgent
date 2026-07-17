"""原生记忆与 Cognee 的联邦召回和秩融合。"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable
from typing import Optional

from core.log import log

from ..memory_types import MemorySearchResult
from .client import CogneeClient
from .config import CogneeConfig

_WHITESPACE_RE = re.compile(r"\s+")


async def federated_search(
    native_search: Awaitable[list[MemorySearchResult]],
    *,
    query: str,
    client: Optional[CogneeClient],
    config: CogneeConfig,
    limit: int,
    entity_scope: str = "",
    query_tags: Optional[list[str]] = None,
) -> list[MemorySearchResult]:
    """并行搜索原生后端和 Cognee，失败时透明降级。"""
    if not config.enabled or not config.recall_enabled or client is None:
        return (await native_search)[:limit]

    datasets = datasets_for_scope(config, entity_scope, query_tags)
    cognee_task = asyncio.create_task(
        _search_cognee(client, config, query, datasets, limit),
        name="memory.cognee.recall",
    )
    native_result, cognee_result = await asyncio.gather(
        native_search,
        cognee_task,
        return_exceptions=True,
    )

    native: list[MemorySearchResult]
    if isinstance(native_result, BaseException):
        log(f"原生记忆搜索失败: {native_result}", "WARNING", tag="思维")
        native = []
    else:
        native = native_result
    if isinstance(cognee_result, BaseException):
        log(f"Cognee 召回失败，降级原生记忆: {cognee_result}", "DEBUG", tag="思维")
        cognee = []
    else:
        cognee = cognee_result
    return reciprocal_rank_fusion(native, cognee, config=config, limit=limit)


async def _search_cognee(
    client: CogneeClient,
    config: CogneeConfig,
    query: str,
    datasets: list[str],
    limit: int,
) -> list[MemorySearchResult]:
    availability = await client.initialize()
    if not availability.ready:
        return []
    pool_size = max(limit, limit * config.recall_pool_multiplier)
    tasks: list[Awaitable] = []
    for search_type_name in config.search_types:
        try:
            search_type = client.search_type(search_type_name)
        except (AttributeError, RuntimeError):
            continue
        for dataset_name in datasets:
            tasks.append(client.recall(
                query,
                query_type=search_type,
                datasets=[dataset_name],
                top_k=pool_size,
                auto_route=False,
                only_context=True,
                include_references=True,
            ))
    if not tasks:
        for dataset_name in datasets:
            tasks.append(client.recall(
                query,
                datasets=[dataset_name],
                top_k=pool_size,
                auto_route=True,
                only_context=True,
                include_references=True,
            ))
    batches = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[MemorySearchResult] = []
    for batch in batches:
        if isinstance(batch, BaseException):
            continue
        for item in batch:
            results.append(MemorySearchResult(
                id=item.id,
                snippet=item.content[:700],
                score=item.score,
                source=item.source,
                dataset_id=item.dataset_id,
                dataset_name=item.dataset_name,
                provenance=item.metadata,
            ))
    return results


def reciprocal_rank_fusion(
    native: list[MemorySearchResult],
    cognee: list[MemorySearchResult],
    *,
    config: CogneeConfig,
    limit: int,
) -> list[MemorySearchResult]:
    """使用加权 RRF 合并不可直接比较的后端分数，并按内容去重。"""
    scores: dict[str, float] = {}
    chosen: dict[str, MemorySearchResult] = {}

    def add(
        results: list[MemorySearchResult],
        weight: float,
    ) -> None:
        for rank, result in enumerate(results, start=1):
            key = _dedupe_key(result)
            scores[key] = scores.get(key, 0.0) + weight / (config.rrf_k + rank)
            current = chosen.get(key)
            if current is None or _source_priority(result.source) > _source_priority(current.source):
                chosen[key] = result.model_copy(deep=True)

    add(native, config.native_weight)
    add(cognee, config.cognee_weight)
    fused = list(chosen.items())
    fused.sort(key=lambda item: scores[item[0]], reverse=True)
    if not fused:
        return []
    max_score = max(scores[key] for key, _ in fused) or 1.0
    output: list[MemorySearchResult] = []
    for key, result in fused[:limit]:
        result.score = scores[key] / max_score
        output.append(result)
    return output


def datasets_for_scope(
    config: CogneeConfig,
    entity_scope: str,
    query_tags: Optional[list[str]],
) -> list[str]:
    """将 Anelf scope 映射为允许访问的 Cognee datasets。"""
    datasets = [f"{config.dataset_prefix}_global"]
    scope_type = ""
    scope_id = ""
    if entity_scope and "_" in entity_scope:
        prefix, value = entity_scope.split("_", 1)
        if prefix in {"user", "group"} and value:
            scope_type, scope_id = prefix, value
    if not scope_id:
        for tag in query_tags or []:
            if ":" not in tag:
                continue
            prefix, value = tag.split(":", 1)
            if prefix in {"user", "group"} and value:
                scope_type, scope_id = prefix, value
                break
    if scope_id:
        digest = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:16]
        datasets.append(f"{config.dataset_prefix}_{scope_type}_{digest}")
    return datasets


def _dedupe_key(result: MemorySearchResult) -> str:
    memory_id = result.provenance.get("anelf_memory_id")
    if memory_id:
        return f"memory:{memory_id}"
    text = result.snippet
    if "\n\n" in text and text.startswith("Memory type:"):
        text = text.split("\n\n", 1)[1]
    normalized = _WHITESPACE_RE.sub(" ", text).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _source_priority(source: str) -> int:
    return {
        "memory": 4,
        "file": 3,
        "cognee_chunk": 2,
        "cognee_graph": 1,
    }.get(source, 0)
