"""删除窗口内复用来源定位、合并检查点与关系类型收尾。

公开删除仍逐篇授权、加锁和实时判定归属；候选事务发布前完成全部收尾。

模型看到什么：不改变模型输入、召回正文或标签。
token 影响：不调用模型；减少派生索引删除中的重复扫描。
缓存影响：缓存仅存在于候选事务的删除阶段，不触碰对话前缀。
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any
from uuid import UUID

from .ladybug_guard import CheckpointWindow, install_checkpoints

NodeRefs = dict[str, list[str]]
EdgeRefs = dict[Any, list[str]]
_active: ContextVar[DeletionWindow | None] = ContextVar("cognee_deletion_window", default=None)


class DeletionWindow:
    """只保留本轮目标定位与候选关系类型；成功收尾前不批准发布。"""

    def __init__(self) -> None:
        self._documents: dict[tuple[int, str, str], tuple[NodeRefs, EdgeRefs]] = {}
        self._pending: set[tuple[str, str]] = set()
        self._checkpoints = CheckpointWindow()
        self._edge_types: dict[int, tuple[Any, Any, set[str]]] = {}

    def includes(self, dataset_id: str, data_id: str) -> bool:
        return (dataset_id, data_id) in self._pending

    async def prepare(self, targets: list[tuple[str, str]]) -> None:
        from cognee.context_global_variables import set_database_global_context_variables
        from cognee.infrastructure.databases.provenance import parse_source_ref_key
        from cognee.infrastructure.databases.provenance.markers import stores_provenance_in_graph
        from cognee.infrastructure.databases.unified import get_unified_engine

        self._pending = {(dataset, document) for dataset, document in targets}
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

    async def completed(self, dataset_id: str, data_id: str) -> None:
        """公开删除成功后确认目标；最后一篇统一收尾，异常仍由外层事务恢复。"""
        target = (dataset_id, data_id)
        if target not in self._pending:
            return
        if len(self._pending) > 1:
            self._pending.remove(target)
            return
        with self.scope():
            for graph, vector, candidates in self._edge_types.values():
                await _prune_edge_types(graph, vector, candidates)
        await self._checkpoints.flush()
        self._pending.remove(target)
        self._documents.clear()
        self._edge_types.clear()

    @contextmanager
    def scope(self) -> Iterator[None]:
        token = _active.set(self)
        try:
            with self._checkpoints.scope():
                yield
        finally:
            _active.reset(token)


def install() -> None:
    """仅在宿主删除窗口适配 SDK；窗口外保持原始检查点及收尾行为。"""
    from cognee.infrastructure.databases.graph.ladybug.adapter import LadybugAdapter
    from cognee.infrastructure.databases.unified import provenance_delete_planner as planner
    from cognee.infrastructure.databases.unified.provenance_delete_planner import execute_source_ref_removal
    from cognee.infrastructure.databases.unified.unified_store_engine import UnifiedStoreEngine
    from cognee.modules.graph.utils.prepare_edges_for_storage import get_edge_retrieval_text

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

    install_checkpoints(LadybugAdapter)
    original_cleanup = planner._cleanup_orphaned_edge_types

    @wraps(original_cleanup)
    async def cleanup(graph_engine: Any, vector_engine: Any, unowned_edges: Any, edge_data: Any) -> None:
        graph, vector = graph_engine, vector_engine
        window = _active.get()
        if window is None or not isinstance(graph, LadybugAdapter):
            await original_cleanup(graph, vector, unowned_edges, edge_data)
            return
        if not unowned_edges:
            return
        _, _, texts = window._edge_types.setdefault(id(graph), (graph, vector, set()))
        texts.update(text for edge in unowned_edges
                     if (text := get_edge_retrieval_text(edge_data[edge].edge_text, edge.relationship_name)))

    planner._cleanup_orphaned_edge_types = cleanup


async def _prune_edge_types(graph: Any, vector: Any, candidates: set[str]) -> None:
    """只读取关系文本列，一轮判定孤立类型；共享文本仍有任一关系使用就保留。"""
    from cognee.modules.graph.models.EdgeType import EdgeType
    from cognee.modules.graph.utils.prepare_edges_for_storage import get_edge_retrieval_text

    if not candidates:
        return
    rows = await graph.query(
        "MATCH ()-[r:EDGE]->() RETURN DISTINCT r.relationship_name, "
        "CAST(json_extract(r.properties, '$.edge_text') AS STRING)",
    )
    orphaned = {str(EdgeType.id_for(text)) for text in candidates}
    for name, text in rows:
        label = get_edge_retrieval_text(json.loads(text) if text else None, name)
        if label:
            orphaned.discard(str(EdgeType.id_for(label)))
    if orphaned:
        identities = sorted(orphaned)
        await graph.delete_nodes(identities)
        if await vector.has_collection("EdgeType_relationship_name"):
            await vector.delete_data_points("EdgeType_relationship_name", identities)
