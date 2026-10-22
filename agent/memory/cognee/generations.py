"""按持久化代际归并已确认目录；不打开 SDK，不推测未记录的历史验收状态。"""

from __future__ import annotations

from typing import TypedDict

from ..store.projection_state import ProjectionState
from .records import DatasetCatalog, DatasetRecord
from .storage import DatasetStorageBytes, StorageStatsDict


def dataset_namespace(name: str, state: ProjectionState) -> str:
    """最长匹配避免初始前缀吞并后续构建轮次。"""
    for prefix in sorted({state.namespace, *state.previous_namespaces}, key=len, reverse=True):
        if name.startswith(prefix + "_"):
            return prefix
    return ""


class GenerationRecord(TypedDict):
    namespace: str
    current: bool
    phase: str
    datasets: list[DatasetRecord]
    document_count: int | None
    processed_count: int | None
    storage: DatasetStorageBytes | None


def generation_catalog(catalog: DatasetCatalog, state: ProjectionState | None,
                       storage: StorageStatsDict) -> list[GenerationRecord]:
    """只统计独立图/向量索引；未知数量和占用保持未知，快照及共享文件不重复摊分。"""
    if state is None:
        return []
    namespaces = list(dict.fromkeys([state.namespace, *reversed(state.previous_namespaces)]))
    groups: dict[str, list[DatasetRecord]] = {name: [] for name in namespaces}
    for row in catalog["items"]:
        owner = dataset_namespace(row["name"], state)
        if owner:
            groups[owner].append(row)
    sizes = storage.get("datasets", {})
    result: list[GenerationRecord] = []
    for namespace, rows in groups.items():
        known_counts = catalog["ready"] and all("document_count" in row and "processed_count" in row for row in rows)
        known_sizes = catalog["ready"] and bool(storage.get("computed_at")) and all(row["id"] in sizes for row in rows)
        size = DatasetStorageBytes(total_bytes=0, graph_bytes=0, lance_bytes=0) if known_sizes else None
        if size is not None:
            for row in rows:
                value = sizes[row["id"]]
                size["total_bytes"] += value["total_bytes"]
                size["graph_bytes"] += value["graph_bytes"]
                size["lance_bytes"] += value["lance_bytes"]
        result.append(GenerationRecord(
            namespace=namespace, current=namespace == state.namespace,
            phase=state.phase if namespace == state.namespace else "unknown",
            datasets=rows, storage=size,
            document_count=sum(row["document_count"] for row in rows) if known_counts else None,
            processed_count=sum(row["processed_count"] for row in rows) if known_counts else None,
        ))
    return result
