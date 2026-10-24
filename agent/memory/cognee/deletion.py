"""删除窗口内复用来源定位，所有归属判定和写入仍委托 Cognee 原生规划器。

模型看到什么：不改变模型输入、召回正文或标签。
token 影响：不调用模型；减少派生索引删除中的重复扫描。
缓存影响：缓存仅存在于候选事务的删除阶段，不触碰对话前缀。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any
from uuid import UUID

NodeRefs = dict[str, list[str]]
EdgeRefs = dict[Any, list[str]]
_active: ContextVar[DeletionReads | None] = ContextVar("cognee_deletion_reads", default=None)


class DeletionReads:
    """只缓存目标定位；共享归属每次从图中重读，删除结束即回收对应缓存。"""

    def __init__(self) -> None:
        self._documents: dict[tuple[int, str, str], tuple[NodeRefs, EdgeRefs]] = {}

    async def prepare(self, targets: list[tuple[str, str]]) -> None:
        from cognee.context_global_variables import set_database_global_context_variables
        from cognee.infrastructure.databases.provenance import parse_source_ref_key
        from cognee.infrastructure.databases.provenance.markers import stores_provenance_in_graph
        from cognee.infrastructure.databases.unified import get_unified_engine

        grouped: dict[str, set[str]] = defaultdict(set)
        for dataset, document in targets:
            grouped[dataset].add(document)
        for dataset, documents in grouped.items():
            if len(documents) < 2:
                continue
            async with set_database_global_context_variables(UUID(dataset)):
                unified = await get_unified_engine()
                if not unified.supports_graph_provenance_delete() or not await stores_provenance_in_graph(unified.graph):
                    continue
                graph = unified.graph
                selected: dict[str, tuple[NodeRefs, EdgeRefs]] = {document: ({}, {}) for document in documents}
                # 同一 Ladybug 执行器仍按其锁读取；不创建无界并发任务。
                readers = (graph.find_node_source_refs_by_dataset, graph.find_edge_source_refs_by_dataset)
                for index, read in enumerate(readers):
                    for identity, keys in (await read(dataset)).items():
                        for key in keys:
                            try:
                                parsed = parse_source_ref_key(key)
                            except ValueError:
                                continue
                            document = str(parsed.data_id)
                            if str(parsed.dataset_id) == dataset and document in selected:
                                selected[document][index].setdefault(identity, []).append(key)
                for document, refs in selected.items():
                    self._documents[id(graph), dataset, document] = refs

    @contextmanager
    def scope(self) -> Iterator[None]:
        token = _active.set(self)
        try:
            yield
        finally:
            _active.reset(token)


def install() -> None:
    """只替换删除前的来源定位；不修改 SDK 文件、锁、授权或删除顺序。"""
    from cognee.infrastructure.databases.unified.provenance_delete_planner import execute_source_ref_removal
    from cognee.infrastructure.databases.unified.unified_store_engine import UnifiedStoreEngine

    original = UnifiedStoreEngine.delete_by_document
    if getattr(original, "_anelf_deletion_reads", False):
        return

    @wraps(original)
    async def delete_document(engine: Any, dataset_id: str, data_id: str) -> Any:
        cache = _active.get()
        if cache is None or not engine.supports_graph_provenance_delete():
            return await original(engine, dataset_id, data_id)
        key = (id(engine.graph), str(dataset_id), str(data_id))
        refs = cache._documents.get(key)
        if refs is None:
            return await original(engine, dataset_id, data_id)
        nodes, edges = refs
        # 这里只复用候选身份；实时重读所有来源，不能用旧快照判断共享节点是否可删。
        node_data = await engine.graph.get_node_delete_data(list(nodes))
        edge_data = await engine.graph.get_edge_delete_data(list(edges))
        result = await execute_source_ref_removal(engine.graph, engine.vector,
            node_data=node_data, edge_data=edge_data, refs_by_node=nodes, refs_by_edge=edges)
        cache._documents.pop(key, None)
        return result

    delete_document.__dict__["_anelf_deletion_reads"] = True
    UnifiedStoreEngine.delete_by_document = delete_document
