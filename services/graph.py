"""关系图谱服务 -- Web API 与 AI 工具共用 GraphStore 权威实现（行为一致）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services._runtime import require_runtime


def _graph():
    rt = require_runtime()
    store = rt.mind.memory_store
    if store is None:
        raise RuntimeError("记忆系统未初始化")
    return store.graph


class GraphService:

    async def get_graph(
        self,
        *,
        predicate: str = "",
        origin: str = "",
        include_archived: bool = False,
        limit: int = 500,
    ) -> Dict[str, Any]:
        """全图数据（拓扑图数据源）：边列表 + 涉及节点 + 统计。"""
        graph = _graph()
        edges = await graph.list_edges(
            predicate=predicate, origin=origin,
            include_archived=include_archived, limit=limit,
        )
        node_ids: set[int] = set()
        for edge in edges:
            node_ids.add(edge["subject"]["id"])
            node_ids.add(edge["object"]["id"])
        nodes = [await graph.get_node_by_id(nid) for nid in sorted(node_ids)]
        stats = await graph.stats()
        return {
            "nodes": [n for n in nodes if n is not None],
            "edges": edges,
            "stats": stats,
        }

    async def query_neighborhood(
        self, node_key: str, *, depth: int = 1,
    ) -> Dict[str, Any]:
        return await _graph().query_relations(node_key, depth=depth)

    async def get_node_detail(self, node_key: str) -> Dict[str, Any]:
        """节点详情：图谱节点 + 实体画像联动（画像/对话计数/主身份/别名列表）。

        user/group 节点经别名归一到主身份后取画像，与图谱写入侧的归一逻辑一致。
        """
        graph = _graph()
        node = await graph.get_node(node_key)
        if not node:
            return {"found": False}
        result: Dict[str, Any] = {"found": True, "node": node}
        if node["node_type"] not in ("user", "group"):
            return result

        scope_type = node["node_type"]
        scope_id = node["node_key"].split(":", 1)[1]
        sqlite = require_runtime().data_center.sqlite
        primary = await sqlite.resolve_alias(scope_type, scope_id)
        p_type, p_id = primary if primary else (scope_type, scope_id)
        result["primary"] = f"{p_type}:{p_id}"
        profile = await sqlite.get_entity_personality(scope_type=p_type, scope_id=p_id)
        result["profile"] = profile
        aliases = await sqlite.get_aliases_for_primary(p_type, p_id)
        result["aliases"] = [f"{a['scope_type']}:{a['scope_id']}" for a in aliases]
        return result

    async def upsert_node(
        self, node_key: str, *, label: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await _graph().upsert_node(node_key, label=label, metadata=metadata)

    async def delete_node(self, node_key: str) -> bool:
        return await _graph().set_node_archived(node_key, True)

    async def add_edge(
        self,
        subject: str,
        predicate: str,
        object: str,
        *,
        symmetric: bool = False,
        strength: float = 0.7,
        evidence: str = "",
    ) -> Dict[str, Any]:
        return await _graph().add_relation(
            subject, predicate, object,
            symmetric=symmetric, strength=strength, evidence=evidence,
            origin="web_ui",
        )

    async def update_edge(self, edge_id: int, **changes: Any) -> Optional[Dict[str, Any]]:
        return await _graph().update_relation(edge_id, **changes)

    async def delete_edge(self, edge_id: int) -> bool:
        return await _graph().set_relation_archived(edge_id, True)

    async def merge_nodes(self, source_key: str, target_key: str) -> Dict[str, Any]:
        return await _graph().merge_nodes(source_key, target_key)
