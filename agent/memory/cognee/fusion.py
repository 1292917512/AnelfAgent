"""原生记忆与 Cognee 的联邦召回和秩融合。

模型看到什么：动态记忆区接收逐条证据，慢通道不丢弃已完成的事实。
token 影响：候选融合后按原预算截取，避免重复文档与检索模板占用上下文。
缓存影响：只修改动态召回区，不触碰稳定前缀。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal, Optional, TypedDict

from pydantic import ValidationError

from core.log import log

from ..memory_types import MemorySearchResult, excerpt
from ..provenance import recall_provenance
from .adapters import query_embeddings
from .client import CogneeClient
from .config import CogneeConfig
from .types import CogneeRecallItem

#: 投影文档头字段（coordinator._render_memory 的输出格式）
_PROJECTION_HEADER_KEYS = ("Memory type", "Memory ID", "Timestamp", "Source", "Importance", "Tags", "Metadata")


@dataclass
class CogneeSearchBatch:
    """单条检索通道的状态与结果，不携带查询原文。"""

    lane: str
    status: Literal["running", "done", "error"]
    elapsed: float = 0.0
    results: list[MemorySearchResult] = field(default_factory=list)
    error: str = ""


class MemoryProjection(TypedDict, total=False):
    """投影文档中实际存在且已验证的记忆字段。"""

    snippet: str
    tags: list[str]
    provenance: dict[str, Any]
    timestamp: float
    memory_type: str | None
    sensitivity: str


def parse_memory_projection(text: str) -> MemoryProjection:
    """恢复完整投影的出处和时间；残缺分块与综合回答不推断原始归属。"""
    if not text.startswith("Memory type:"):
        return {"snippet": text}
    head, separator, content = text.partition("\n\n")
    if not separator or not content.strip():
        return {"snippet": text}
    fields: dict[str, str] = {}
    for line in head.split("\n"):
        for key in _PROJECTION_HEADER_KEYS:
            if line.startswith(f"{key}: "):
                fields[key] = line[len(key) + 2:].strip()
                break
    tags = [tag.strip() for tag in fields.get("Tags", "").split(",") if tag.strip()]
    try:
        metadata = json.loads(fields.get("Metadata", "{}"))
    except (ValueError, TypeError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    provenance = recall_provenance(metadata, source=fields.get("Source", ""))
    mid = fields.get("Memory ID", "")
    if mid.isdigit() and int(mid) > 0:
        provenance["anelf_memory_id"] = int(mid)
    try:
        timestamp = float(fields.get("Timestamp", "0"))
    except ValueError:
        timestamp = 0.0
    if not 0 <= timestamp < 253402300800:
        timestamp = 0.0
    return {
        "snippet": content.strip(), "tags": tags, "provenance": provenance,
        "timestamp": timestamp, "memory_type": fields.get("Memory type"),
        "sensitivity": str(metadata.get("sensitivity", "normal"))
        if metadata.get("sensitivity") in ("private", "secret") else "normal",
    }


async def federated_search(
    native_search: Awaitable[list[MemorySearchResult]],
    *,
    query: str,
    client: Optional[CogneeClient],
    config: CogneeConfig,
    limit: int,
    entity_scope: str = "",
    query_tags: Optional[list[str]] = None,
    deep: bool = False,
    node_names: Optional[list[str]] = None,
    on_results: Callable[[list[MemorySearchResult]], None] | None = None,
) -> list[MemorySearchResult]:
    """并行搜索原生后端和 Cognee，失败时透明降级。

    deep=True 时使用 config.deep_search_types（含图谱类检索）替代
    config.search_types，覆盖更广但更慢；仅用于主动深度召回。
    node_names 非空时追加定向检索通道（cognee node_name 限定到
    提及实体的事实块，聊天对象明确时精度显著高于泛化检索）。
    """
    if not config.enabled or not config.recall_enabled or client is None:
        return (await native_search)[:limit]

    datasets = datasets_for_scope(config, entity_scope, query_tags)
    search_types = (config.deep_search_types or config.search_types) if deep else config.search_types
    native: list[MemorySearchResult] = []
    batches: dict[str, list[MemorySearchResult]] = {}

    def snapshot() -> list[MemorySearchResult]:
        return _fuse_ranked_lists(
            [(native, config.native_weight),
             *((batches[key], config.cognee_weight) for key in sorted(batches))],
            config=config, limit=limit,
        )

    def publish() -> None:
        if on_results is not None:
            on_results(snapshot())

    async def read_native() -> None:
        nonlocal native
        native = await native_search
        publish()

    async def consume(batch: CogneeSearchBatch) -> None:
        if batch.status == "done":
            batches[batch.lane] = batch.results
            publish()

    tasks = [asyncio.create_task(read_native()), asyncio.create_task(
        search_cognee(client, config, query, datasets, limit, search_types,
                      node_names=node_names, on_batch=consume),
        name="memory.cognee.recall",
    )]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                log(f"联邦召回通道失败，保留已完成结果: {result}", "DEBUG", tag="思维")
        return snapshot()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def search_cognee(
    client: CogneeClient,
    config: CogneeConfig,
    query: str,
    datasets: list[str],
    limit: int,
    search_types: Optional[list[str]] = None,
    *,
    node_names: Optional[list[str]] = None,
    on_batch: Callable[[CogneeSearchBatch], Awaitable[None]] | None = None,
) -> list[MemorySearchResult]:
    """cognee 检索的统一拼装点：类型 × 数据集并行 recall + 定向通道 + 归一化。

    联邦召回（federated_search）与异步深探（probe）共用本入口——
    cognee 调用模式只在此处拼装，外层不再各自组 client.recall。
    每条通道完成即可通过 on_batch 消费，慢通道不阻塞已完成结果的呈现。
    """
    availability = await client.initialize()
    if not availability.ready:
        return []
    datasets = await client.available_datasets(datasets)
    if not datasets:
        return []
    pool_size = max(limit, limit * config.recall_pool_multiplier)
    lanes: list[tuple[str, Callable[[], Awaitable[list[CogneeRecallItem]]]]] = []
    targeted_type = None
    if node_names:
        try:
            targeted_type = client.search_type("CHUNKS")
        except (AttributeError, RuntimeError):
            targeted_type = None
    datasets = list(dict.fromkeys(datasets))
    for search_type_name in dict.fromkeys(search_types or config.search_types):
        try:
            search_type = client.search_type(search_type_name)
        except (AttributeError, RuntimeError):
            continue
        for dataset_name in datasets:
            lanes.append((f"{search_type_name}/{dataset_name}", partial(client.recall,
                query,
                query_type=search_type,
                datasets=[dataset_name],
                top_k=pool_size,
                auto_route=False,
                only_context=search_type_name not in {"CHUNKS", "CHUNKS_LEXICAL", "SUMMARIES"},
                include_references=True,
            )))
    # 定向检索通道：node_name 限定到提及实体的事实块（每数据集一次，
    # 不随 search_types 翻倍——定向精度来自节点过滤而非查询类型）
    if targeted_type is not None:
        for dataset_name in datasets:
            lanes.append((f"CHUNKS:targeted/{dataset_name}", partial(client.recall,
                query,
                query_type=targeted_type,
                datasets=[dataset_name],
                top_k=pool_size,
                node_name=list(node_names or []),
                auto_route=False,
                only_context=False,
                include_references=True,
            )))
    if not lanes:
        for dataset_name in datasets:
            lanes.append((f"AUTO/{dataset_name}", partial(client.recall,
                query,
                datasets=[dataset_name],
                top_k=pool_size,
                auto_route=True,
                only_context=True,
                include_references=True,
            )))

    async def consume(lane: str, call: Callable[[], Awaitable[list[CogneeRecallItem]]]) -> list[MemorySearchResult]:
        started = time.monotonic()
        if on_batch is not None:
            await on_batch(CogneeSearchBatch(lane, "running"))
        try:
            items = await call()
        except Exception as exc:
            detail = f"{type(exc).__name__}: {str(exc) or '检索未完成'}"
            elapsed = time.monotonic() - started
            log(f"Cognee 检索通道失败: {lane}, {elapsed:.1f}s, {detail}", "DEBUG", tag="记忆")
            if on_batch is not None:
                await on_batch(CogneeSearchBatch(lane, "error", elapsed, error=detail))
            return []
        results: list[MemorySearchResult] = []
        for item in items:
            projection = parse_memory_projection(item.content)
            if item.source == "cognee_graph":
                projection = {"snippet": projection["snippet"],
                              "sensitivity": projection.get("sensitivity", "normal")}
            attribution = item.metadata.get("anelf_attribution")
            if item.source == "cognee_chunk" and isinstance(attribution, dict):
                try:
                    restored = MemorySearchResult.model_validate({
                        **attribution, "id": item.id, "snippet": "", "score": 0,
                    })
                except ValidationError as exc:
                    log(f"Cognee 分块出处字段无效: {type(exc).__name__}", "DEBUG", tag="记忆")
                else:
                    projection.setdefault("tags", restored.tags)
                    projection.setdefault("timestamp", restored.timestamp)
                    projection.setdefault("sensitivity", restored.sensitivity)
                    projection.setdefault("provenance", restored.provenance)
            provenance = {**item.metadata, **projection.pop("provenance", {})}
            provenance.pop("anelf_attribution", None)
            projection["snippet"] = excerpt(str(projection["snippet"]), 700)
            results.append(MemorySearchResult.model_validate({
                **projection, "id": item.id, "score": item.score, "source": item.source,
                "dataset_id": item.dataset_id, "dataset_name": item.dataset_name,
                "provenance": provenance,
            }))
        elapsed = time.monotonic() - started
        log(f"Cognee 检索通道完成: {lane}, {elapsed:.1f}s, {len(results)} 条", "DEBUG", tag="记忆")
        if on_batch is not None:
            await on_batch(CogneeSearchBatch(lane, "done", elapsed, results))
        return results

    async with query_embeddings():
        tasks = [asyncio.create_task(consume(lane, call), name=f"cognee.search.{lane}") for lane, call in lanes]
        try:
            batches = await asyncio.gather(*tasks)
            return _fuse_ranked_lists(
                [(batch, 1.0) for batch in batches], config=config,
                limit=pool_size,
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def reciprocal_rank_fusion(
    native: list[MemorySearchResult],
    cognee: list[MemorySearchResult],
    *,
    config: CogneeConfig,
    limit: int,
) -> list[MemorySearchResult]:
    """使用加权 RRF 合并不可直接比较的后端分数，并按内容去重。"""
    return _fuse_ranked_lists(
        [(native, config.native_weight), (cognee, config.cognee_weight)],
        config=config, limit=limit,
    )


def _fuse_ranked_lists(
    lanes: list[tuple[list[MemorySearchResult], float]],
    *, config: CogneeConfig, limit: int,
) -> list[MemorySearchResult]:
    """逐通道计算秩，同通道重复命中只计一次，来源优先级保留原生证据。"""
    scores: dict[str, float] = {}
    chosen: dict[str, MemorySearchResult] = {}
    references: dict[str, set[str]] = {}
    for results, _weight in lanes:
        for result in results:
            if reference := result.memory_reference():
                references.setdefault(result.content_key(), set()).add(reference)

    def add(
        results: list[MemorySearchResult],
        weight: float,
    ) -> None:
        seen: set[str] = set()
        for result in results:
            key = dedupe_key(result)
            linked = references.get(result.content_key(), set())
            if not result.memory_reference() and len(linked) == 1:
                key = next(iter(linked))
            if key in seen:
                continue
            seen.add(key)
            rank = len(seen)
            scores[key] = scores.get(key, 0.0) + weight / (config.rrf_k + rank)
            current = chosen.get(key)
            if current is None or _source_priority(result.source) > _source_priority(current.source):
                chosen[key] = result.model_copy(deep=True)

    for results, weight in lanes:
        add(results, weight)
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
    """将 Anelf scope 映射为允许访问的 Cognee datasets。

    全局 relations 数据集（自由型节点：topic/person/concept 等公共知识）
    对所有 scope 开放——跨实体正是图谱价值；实体型节点的投影按 scope
    拆分数据集（coordinator.graph_dataset_for_node 同源派生），消除
    跨 scope 检索稀释。
    """
    datasets = [f"{config.dataset_prefix}_global", f"{config.dataset_prefix}_relations"]
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
        # 本 scope 的实体关系投影数据集（与 coordinator.graph_dataset_for_node 同构）
        datasets.append(f"{config.dataset_prefix}_relations_{scope_type}_{digest}")
    return datasets


def dedupe_key(result: MemorySearchResult) -> str:
    """精确回链优先；否则按内容和归属判重，未知出处不借用查询身份。"""
    return result.memory_reference() or result.content_key() or result.id


def _source_priority(source: str) -> int:
    return {
        "memory": 4,
        "file": 3,
        "cognee_chunk": 2,
        "cognee_graph": 1,
    }.get(source, 0)
