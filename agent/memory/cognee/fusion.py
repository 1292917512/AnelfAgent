"""原生记忆与 Cognee 的联邦召回和秩融合。

模型看到什么：动态记忆区接收逐条证据，慢通道不丢弃已完成的事实。
token 影响：候选融合后按原预算截取，避免重复文档与检索模板占用上下文。
缓存影响：只修改动态召回区，不触碰稳定前缀。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal, Optional, TypedDict

from pydantic import ValidationError

from core.config import get_config_float
from core.log import log

from ..memory_types import MemorySearchResult, excerpt
from ..projection import canonical_tags, projection_dataset
from ..tag_query import TagQuery
from .adapters import query_embeddings
from .config import CogneeConfig
from .ports import RecallBackend
from .types import CogneeRecallItem


@dataclass
class CogneeSearchBatch:
    """单条检索通道的状态与结果，不携带查询原文。"""

    lane: str
    status: Literal["running", "done", "error"]
    elapsed: float = 0.0
    results: list[MemorySearchResult] = field(default_factory=list)
    error: str = ""


@dataclass
class RecallDiagnostics:
    """单次检索的覆盖范围，不把超时/降级误报成知识不存在；不存查询或供应商错误。"""

    native: str = "running"
    cognee: str = "running"
    completed_lanes: int = 0
    failed_lanes: int = 0
    timed_out: bool = False
    summary_used: bool = False

    def result(self) -> dict[str, Any]:
        complete = not self.timed_out and not self.failed_lanes and self.native == "complete"
        status = "complete" if complete and self.cognee == "complete" else "partial"
        if complete and self.cognee in {"disabled", "index_not_ready", "unavailable", "no_datasets"}:
            status = "native_only"
        return {"status": status, "native": self.native, "cognee": self.cognee,
                "completed_lanes": self.completed_lanes, "failed_lanes": self.failed_lanes,
                "timed_out": self.timed_out, "summary_used": self.summary_used}


class MemoryProjection(TypedDict, total=False):
    """投影文档中实际存在且已验证的记忆字段。"""

    snippet: str
    tags: list[str]
    provenance: dict[str, Any]
    timestamp: float
    memory_type: str | None
    sensitivity: str


def recall_projection(item: CogneeRecallItem) -> MemoryProjection:
    """正文保持原样；只从分块的结构化归属恢复来源，图谱综合不冒充原话。"""
    projection: MemoryProjection = {"snippet": item.content, "sensitivity": "private"}
    attribution = item.metadata.get("anelf_attribution")
    if item.source != "cognee_chunk" or not isinstance(attribution, dict):
        return projection
    try:
        restored = MemorySearchResult.model_validate({
            **attribution, "id": item.id, "snippet": "", "score": 0,
        })
    except ValidationError as exc:
        log(f"Cognee 分块出处字段无效: {type(exc).__name__}", "DEBUG", tag="记忆")
        return projection
    projection.update({
        "tags": restored.tags,
        "timestamp": restored.timestamp if 0 <= restored.timestamp < 253402300800 else 0,
        "memory_type": restored.memory_type,
        "sensitivity": restored.sensitivity if restored.sensitivity in {"normal", "private", "secret"} else "private",
        "provenance": restored.provenance,
    })
    return projection


async def federated_search(
    native_search: Awaitable[list[MemorySearchResult]],
    *,
    query: str,
    client: Optional[RecallBackend],
    config: CogneeConfig,
    limit: int,
    query_tags: Optional[list[str]] = None,
    deep: bool = False,
    node_names: Optional[list[str]] = None,
    tag_query: TagQuery | None = None,
    overview: bool = False,
    diagnostics: RecallDiagnostics | None = None,
    on_results: Callable[[list[MemorySearchResult]], None] | None = None,
) -> list[MemorySearchResult]:
    """并行搜索原生后端和 Cognee，失败时透明降级。

    deep=True 时使用 config.deep_search_types（含图谱类检索）替代
    config.search_types，覆盖更广但更慢；仅用于主动深度召回。
    node_names 非空时追加定向检索通道（cognee node_name 限定到
    提及实体的事实块，聊天对象明确时精度显著高于泛化检索）。
    """
    diagnostics = diagnostics or RecallDiagnostics()
    if not config.enabled or not config.recall_enabled or client is None or not client.projection_ready:
        diagnostics.cognee = ("disabled" if not config.enabled or not config.recall_enabled else
                              "unavailable" if client is None else "index_not_ready")
        results = await native_search
        diagnostics.native = "complete"
        return results[:limit]

    datasets = datasets_for_projection(config, client.projection_namespace)
    node_names = canonical_tags([*(query_tags or []), *(node_names or [])])
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
        try:
            native = await native_search
            diagnostics.native = "complete"
        except BaseException:
            diagnostics.native = "incomplete"
            raise
        publish()

    async def consume(batch: CogneeSearchBatch) -> None:
        if batch.status == "done":
            batches[batch.lane] = batch.results
            publish()

    tasks = [asyncio.create_task(read_native()), asyncio.create_task(
        search_cognee(client, config, query, datasets, limit, search_types,
                      node_names=node_names, on_batch=consume, tag_query=tag_query, overview=overview,
                      diagnostics=diagnostics),
        name="memory.cognee.recall",
    )]
    try:
        budget = get_config_float("memory_probe_timeout_seconds" if deep else "memory_recall_timeout_seconds",
                                  60.0 if deep else 5.0)
        done, _pending = await asyncio.wait(tasks, timeout=max(0.1, budget))
        diagnostics.timed_out = bool(_pending)
        for task in done:
            if not task.cancelled() and (error := task.exception()):
                log(f"联邦召回通道失败，保留已完成结果: {error}", "DEBUG", tag="思维")
        return snapshot()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def search_cognee(
    client: RecallBackend,
    config: CogneeConfig,
    query: str,
    datasets: list[str],
    limit: int,
    search_types: Optional[list[str]] = None,
    *,
    node_names: Optional[list[str]] = None,
    on_batch: Callable[[CogneeSearchBatch], Awaitable[None]] | None = None,
    tag_query: TagQuery | None = None,
    overview: bool = False,
    diagnostics: RecallDiagnostics | None = None,
) -> list[MemorySearchResult]:
    """cognee 检索的统一拼装点：类型 × 数据集并行 recall + 定向通道 + 归一化。

    联邦召回（federated_search）与异步深探（probe）共用本入口——
    cognee 调用模式只在此处拼装，外层不再各自组 client.recall。
    每条通道完成即可通过 on_batch 消费，慢通道不阻塞已完成结果的呈现。
    """
    diagnostics = diagnostics or RecallDiagnostics()
    if not client.projection_ready:
        diagnostics.cognee = "index_not_ready"
        return []
    if client.projection_namespace:
        allowed = set(datasets_for_projection(config, client.projection_namespace))
        datasets = [name for name in datasets if name in allowed]
    strict = tag_query is not None and tag_query.active
    node_names = [] if strict else canonical_tags(node_names or [])
    availability = await client.initialize()
    if not availability.ready:
        diagnostics.cognee = "unavailable"
        return []
    datasets = await client.available_datasets(datasets)
    if not datasets:
        diagnostics.cognee = "no_datasets"
        return []
    pool_size = max(limit, limit * config.recall_pool_multiplier)
    lanes: list[tuple[str, Callable[[], Awaitable[list[CogneeRecallItem]]]]] = []
    summary_lanes: set[str] = set()
    targeted_type = None
    if node_names:
        try:
            targeted_type = client.search_type("CHUNKS")
        except (AttributeError, RuntimeError):
            targeted_type = None
    datasets = list(dict.fromkeys(datasets))
    selected_types = ["CHUNKS"] if strict else (search_types or config.search_types)
    exact_required = [tag for tag in tag_query.all_of if not tag.endswith("/*")] if tag_query else []
    filters: dict[str, Any] = {"node_name": exact_required, "node_name_filter_operator": "AND"} if exact_required else {}
    for search_type_name in dict.fromkeys(selected_types):
        try:
            search_type = client.search_type(search_type_name)
        except (AttributeError, RuntimeError):
            continue
        for dataset_name in datasets:
            use_summary = overview and search_type_name == "GRAPH_COMPLETION" and client.global_context_ready(dataset_name)
            if use_summary:
                summary_lanes.add(f"{search_type_name}/{dataset_name}")
            summary_options = {"retriever_specific_config": {"include_global_context_index": True}} if use_summary else {}
            lanes.append((f"{search_type_name}/{dataset_name}", partial(client.recall,
                query,
                query_type=search_type,
                **filters,
                **summary_options,
                datasets=[dataset_name],
                top_k=pool_size,
                auto_route=False,
                only_context=not use_summary and search_type_name not in {"CHUNKS", "CHUNKS_LEXICAL", "SUMMARIES"},
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
            diagnostics.failed_lanes += 1
            detail = f"{type(exc).__name__}: {str(exc) or '检索未完成'}"
            elapsed = time.monotonic() - started
            log(f"Cognee 检索通道失败: {lane}, {elapsed:.1f}s, {detail}", "DEBUG", tag="记忆")
            if on_batch is not None:
                await on_batch(CogneeSearchBatch(lane, "error", elapsed, error=detail))
            return []
        results: list[MemorySearchResult] = []
        for item in items:
            projection = recall_projection(item)
            if strict and (item.source != "cognee_chunk" or "tags" not in projection
                           or tag_query is None or not tag_query.matches(projection["tags"])):
                continue
            provenance = {**item.metadata, **projection.pop("provenance", {})}
            provenance.pop("anelf_attribution", None)
            projection["snippet"] = excerpt(str(projection["snippet"]), 700)
            results.append(MemorySearchResult.model_validate({
                **projection, "id": item.id, "score": item.score, "source": item.source,
                "dataset_id": item.dataset_id, "dataset_name": item.dataset_name,
                "provenance": provenance,
            }))
        elapsed = time.monotonic() - started
        diagnostics.completed_lanes += 1
        diagnostics.summary_used |= bool(results) and lane in summary_lanes
        log(f"Cognee 检索通道完成: {lane}, {elapsed:.1f}s, {len(results)} 条", "DEBUG", tag="记忆")
        if on_batch is not None:
            await on_batch(CogneeSearchBatch(lane, "done", elapsed, results))
        return results

    async with query_embeddings():
        tasks = [asyncio.create_task(consume(lane, call), name=f"cognee.search.{lane}") for lane, call in lanes]
        try:
            batches = await asyncio.gather(*tasks)
            diagnostics.cognee = "partial" if diagnostics.failed_lanes else "complete"
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
    conflicts: dict[str, list[Any]] = {}
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
            warnings = result.provenance.get("conflicts")
            if isinstance(warnings, list):
                merged = conflicts.setdefault(key, [])
                for warning in warnings:
                    if warning not in merged and len(merged) < 5:
                        merged.append(warning)
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
        if key in conflicts:
            result.provenance["conflicts"] = conflicts[key]
        output.append(result)
    return output


def datasets_for_projection(
    config: CogneeConfig,
    namespace: str = "",
) -> list[str]:
    """返回同一代际的内容分区，供真实检索与诊断共用。"""
    prefix = namespace or config.dataset_prefix
    return [projection_dataset(prefix), projection_dataset(prefix, graph=True)]


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
