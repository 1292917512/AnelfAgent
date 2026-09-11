"""关系图谱权威存储：graph_nodes / graph_edges 两表的全部读写。

节点分两类：
- 实体型：user/group，node_key 与记忆标签同构（``user:{adapter}:{uid}``），
  写入时经注入的 alias_resolver 归一到主身份（跨频道同一人天然同节点）；
- 自由型：person/topic/project/org/thing/concept 等，承载未入库人物与抽象概念。

边以 (subject, predicate, object) 唯一，重复写入即更新；软删（archived）可恢复。
任何变更在同一事务内向 cognee outbox 入队节点级投影（邻域文档先删后加）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import aiosqlite

from ..store.cognee_queue import ENTRY_KIND_GRAPH_NODE, CogneeSyncQueue
from ..store.connection import MemoryConnectionManager

# 内置节点类型（key 前缀）；未知前缀归为 custom
NODE_TYPES = ("user", "group", "person", "topic", "project", "org", "thing", "concept")

# 边的来源：manual_tool（AI 工具）/ heartbeat_extract（心跳抽取）/ web_ui（前端）
EDGE_ORIGINS = ("manual_tool", "heartbeat_extract", "web_ui")

_KEY_RE = re.compile(r"^(?P<ntype>[a-z][a-z0-9_]*):(?P<name>\S.*)$")
_MAX_PREDICATE_LEN = 64
_MAX_EVIDENCE_LEN = 500

# 别名解析器：(scope_type, scope_id) -> (primary_type, primary_id) | None
AliasResolver = Callable[[str, str], Awaitable[Optional[tuple[str, str]]]]


def parse_node_key(node_key: str) -> tuple[str, str]:
    """解析 node_key 为 (node_type, name)；未知前缀归为 custom。非法时抛 ValueError。"""
    key = (node_key or "").strip()
    m = _KEY_RE.match(key)
    if not m:
        raise ValueError(
            f"非法节点标识: {node_key!r}（格式应为 类型:名称，如 user:qq:123 / person:老王）"
        )
    ntype = m.group("ntype")
    if ntype not in NODE_TYPES:
        ntype = "custom"
    return ntype, m.group("name").strip()


def _brief(node: Dict[str, Any]) -> Dict[str, Any]:
    """节点的边内嵌简介。"""
    return {
        "id": node["id"],
        "node_key": node["node_key"],
        "node_type": node["node_type"],
        "label": node["label"],
    }


def format_triple(edge: Dict[str, Any]) -> str:
    """把边格式化为紧凑三元组文本（AI 注入与工具返回的统一记号）。"""
    s, o = edge["subject"], edge["object"]
    s_name = f"{s['label']}({s['node_key']})" if s["label"] else s["node_key"]
    o_name = f"{o['label']}({o['node_key']})" if o["label"] else o["node_key"]
    mid = f"{edge['predicate']}│{edge['strength']:.2f}"
    if edge["symmetric"]:
        return f"{s_name} ─[{mid}]─ {o_name}"
    return f"{s_name} ─[{mid}]→ {o_name}"


class GraphStore:
    """关系图谱存储：节点/边 CRUD + 邻域/路径查询 + cognee 投影入队。"""

    def __init__(
        self,
        conn: MemoryConnectionManager,
        cognee_queue: Optional[CogneeSyncQueue] = None,
    ) -> None:
        self._conn = conn
        self._cognee = cognee_queue
        self._alias_resolver: Optional[AliasResolver] = None

    def set_alias_resolver(self, resolver: Optional[AliasResolver]) -> None:
        """注入实体别名解析器（业务事务外，仅用于写路径的 key 归一）。"""
        self._alias_resolver = resolver

    # ------------------------------------------------------------------
    # 内部助手
    # ------------------------------------------------------------------

    async def _normalize_entity_key(self, node_key: str) -> str:
        """user/group 节点 key 归一：去子会话后缀 + 别名归一到主身份。"""
        ntype, name = parse_node_key(node_key)
        if ntype not in ("user", "group"):
            return node_key.strip()
        base = name.split("#", 1)[0]
        if self._alias_resolver is not None:
            try:
                primary = await self._alias_resolver(ntype, base)
            except Exception:
                primary = None
            if primary:
                return f"{primary[0]}:{primary[1]}"
        return f"{ntype}:{base}"

    @staticmethod
    def _row_to_node(row: aiosqlite.Row) -> Dict[str, Any]:
        try:
            metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return {
            "id": int(row["id"]),
            "node_key": str(row["node_key"]),
            "node_type": str(row["node_type"]),
            "label": str(row["label"]),
            "metadata": metadata,
            "created": row["created_ns"] / 1e9,
            "updated": row["updated_ns"] / 1e9,
            "archived": bool(row["archived"]),
        }

    @staticmethod
    def _row_to_edge(row: aiosqlite.Row) -> Dict[str, Any]:
        try:
            metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        keys = row.keys()
        return {
            "id": int(row["id"]),
            "subject": {
                "id": int(row["subject_id"]),
                "node_key": str(row["s_key"]),
                "node_type": str(row["s_type"]),
                "label": str(row["s_label"]),
            },
            "predicate": str(row["predicate"]),
            "object": {
                "id": int(row["object_id"]),
                "node_key": str(row["o_key"]),
                "node_type": str(row["o_type"]),
                "label": str(row["o_label"]),
            },
            "symmetric": bool(row["symmetric"]),
            "strength": float(row["strength"]),
            "evidence": str(row["evidence"]),
            "source_memory_id": row["source_memory_id"],
            "origin": str(row["origin"]),
            "metadata": metadata,
            "created": row["created_ns"] / 1e9,
            "updated": row["updated_ns"] / 1e9,
            "archived": bool(row["archived"]),
            "access_count": int(row["access_count"]) if "access_count" in keys else 0,
            "last_accessed": (row["last_accessed_ns"] / 1e9) if "last_accessed_ns" in keys else 0.0,
        }

    _EDGE_SELECT = (
        "SELECT e.*, s.node_key AS s_key, s.node_type AS s_type, s.label AS s_label, "
        "o.node_key AS o_key, o.node_type AS o_type, o.label AS o_label "
        "FROM graph_edges e "
        "JOIN graph_nodes s ON e.subject_id = s.id "
        "JOIN graph_nodes o ON e.object_id = o.id"
    )

    async def _get_node_row(
        self, db: aiosqlite.Connection, node_key: str
    ) -> Optional[Dict[str, Any]]:
        cursor = await db.execute(
            "SELECT * FROM graph_nodes WHERE node_key=?", (node_key,)
        )
        row = await cursor.fetchone()
        return self._row_to_node(row) if row else None

    async def _ensure_node(
        self, db: aiosqlite.Connection, node_key: str, label: str = ""
    ) -> Dict[str, Any]:
        """按 key 取节点，不存在则建档（类型从 key 前缀推断）。"""
        key = await self._normalize_entity_key(node_key)
        node = await self._get_node_row(db, key)
        if node:
            if label and label != node["label"]:
                await db.execute(
                    "UPDATE graph_nodes SET label=?, updated_ns=? WHERE id=?",
                    (label, time.time_ns(), node["id"]),
                )
                node["label"] = label
            if node["archived"]:
                await db.execute(
                    "UPDATE graph_nodes SET archived=0, updated_ns=? WHERE id=?",
                    (time.time_ns(), node["id"]),
                )
                node["archived"] = False
            return node
        ntype, name = parse_node_key(key)
        now_ns = time.time_ns()
        # ON CONFLICT 兜底并发建档：另一协程可能已插入同 key 节点，冲突时重读
        cursor = await db.execute(
            "INSERT INTO graph_nodes(node_key, node_type, label, metadata_json, "
            "created_ns, updated_ns, archived) VALUES(?,?,?,'{}',?,?,0) "
            "ON CONFLICT(node_key) DO NOTHING",
            (key, ntype, (label or name).strip(), now_ns, now_ns),
        )
        if (cursor.rowcount or 0) > 0:
            return {
                "id": int(cursor.lastrowid or 0),
                "node_key": key,
                "node_type": ntype,
                "label": (label or name).strip(),
                "metadata": {},
                "created": now_ns / 1e9,
                "updated": now_ns / 1e9,
                "archived": False,
            }
        node = await self._get_node_row(db, key)
        if node is None:
            raise RuntimeError(f"图谱节点写入后读取失败: {key}")
        return node

    async def _neighbor_node_ids(self, db: aiosqlite.Connection, node_id: int) -> list[int]:
        cursor = await db.execute(
            "SELECT subject_id, object_id FROM graph_edges "
            "WHERE subject_id=? OR object_id=?",
            (node_id, node_id),
        )
        ids: set[int] = set()
        for row in await cursor.fetchall():
            ids.add(int(row["subject_id"]))
            ids.add(int(row["object_id"]))
        ids.discard(node_id)
        return sorted(ids)

    async def _enqueue_projection(
        self,
        db: aiosqlite.Connection,
        upsert_ids: list[int],
        delete_ids: Optional[list[int]] = None,
    ) -> None:
        """向 cognee outbox 入队节点投影（邻域文档先删后加）。"""
        if self._cognee is None:
            return
        for nid in dict.fromkeys(upsert_ids):
            await self._cognee.enqueue_sync(
                db, nid, "upsert", {"node_id": nid}, entry_kind=ENTRY_KIND_GRAPH_NODE,
            )
        for nid in dict.fromkeys(delete_ids or []):
            await self._cognee.enqueue_sync(
                db, nid, "delete", {"node_id": nid}, entry_kind=ENTRY_KIND_GRAPH_NODE,
            )

    async def _edges_for_node_ids(
        self,
        db: aiosqlite.Connection,
        node_ids: list[int],
        *,
        include_archived: bool = False,
        predicate: str = "",
    ) -> list[Dict[str, Any]]:
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        sql = (
            f"{self._EDGE_SELECT} WHERE (e.subject_id IN ({placeholders}) "
            f"OR e.object_id IN ({placeholders}))"
        )
        params: list[Any] = [*node_ids, *node_ids]
        if not include_archived:
            sql += " AND e.archived=0"
        if predicate:
            sql += " AND e.predicate=?"
            params.append(predicate)
        sql += " ORDER BY e.updated_ns DESC"
        cursor = await db.execute(sql, params)
        return [self._row_to_edge(row) for row in await cursor.fetchall()]

    # ------------------------------------------------------------------
    # 节点 CRUD
    # ------------------------------------------------------------------

    async def upsert_node(
        self,
        node_key: str,
        *,
        label: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建或更新节点（label/metadata 非空才覆盖）。返回节点 dict。"""
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            node = await self._ensure_node(db, node_key, label)
            if metadata:
                merged = {**node["metadata"], **metadata}
                await db.execute(
                    "UPDATE graph_nodes SET metadata_json=?, updated_ns=? WHERE id=?",
                    (json.dumps(merged, ensure_ascii=False), time.time_ns(), node["id"]),
                )
                node["metadata"] = merged
            neighbors = await self._neighbor_node_ids(db, node["id"])
            await self._enqueue_projection(db, [node["id"], *neighbors])
        return node

    async def get_node(self, node_key: str) -> Optional[Dict[str, Any]]:
        db = await self._conn.get_db()
        key = await self._normalize_entity_key(node_key)
        return await self._get_node_row(db, key)

    async def get_nodes_by_keys(self, node_keys: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """按键批量取节点（含别名归一），返回 {原始 key: node 或 None}。

        单条 IN 查询替代逐键往返（召回后处理的实体标签人类化场景）。
        """
        if not node_keys:
            return {}
        db = await self._conn.get_db()
        norm_map: Dict[str, str] = {}
        for k in node_keys:
            norm_map[k] = await self._normalize_entity_key(k)
        unique_keys = sorted(set(norm_map.values()))
        placeholders = ",".join("?" for _ in unique_keys)
        cursor = await db.execute(
            f"SELECT * FROM graph_nodes WHERE node_key IN ({placeholders})",
            unique_keys,
        )
        found = {str(r["node_key"]): self._row_to_node(r) for r in await cursor.fetchall()}
        return {k: found.get(norm_map[k]) for k in node_keys}

    async def get_nodes_by_ids(self, node_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """按 id 批量取节点，返回 {id: node}（单条 IN 查询替代逐条往返）。"""
        if not node_ids:
            return {}
        db = await self._conn.get_db()
        unique_ids = sorted({int(i) for i in node_ids})
        placeholders = ",".join("?" for _ in unique_ids)
        cursor = await db.execute(
            f"SELECT * FROM graph_nodes WHERE id IN ({placeholders})",
            unique_ids,
        )
        return {int(r["id"]): self._row_to_node(r) for r in await cursor.fetchall()}

    async def get_node_by_id(self, node_id: int) -> Optional[Dict[str, Any]]:
        db = await self._conn.get_db()
        cursor = await db.execute("SELECT * FROM graph_nodes WHERE id=?", (node_id,))
        row = await cursor.fetchone()
        return self._row_to_node(row) if row else None

    async def list_nodes(
        self,
        *,
        node_type: str = "",
        query: str = "",
        include_archived: bool = False,
        limit: int = 200,
    ) -> list[Dict[str, Any]]:
        db = await self._conn.get_db()
        sql = "SELECT * FROM graph_nodes WHERE 1=1"
        params: list[Any] = []
        if not include_archived:
            sql += " AND archived=0"
        if node_type:
            sql += " AND node_type=?"
            params.append(node_type)
        if query:
            sql += " AND (node_key LIKE ? OR label LIKE ?)"
            like = f"%{query}%"
            params.extend([like, like])
        sql += " ORDER BY updated_ns DESC LIMIT ?"
        params.append(max(1, limit))
        cursor = await db.execute(sql, params)
        return [self._row_to_node(row) for row in await cursor.fetchall()]

    async def set_node_archived(self, node_key: str, archived: bool) -> bool:
        """归档/恢复节点。归档会级联归档其全部边；恢复只恢复节点本身。"""
        db = await self._conn.get_db()
        key = await self._normalize_entity_key(node_key)
        async with self._conn.tx(db):
            node = await self._get_node_row(db, key)
            if not node or node["archived"] == archived:
                return False
            now_ns = time.time_ns()
            await db.execute(
                "UPDATE graph_nodes SET archived=?, updated_ns=? WHERE id=?",
                (1 if archived else 0, now_ns, node["id"]),
            )
            neighbors = await self._neighbor_node_ids(db, node["id"])
            if archived:
                await db.execute(
                    "UPDATE graph_edges SET archived=1, updated_ns=? "
                    "WHERE (subject_id=? OR object_id=?) AND archived=0",
                    (now_ns, node["id"], node["id"]),
                )
                await self._enqueue_projection(db, neighbors, [node["id"]])
            else:
                await self._enqueue_projection(db, [node["id"], *neighbors])
        return True

    # ------------------------------------------------------------------
    # 边 CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_relation(predicate: str, strength: float, evidence: str) -> tuple[str, float, str]:
        pred = (predicate or "").strip()
        if not pred:
            raise ValueError("关系类型（predicate）不能为空")
        if len(pred) > _MAX_PREDICATE_LEN:
            raise ValueError(f"关系类型过长（>{_MAX_PREDICATE_LEN} 字符）")
        return pred, max(0.0, min(1.0, float(strength))), (evidence or "").strip()[:_MAX_EVIDENCE_LEN]

    async def add_relation(
        self,
        subject_key: str,
        predicate: str,
        object_key: str,
        *,
        subject_label: str = "",
        object_label: str = "",
        symmetric: bool = False,
        strength: float = 0.7,
        evidence: str = "",
        source_memory_id: Optional[int] = None,
        origin: str = "manual_tool",
    ) -> Dict[str, Any]:
        """新增关系：节点不存在自动建档；(s,p,o) 已存在则更新强度/证据（upsert）。"""
        pred, strength, evidence = self._validate_relation(predicate, strength, evidence)
        if origin not in EDGE_ORIGINS:
            origin = "manual_tool"
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            subject = await self._ensure_node(db, subject_key, subject_label)
            obj = await self._ensure_node(db, object_key, object_label)
            if subject["id"] == obj["id"]:
                raise ValueError("不允许建立自指向关系（subject 与 object 相同）")
            now_ns = time.time_ns()
            await db.execute(
                "INSERT INTO graph_edges(subject_id, predicate, object_id, symmetric, "
                "strength, evidence, source_memory_id, origin, metadata_json, "
                "created_ns, updated_ns, archived) VALUES(?,?,?,?,?,?,?,?,'{}',?,?,0) "
                "ON CONFLICT(subject_id, predicate, object_id) DO UPDATE SET "
                "symmetric=excluded.symmetric, strength=excluded.strength, "
                "evidence=excluded.evidence, source_memory_id=excluded.source_memory_id, "
                "origin=excluded.origin, updated_ns=excluded.updated_ns, archived=0",
                (subject["id"], pred, obj["id"], 1 if symmetric else 0, strength,
                 evidence, source_memory_id, origin, now_ns, now_ns),
            )
            await self._enqueue_projection(db, [subject["id"], obj["id"]])
            cursor = await db.execute(
                f"{self._EDGE_SELECT} WHERE e.subject_id=? AND e.predicate=? AND e.object_id=?",
                (subject["id"], pred, obj["id"]),
            )
            row = await cursor.fetchone()
        if row is None:  # 刚写入的边必然存在，防御性断言
            raise RuntimeError("关系边写入后读取失败")
        return self._row_to_edge(row)

    async def get_relation(self, edge_id: int) -> Optional[Dict[str, Any]]:
        db = await self._conn.get_db()
        cursor = await db.execute(
            f"{self._EDGE_SELECT} WHERE e.id=?", (edge_id,)
        )
        row = await cursor.fetchone()
        return self._row_to_edge(row) if row else None

    async def update_relation(
        self,
        edge_id: int,
        *,
        predicate: Optional[str] = None,
        strength: Optional[float] = None,
        evidence: Optional[str] = None,
        symmetric: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """原地更新边（None 表示不变）。目标三元组冲突时抛 ValueError。"""
        sets: list[str] = []
        params: list[Any] = []
        if predicate is not None:
            pred, _, _ = self._validate_relation(predicate, 0.0, "")
            sets.append("predicate=?")
            params.append(pred)
        if strength is not None:
            sets.append("strength=?")
            params.append(max(0.0, min(1.0, float(strength))))
        if evidence is not None:
            sets.append("evidence=?")
            params.append(evidence.strip()[:_MAX_EVIDENCE_LEN])
        if symmetric is not None:
            sets.append("symmetric=?")
            params.append(1 if symmetric else 0)
        if not sets:
            return await self.get_relation(edge_id)
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            existing = await self.get_relation(edge_id)
            if not existing:
                return None
            sets.append("updated_ns=?")
            params.append(time.time_ns())
            params.append(edge_id)
            try:
                await db.execute(
                    f"UPDATE graph_edges SET {', '.join(sets)} WHERE id=?",
                    params,
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("更新后的关系与既有关系重复（同主语+谓词+宾语）") from exc
            await self._enqueue_projection(
                db, [existing["subject"]["id"], existing["object"]["id"]],
            )
        return await self.get_relation(edge_id)

    async def set_relation_archived(self, edge_id: int, archived: bool) -> bool:
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            existing = await self.get_relation(edge_id)
            if not existing or existing["archived"] == archived:
                return False
            await db.execute(
                "UPDATE graph_edges SET archived=?, updated_ns=? WHERE id=?",
                (1 if archived else 0, time.time_ns(), edge_id),
            )
            await self._enqueue_projection(
                db, [existing["subject"]["id"], existing["object"]["id"]],
            )
        return True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def query_relations(
        self,
        node_key: str,
        *,
        depth: int = 1,
        predicate: str = "",
        include_archived: bool = False,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """邻域查询：以节点为中心向外扩展 depth 层（1~2），返回节点与边集合。"""
        db = await self._conn.get_db()
        node = await self._get_node_row(db, await self._normalize_entity_key(node_key))
        if not node:
            return {"found": False, "node": None, "nodes": [], "edges": []}
        depth = max(1, min(2, int(depth)))
        frontier = [node["id"]]
        seen_nodes = {node["id"]}
        all_edges: Dict[int, Dict[str, Any]] = {}
        for _ in range(depth):
            hop_edges = await self._edges_for_node_ids(
                db, frontier, include_archived=include_archived, predicate=predicate,
            )
            next_frontier: list[int] = []
            for edge in hop_edges:
                all_edges[edge["id"]] = edge
                for endpoint in (edge["subject"], edge["object"]):
                    if endpoint["id"] not in seen_nodes:
                        seen_nodes.add(endpoint["id"])
                        next_frontier.append(endpoint["id"])
            frontier = next_frontier
            if not frontier or len(all_edges) >= limit:
                break
        # 强度降序 + id 升序决胜：边更新只动 updated_ns 不影响次序，渲染字节稳定
        edges = sorted(all_edges.values(), key=lambda e: (-e["strength"], e["id"]))[:limit]
        await self._record_edge_access(db, [e["id"] for e in edges])
        node_ids = {node["id"]}
        for edge in edges:
            node_ids.add(edge["subject"]["id"])
            node_ids.add(edge["object"]["id"])
        node_map = await self.get_nodes_by_ids(sorted(node_ids))
        nodes = [node_map[nid] for nid in sorted(node_ids) if nid in node_map]
        return {"found": True, "node": node, "nodes": nodes, "edges": edges}

    async def _record_edge_access(self, db: aiosqlite.Connection, edge_ids: list[int]) -> None:
        """检索命中即计数（衰减访问护盾的数据源）；写失败静默（读路径优先）。"""
        if not edge_ids:
            return
        try:
            placeholders = ",".join("?" for _ in edge_ids)
            async with self._conn.tx(db):
                await db.execute(
                    f"UPDATE graph_edges SET access_count=access_count+1, last_accessed_ns=? "
                    f"WHERE id IN ({placeholders}) AND archived=0",
                    (time.time_ns(), *edge_ids),
                )
        except Exception:
            pass

    async def resolve_nodes_for_tags(
        self, names: list[str], *, limit: int = 4,
    ) -> list[Dict[str, Any]]:
        """实体名/话题词 → 图谱节点（label 或 node_key 子串匹配，更新近者优先）。

        供检索规划实体定向：计划实体名解析为节点后驱动邻域查询与
        cognee node_name 定向检索。空名单/无命中返回空。
        """
        found: Dict[str, Dict[str, Any]] = {}
        for name in names[:8]:
            name = (name or "").strip()
            if len(name) < 2:
                continue
            try:
                for node in await self.list_nodes(query=name, limit=3):
                    found.setdefault(str(node["node_key"]), node)
            except Exception:
                continue
            if len(found) >= limit:
                break
        return list(found.values())[:limit]

    async def edges_for_scopes(
        self,
        node_keys: list[str],
        *,
        limit: int = 30,
    ) -> list[Dict[str, Any]]:
        """取一组节点的一跳活跃边（上下文注入用；按强度降序）。"""
        if not node_keys:
            return []
        db = await self._conn.get_db()
        ids: list[int] = []
        for key in node_keys:
            node = await self._get_node_row(db, await self._normalize_entity_key(key))
            if node and not node["archived"]:
                ids.append(node["id"])
        edges = await self._edges_for_node_ids(db, ids)
        edges.sort(key=lambda e: (-e["strength"], e["id"]))  # id 决胜保证字节稳定
        edges = edges[:limit]
        await self._record_edge_access(db, [e["id"] for e in edges])
        return edges

    async def find_path(
        self,
        from_key: str,
        to_key: str,
        *,
        max_depth: int = 4,
    ) -> list[Dict[str, Any]]:
        """BFS 最短关系路径（无向遍历活跃边），返回构成路径的边列表。"""
        db = await self._conn.get_db()
        src = await self._get_node_row(db, await self._normalize_entity_key(from_key))
        dst = await self._get_node_row(db, await self._normalize_entity_key(to_key))
        if not src or not dst:
            return []
        if src["id"] == dst["id"]:
            return []
        max_depth = max(1, min(6, int(max_depth)))
        # parent[nid] = (上一层节点 id, 连接边 id)
        parent: Dict[int, tuple[int, int]] = {}
        edge_cache: Dict[int, Dict[str, Any]] = {}
        frontier = [src["id"]]
        visited = {src["id"]}
        found = False
        for _ in range(max_depth):
            if found or not frontier:
                break
            hop_edges = await self._edges_for_node_ids(db, frontier)
            next_frontier: list[int] = []
            for edge in hop_edges:
                edge_cache[edge["id"]] = edge
                for a, b in ((edge["subject"], edge["object"]), (edge["object"], edge["subject"])):
                    if a["id"] in visited and b["id"] not in visited:
                        visited.add(b["id"])
                        parent[b["id"]] = (a["id"], edge["id"])
                        if b["id"] == dst["id"]:
                            found = True
                        else:
                            next_frontier.append(b["id"])
            frontier = next_frontier
        if dst["id"] not in parent:
            return []
        path: list[Dict[str, Any]] = []
        cursor_id = dst["id"]
        while cursor_id in parent:
            prev_id, edge_id = parent[cursor_id]
            path.append(edge_cache[edge_id])
            cursor_id = prev_id
        path.reverse()
        return path

    async def merge_nodes(self, source_key: str, target_key: str) -> Dict[str, Any]:
        """合并节点：source 的全部边改挂 target（冲突边合并强度与证据），source 归档。

        典型场景：未入库人物（person:老王）后来有了真实身份（user:qq:123）。
        """
        db = await self._conn.get_db()
        src_key = await self._normalize_entity_key(source_key)
        dst_key = await self._normalize_entity_key(target_key)
        if src_key == dst_key:
            raise ValueError("源节点与目标节点相同，无需合并")
        async with self._conn.tx(db):
            src = await self._get_node_row(db, src_key)
            if not src:
                raise ValueError(f"节点不存在: {src_key}")
            # 目标节点不存在时自动建档（未入库人物合并进尚无图节点的真实实体）
            dst = await self._get_node_row(db, dst_key)
            if dst is None:
                dst = await self._ensure_node(db, dst_key)
            now_ns = time.time_ns()
            cursor = await db.execute(
                "SELECT id, subject_id, object_id, predicate, strength, evidence "
                "FROM graph_edges WHERE subject_id=? OR object_id=?",
                (src["id"], src["id"]),
            )
            moved = merged = dropped = 0
            for row in await cursor.fetchall():
                edge_id = int(row["id"])
                new_subject = dst["id"] if int(row["subject_id"]) == src["id"] else int(row["subject_id"])
                new_object = dst["id"] if int(row["object_id"]) == src["id"] else int(row["object_id"])
                if new_subject == new_object:
                    await db.execute(
                        "UPDATE graph_edges SET archived=1, updated_ns=? WHERE id=?",
                        (now_ns, edge_id),
                    )
                    dropped += 1
                    continue
                try:
                    await db.execute(
                        "UPDATE graph_edges SET subject_id=?, object_id=?, updated_ns=? WHERE id=?",
                        (new_subject, new_object, now_ns, edge_id),
                    )
                    moved += 1
                except sqlite3.IntegrityError:
                    # 目标侧已有同三元组：合并强度与证据后删除重复边
                    await db.execute(
                        "UPDATE graph_edges SET strength=MAX(strength, ?), "
                        "evidence=CASE WHEN ? != '' AND evidence NOT LIKE '%' || ? || '%' "
                        "THEN evidence || '；' || ? ELSE evidence END, updated_ns=? "
                        "WHERE subject_id=? AND predicate=? AND object_id=?",
                        (row["strength"], row["evidence"], row["evidence"],
                         row["evidence"], now_ns, new_subject, row["predicate"], new_object),
                    )
                    await db.execute("DELETE FROM graph_edges WHERE id=?", (edge_id,))
                    merged += 1
            merged_meta = {**src["metadata"], "merged_into": dst_key}
            await db.execute(
                "UPDATE graph_nodes SET archived=1, metadata_json=?, updated_ns=? WHERE id=?",
                (json.dumps(merged_meta, ensure_ascii=False), now_ns, src["id"]),
            )
            if not dst["label"] and src["label"]:
                await db.execute(
                    "UPDATE graph_nodes SET label=? WHERE id=?", (src["label"], dst["id"]),
                )
            neighbors = await self._neighbor_node_ids(db, dst["id"])
            await self._enqueue_projection(db, [dst["id"], *neighbors], [src["id"]])
        return {
            "source": src_key,
            "target": dst_key,
            "edges_moved": moved,
            "edges_merged": merged,
            "edges_dropped": dropped,
        }

    async def list_edges(
        self,
        *,
        predicate: str = "",
        origin: str = "",
        include_archived: bool = False,
        limit: int = 500,
    ) -> list[Dict[str, Any]]:
        """全图边列表（Web 拓扑图数据源）。"""
        db = await self._conn.get_db()
        sql = f"{self._EDGE_SELECT} WHERE 1=1"
        params: list[Any] = []
        if not include_archived:
            sql += " AND e.archived=0 AND s.archived=0 AND o.archived=0"
        if predicate:
            sql += " AND e.predicate=?"
            params.append(predicate)
        if origin:
            sql += " AND e.origin=?"
            params.append(origin)
        sql += " ORDER BY e.updated_ns DESC LIMIT ?"
        params.append(max(1, limit))
        cursor = await db.execute(sql, params)
        return [self._row_to_edge(row) for row in await cursor.fetchall()]

    async def stats(self) -> Dict[str, Any]:
        db = await self._conn.get_db()
        node_row = await (await db.execute(
            "SELECT COUNT(*) AS c FROM graph_nodes WHERE archived=0"
        )).fetchone()
        edge_row = await (await db.execute(
            "SELECT COUNT(*) AS c FROM graph_edges WHERE archived=0"
        )).fetchone()
        nodes = int(node_row["c"]) if node_row else 0
        edges = int(edge_row["c"]) if edge_row else 0
        type_rows = await (await db.execute(
            "SELECT node_type, COUNT(*) AS c FROM graph_nodes WHERE archived=0 "
            "GROUP BY node_type"
        )).fetchall()
        pred_rows = await (await db.execute(
            "SELECT predicate, COUNT(*) AS c FROM graph_edges WHERE archived=0 "
            "GROUP BY predicate ORDER BY c DESC LIMIT 20"
        )).fetchall()
        return {
            "nodes": int(nodes),
            "edges": int(edges),
            "node_types": {str(r["node_type"]): int(r["c"]) for r in type_rows},
            "top_predicates": {str(r["predicate"]): int(r["c"]) for r in pred_rows},
        }

    # ------------------------------------------------------------------
    # 治理：衰减与遗忘（记忆整理器周期调用，全部确定性 SQL、无 LLM）
    # ------------------------------------------------------------------

    async def relax_edge_strength(
        self,
        *,
        stale_days: int = 30,
        rate: float = 0.05,
        baseline: float = 0.5,
        limit: int = 500,
    ) -> int:
        """边强度松弛：长期未活动的活跃边向基线回归（访问护盾）。

        护盾公式与记忆 importance 松弛一致：有效速率 ÷ (1 + ln(access_count))，
        常被检索命中的关系更抗遗忘。不触碰 updated_ns（与记忆松弛同语义，
        重复整理持续向基线收敛，到基线后不再入选）。
        """
        db = await self._conn.get_db()
        cutoff_ns = time.time_ns() - int(stale_days * 86_400 * 1e9)
        cursor = await db.execute(
            "SELECT id, strength, access_count FROM graph_edges "
            "WHERE archived=0 AND strength > ? AND "
            "(CASE WHEN last_accessed_ns > 0 THEN last_accessed_ns ELSE updated_ns END) < ? "
            "ORDER BY updated_ns ASC LIMIT ?",
            (baseline, cutoff_ns, max(1, limit)),
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0
        relaxed = 0
        async with self._conn.tx(db):
            for row in rows:
                access = int(row["access_count"] or 0)
                shield = 1.0 + math.log(access) if access > 1 else 1.0
                strength = float(row["strength"])
                new_strength = baseline + (strength - baseline) * (1.0 - rate / shield)
                await db.execute(
                    "UPDATE graph_edges SET strength=? WHERE id=?",
                    (round(max(baseline, new_strength), 4), int(row["id"])),
                )
                relaxed += 1
        return relaxed

    async def forget_weak_edges(
        self,
        *,
        min_age_days: int = 90,
        strength_threshold: float = 0.25,
        limit: int = 100,
    ) -> int:
        """弱边遗忘：强度低于阈值且长期无活动的边软归档（可恢复，自动触发投影更新）。"""
        db = await self._conn.get_db()
        cutoff_ns = time.time_ns() - int(min_age_days * 86_400 * 1e9)
        cursor = await db.execute(
            "SELECT id FROM graph_edges WHERE archived=0 AND strength < ? AND "
            "(CASE WHEN last_accessed_ns > 0 THEN last_accessed_ns ELSE updated_ns END) < ? "
            "ORDER BY strength ASC, updated_ns ASC LIMIT ?",
            (strength_threshold, cutoff_ns, max(1, limit)),
        )
        edge_ids = [int(r["id"]) for r in await cursor.fetchall()]
        archived = 0
        for edge_id in edge_ids:
            if await self.set_relation_archived(edge_id, True):
                archived += 1
        return archived

    async def archive_orphan_nodes(self, *, min_age_days: int = 60, limit: int = 50) -> int:
        """孤立节点归档：无活跃边且长期未更新的自由型节点软归档。

        user/group 实体节点是会话锚点（scope 注入与画像依赖），永不自动归档。
        """
        db = await self._conn.get_db()
        cutoff_ns = time.time_ns() - int(min_age_days * 86_400 * 1e9)
        cursor = await db.execute(
            "SELECT n.node_key FROM graph_nodes n "
            "WHERE n.archived=0 AND n.updated_ns < ? AND n.node_type NOT IN ('user','group') "
            "AND NOT EXISTS (SELECT 1 FROM graph_edges e WHERE e.archived=0 "
            "AND (e.subject_id=n.id OR e.object_id=n.id)) "
            "ORDER BY n.updated_ns ASC LIMIT ?",
            (cutoff_ns, max(1, limit)),
        )
        keys = [str(r["node_key"]) for r in await cursor.fetchall()]
        archived = 0
        for key in keys:
            if await self.set_node_archived(key, True):
                archived += 1
        return archived

    async def labels_for_keys(self, node_keys: list[str]) -> list[str]:
        """按键批量取节点展示名（去重保序）——cognee node_name 定向检索入参。"""
        node_map = await self.get_nodes_by_keys(node_keys)
        labels: list[str] = []
        for key in node_keys:
            node = node_map.get(key)
            label = str(node.get("label", "")).strip() if node else ""
            if label and label not in labels:
                labels.append(label)
        return labels

    async def curation_facts(
        self,
        *,
        weak_strength: float,
        stale_cutoff_ns: int,
        ambiguity_min_strength: float,
        hub_degree: int,
    ) -> Dict[str, list]:
        """图谱治理事实的确定性查询（治理议程的数据底座）。

        阈值语义与配置归 ``graph/curation.py``（领域层），本方法只做
        数据访问；各项上限固定（弱边/陈旧/歧义/重复 10、枢纽 5）。
        """
        db = await self._conn.get_db()

        async def _edges(where: str, params: list[Any], order: str, limit: int) -> list[Dict[str, Any]]:
            cursor = await db.execute(
                f"{self._EDGE_SELECT} WHERE {where} ORDER BY {order} LIMIT {limit}",
                params,
            )
            return [self._row_to_edge(row) for row in await cursor.fetchall()]

        facts: Dict[str, list] = {
            "weak_edges": await _edges(
                "e.archived=0 AND e.strength < ?", [weak_strength],
                "e.strength ASC", 10,
            ),
            "stale_edges": await _edges(
                "e.archived=0 AND (CASE WHEN e.last_accessed_ns > 0 "
                "THEN e.last_accessed_ns ELSE e.updated_ns END) < ?",
                [stale_cutoff_ns], "e.updated_ns ASC", 10,
            ),
        }

        # 歧义组：同主语同谓词指向多个强对象（抽取噪声或真实多值）
        cursor = await db.execute(
            "SELECT e.subject_id AS sid, s.node_key AS s_key, s.label AS s_label, "
            "e.predicate, COUNT(DISTINCT e.object_id) AS c "
            "FROM graph_edges e JOIN graph_nodes s ON e.subject_id = s.id "
            "WHERE e.archived=0 AND e.strength >= ? "
            "GROUP BY e.subject_id, e.predicate "
            "HAVING c >= 2 ORDER BY c DESC LIMIT 10",
            (ambiguity_min_strength,),
        )
        ambiguous: list[Dict[str, Any]] = []
        for row in await cursor.fetchall():
            edges = await _edges(
                "e.archived=0 AND e.subject_id=? AND e.predicate=? AND e.strength >= ?",
                [int(row["sid"]), str(row["predicate"]), ambiguity_min_strength],
                "e.strength DESC", 10,
            )
            if len(edges) >= 2:
                ambiguous.append({
                    "subject_key": str(row["s_key"]),
                    "subject_label": str(row["s_label"]),
                    "predicate": str(row["predicate"]),
                    "edges": edges,
                })
        facts["ambiguous_groups"] = ambiguous

        # 疑似重复节点：同类型且称呼归一（去空白/小写）后相同
        cursor = await db.execute(
            "SELECT node_type, LOWER(TRIM(label)) AS norm, "
            "COUNT(*) AS c, GROUP_CONCAT(node_key) AS keys, GROUP_CONCAT(label) AS labels "
            "FROM graph_nodes WHERE archived=0 AND TRIM(label) != '' "
            "GROUP BY node_type, norm HAVING c > 1 LIMIT 10"
        )
        facts["duplicate_nodes"] = [
            {
                "node_type": str(r["node_type"]),
                "nodes": str(r["keys"]).split(","),
                "labels": str(r["labels"]).split(","),
            }
            for r in await cursor.fetchall()
        ]

        # 枢纽异常：度数超阈的自由型节点（万物连接点多 为抽取错误）
        cursor = await db.execute(
            "SELECT n.node_key, n.label, n.node_type, COUNT(e.id) AS deg "
            "FROM graph_nodes n JOIN graph_edges e "
            "ON ((e.subject_id = n.id OR e.object_id = n.id) AND e.archived = 0) "
            "WHERE n.archived = 0 AND n.node_type NOT IN ('user', 'group') "
            "GROUP BY n.id HAVING deg > ? ORDER BY deg DESC LIMIT 5",
            (hub_degree,),
        )
        facts["hub_nodes"] = [
            {"node": str(r["node_key"]), "label": str(r["label"]), "degree": int(r["deg"])}
            for r in await cursor.fetchall()
        ]
        return facts

    # ------------------------------------------------------------------
    # cognee 投影
    # ------------------------------------------------------------------

    async def render_node_projection(self, node_id: int) -> Optional[Tuple[str, str]]:
        """渲染节点邻域文档与结构指纹（cognee 投影单元）；节点不存在或已归档返回 None。

        指纹只覆盖结构内容（节点身份 + 各边的谓词/方向/对端节点），
        不含强度与证据文本——关系被重复提及时的强化（strength 漂移）
        与证据刷新不改变指纹，投影因此跳过；仅在邻域结构真实变化
        （增删边/谓词或对端变化）时重投影，文档中的强度/证据随之刷新。
        """
        node = await self.get_node_by_id(node_id)
        if not node or node["archived"]:
            return None
        db = await self._conn.get_db()
        edges = await self._edges_for_node_ids(db, [node_id])
        label = node["label"] or node["node_key"]
        lines = [f"[关系节点] {label}（{node['node_key']}，类型 {node['node_type']}）"]
        structure = [f"{node['node_key']}|{node['node_type']}|{label}"]
        for edge in edges:
            if edge["subject"]["id"] == node_id:
                target = edge["object"]
                t_name = f"{target['label']}（{target['node_key']}）" if target["label"] else target["node_key"]
                arrow = "↔" if edge["symmetric"] else "→"
                line = f"- {edge['predicate']} {arrow} {t_name}（强度 {edge['strength']:.2f}）"
                structure.append(f"{edge['predicate']}|{'s' if edge['symmetric'] else 'o'}|{target['node_key']}")
            else:
                source = edge["subject"]
                s_name = f"{source['label']}（{source['node_key']}）" if source["label"] else source["node_key"]
                arrow = "↔" if edge["symmetric"] else "←"
                line = f"- {edge['predicate']} {arrow} {s_name}（强度 {edge['strength']:.2f}）"
                structure.append(f"{edge['predicate']}|{'s' if edge['symmetric'] else 'i'}|{source['node_key']}")
            if edge["evidence"]:
                line += f"——{edge['evidence']}"
            lines.append(line)
        if len(lines) == 1:
            lines.append("- （暂无已知关系）")
        canonical = "\n".join([structure[0], *sorted(structure[1:])])
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return "\n".join(lines), fingerprint

    async def render_node_document(self, node_id: int) -> Optional[str]:
        """渲染节点邻域文档（cognee 投影单元）；节点不存在或已归档返回 None。"""
        projection = await self.render_node_projection(node_id)
        return projection[0] if projection else None
