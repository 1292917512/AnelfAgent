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

#: 投影文档头字段（coordinator._render_memory 的输出格式）
_PROJECTION_HEADER_KEYS = ("Memory type", "Source", "Importance", "Tags", "Metadata")


def parse_memory_projection(text: str) -> tuple[str, list[str]]:
    """解析投影文档：剥离头部字段，返回 (干净正文, 标签列表)。

    coordinator 把记忆投影为 ``Memory type/Source/Importance/Tags/Metadata``
    头部 + 正文的文档；检索回来后按同一格式解析——正文供注入展示（不漏
    头部噪音），标签回填结果（归属标注/上下文加权/联想种子因此对 cognee
    结果同样生效）。非投影内容（图谱综合/实体摘要等）原样返回。
    """
    if not text.startswith("Memory type:"):
        return text, []
    head, _, content = text.partition("\n\n")
    fields: dict[str, str] = {}
    for line in head.split("\n"):
        for key in _PROJECTION_HEADER_KEYS:
            if line.startswith(f"{key}: "):
                fields[key] = line[len(key) + 2:].strip()
                break
    tags = [tag.strip() for tag in fields.get("Tags", "").split(",") if tag.strip()]
    return (content or text).strip(), tags


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
    cognee_task = asyncio.create_task(
        search_cognee(client, config, query, datasets, limit, search_types,
                      node_names=node_names),
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


async def search_cognee(
    client: CogneeClient,
    config: CogneeConfig,
    query: str,
    datasets: list[str],
    limit: int,
    search_types: Optional[list[str]] = None,
    *,
    node_names: Optional[list[str]] = None,
) -> list[MemorySearchResult]:
    """cognee 检索的统一拼装点：类型 × 数据集并行 recall + 定向通道 + 归一化。

    联邦召回（federated_search）与异步深探（probe）共用本入口——
    cognee 调用模式只在此处拼装，外层不再各自组 client.recall。
    不支持的检索类型静默跳过（向后兼容）。
    """
    availability = await client.initialize()
    if not availability.ready:
        return []
    pool_size = max(limit, limit * config.recall_pool_multiplier)
    tasks: list[Awaitable] = []
    targeted_type = None
    if node_names:
        try:
            targeted_type = client.search_type("CHUNKS")
        except (AttributeError, RuntimeError):
            targeted_type = None
    for search_type_name in search_types or config.search_types:
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
    # 定向检索通道：node_name 限定到提及实体的事实块（每数据集一次，
    # 不随 search_types 翻倍——定向精度来自节点过滤而非查询类型）
    if targeted_type is not None:
        for dataset_name in datasets:
            tasks.append(client.recall(
                query,
                query_type=targeted_type,
                datasets=[dataset_name],
                top_k=pool_size,
                node_name=list(node_names or []),
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
            content, tags = parse_memory_projection(item.content)
            results.append(MemorySearchResult(
                id=item.id,
                snippet=content[:700],
                score=item.score,
                source=item.source,
                tags=tags,
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
            key = dedupe_key(result)
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
    """统一去重键：优先投影回链 id（anelf_memory_id），否则内容归一哈希。

    跨通道防重复的权威实现——RRF 融合、多查询共识合并共用。
    """
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
