"""关系图谱工具 — Agent 的人物/概念关系网络接口。

权威存储为记忆库 graph_nodes/graph_edges（GraphStore），本模块是 AI 侧的
增删改查入口；与记忆（什么事）、画像（谁）互补，专管"谁和谁/什么和什么
什么关系"。节点 key 格式 ``类型:名称``：实体型 ``user:{频道}:{uid}`` /
``group:{频道}:{gid}``（自动别名归一），自由型 person:/topic:/project: 等。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

from core.log import log
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import activate_group, deferred_tool

from ..memory_store import MemoryStore
from .store import NODE_TYPES, format_triple, parse_node_key

_store: Optional[MemoryStore] = None

# 别名解析进程内缓存：别名极少变更，图谱每次读写都解析，直查主库是热路径开销
_ALIAS_CACHE_TTL = 300.0
_ALIAS_CACHE_MAX = 1024
_alias_cache: Dict[Tuple[str, str], Tuple[float, Optional[tuple[str, str]]]] = {}


def invalidate_alias_cache() -> None:
    """清空别名解析缓存（别名登记/移除后调用）。"""
    _alias_cache.clear()


def register_graph_tools(store: MemoryStore) -> None:
    """注入运行时依赖、接线别名解析器并批量注册关系图谱工具。"""
    global _store
    _store = store
    store.graph.set_alias_resolver(_resolve_alias)
    count = activate_group("graph", "关系图谱 - 人物/概念关系网络的结构化存储与查询")
    log(f"🕸 关系图谱工具已注册 ({count} 个)", tag="思维")


async def _resolve_alias(scope_type: str, scope_id: str) -> Optional[tuple[str, str]]:
    """别名解析桥：惰性访问运行时 sqlite（心跳/测试环境无运行时则放弃归一）。

    进程内 TTL 缓存（别名极少变更）；登记/移除别名时经 invalidate_alias_cache 失效。
    """
    key = (scope_type, scope_id)
    cached = _alias_cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < _ALIAS_CACHE_TTL:
        return cached[1]
    try:
        from services._runtime import require_runtime
        result = await require_runtime().data_center.sqlite.resolve_alias(scope_type, scope_id)
    except Exception:
        return None
    if len(_alias_cache) >= _ALIAS_CACHE_MAX:
        _alias_cache.clear()
    _alias_cache[key] = (time.monotonic(), result)
    return result


def _graph() -> Any:
    """取 GraphStore；未就绪返回 None。"""
    return _store.graph if _store else None


def _not_ready() -> str:
    return tool_error(
        "记忆系统未初始化",
        cause=ErrorCause.STATE, retryable=False,
        hint="关系图谱组件未初始化，请检查服务启动状态",
    )


def _normalize_node_key(node_key: str) -> str:
    """归一节点 key：user/group 的裸 id 按当前会话频道补 adapter 前缀。"""
    key = (node_key or "").strip()
    try:
        ntype, name = parse_node_key(key)
    except ValueError:
        return key
    if ntype in ("user", "group") and ":" not in name.split("#", 1)[0]:
        from ..tools import _normalize_scope_id
        return f"{ntype}:{_normalize_scope_id(name)}"
    return key


def _edge_json(edge: Dict[str, Any]) -> Dict[str, Any]:
    """边的 AI 友好序列化：结构化字段 + 紧凑三元组文本。"""
    return {
        "id": edge["id"],
        "triple": format_triple(edge),
        "subject": edge["subject"],
        "predicate": edge["predicate"],
        "object": edge["object"],
        "symmetric": edge["symmetric"],
        "strength": edge["strength"],
        "evidence": edge["evidence"],
        "origin": edge["origin"],
        "archived": edge["archived"],
    }


@deferred_tool(
    group="graph", tags=["always"], source="mind.graph",
    description=(
        "记录一条关系：主语 ─[关系类型]→ 宾语。节点不存在会自动建档。"
        "用于沉淀人物关系（A 是 B 的姐姐）、偏好（A 喜欢 火锅）、归属（A 属于 某组织）等结构化事实。"
        "同一 (主语, 关系, 宾语) 重复写入会更新强度与证据，不会产生重复边。"
    ),
)
async def graph_add_relation(
    subject: str,
    predicate: str,
    object: str,
    subject_label: str = "",
    object_label: str = "",
    symmetric: bool = False,
    strength: float = 0.7,
    evidence: str = "",
    source_memory_id: int = 0,
) -> str:
    """新增/更新一条关系边。

    Args:
        subject: 主语节点 key（``user:123``、``person:老王``、``topic:火锅``；user/group 裸 id 自动补当前频道前缀并归一别名）
        predicate: 关系类型。人物关系推荐：家人/朋友/同事/同学/恋人/上下级；通用：喜欢/讨厌/属于/参与/擅长/位于/使用
        object: 宾语节点 key（同 subject 格式）
        subject_label: 主语展示名（称呼，可选）
        object_label: 宾语展示名（可选）
        symmetric: 是否对称关系（朋友/同事等为 true；喜欢/属于等为 false）
        strength: 关系强度 0-1（反映置信度，默认 0.7）
        evidence: 证据摘要（从哪段对话/哪条记忆得出，必填更佳，便于日后核实）
        source_memory_id: 溯源的记忆 id（可选，0 表示无）
    """
    graph = _graph()
    if graph is None:
        return _not_ready()
    try:
        edge = await graph.add_relation(
            _normalize_node_key(subject), predicate, _normalize_node_key(object),
            subject_label=subject_label.strip(), object_label=object_label.strip(),
            symmetric=symmetric, strength=strength, evidence=evidence,
            source_memory_id=source_memory_id or None, origin="manual_tool",
        )
        return json.dumps({"ok": True, "edge": _edge_json(edge)}, ensure_ascii=False)
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action="新增关系")


@deferred_tool(
    group="graph", tags=["core", "heartbeat"], source="mind.graph",
    description="更新一条已有关系边的谓词/强度/证据/对称性（按边 id，空参数表示不变）。",
)
async def graph_update_relation(
    edge_id: int,
    predicate: str = "",
    strength: float = -1.0,
    evidence: str = "",
    symmetric: str = "",
) -> str:
    """更新关系边。

    Args:
        edge_id: 边 id（graph_query 返回的 id）
        predicate: 新关系类型（空串不变）
        strength: 新强度 0-1（负数不变）
        evidence: 新证据（空串不变）
        symmetric: "true"/"false"（空串不变）
    """
    graph = _graph()
    if graph is None:
        return _not_ready()
    try:
        sym: Optional[bool] = None
        if symmetric.strip().lower() in ("true", "false"):
            sym = symmetric.strip().lower() == "true"
        edge = await graph.update_relation(
            int(edge_id),
            predicate=predicate or None,
            strength=strength if strength >= 0 else None,
            evidence=evidence or None,
            symmetric=sym,
        )
        if edge is None:
            return tool_error(f"关系边不存在: {edge_id}", cause=ErrorCause.NOT_FOUND, retryable=False)
        return json.dumps({"ok": True, "edge": _edge_json(edge)}, ensure_ascii=False)
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action="更新关系")


@deferred_tool(
    group="graph", tags=["core", "heartbeat"], source="mind.graph",
    description="移除一条关系边（软删除，数据保留可审计）。确认关系已失效或错误时使用。",
)
async def graph_remove_relation(edge_id: int) -> str:
    """移除关系边（软删除）。

    Args:
        edge_id: 边 id（graph_query 返回的 id）
    """
    graph = _graph()
    if graph is None:
        return _not_ready()
    try:
        ok = await graph.set_relation_archived(int(edge_id), True)
        if not ok:
            return tool_error(f"关系边不存在: {edge_id}", cause=ErrorCause.NOT_FOUND, retryable=False)
        return json.dumps({"ok": True, "removed": int(edge_id)}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="移除关系")


@deferred_tool(
    group="graph", tags=["core", "heartbeat"], source="mind.graph",
    description=(
        "创建或更新图谱节点（补充展示名/备注）。实体节点（user:/group:）通常随关系自动建档，"
        "本工具主要用于自由节点（person:/topic:/project: 等）的资料维护。"
    ),
)
async def graph_upsert_node(node_key: str, label: str = "", metadata: str = "") -> str:
    """创建或更新节点。

    Args:
        node_key: 节点 key（``类型:名称``，类型推荐 user/group/person/topic/project/org/thing/concept）
        label: 展示名（空串不变）
        metadata: 备注 JSON 对象字符串（与既有备注合并，空串不变）
    """
    graph = _graph()
    if graph is None:
        return _not_ready()
    try:
        meta: Optional[Dict[str, Any]] = None
        if metadata.strip():
            try:
                parsed = json.loads(metadata)
            except json.JSONDecodeError:
                return tool_error("metadata 不是合法 JSON", cause=ErrorCause.PARAM, retryable=False)
            if not isinstance(parsed, dict):
                return tool_error("metadata 必须是 JSON 对象", cause=ErrorCause.PARAM, retryable=False)
            meta = parsed
        node = await graph.upsert_node(
            _normalize_node_key(node_key), label=label.strip(), metadata=meta,
        )
        return json.dumps({"ok": True, "node": node}, ensure_ascii=False)
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action="更新节点")


@deferred_tool(
    group="graph", tags=["core", "heartbeat"], source="mind.graph",
    description="归档一个节点及其全部关系边（软删除）。仅在节点彻底错误/废弃时使用。",
)
async def graph_remove_node(node_key: str) -> str:
    """归档节点（级联归档其所有边）。

    Args:
        node_key: 节点 key
    """
    graph = _graph()
    if graph is None:
        return _not_ready()
    try:
        ok = await graph.set_node_archived(_normalize_node_key(node_key), True)
        if not ok:
            return tool_error(f"节点不存在: {node_key}", cause=ErrorCause.NOT_FOUND, retryable=False)
        return json.dumps({"ok": True, "removed": node_key}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="归档节点")


@deferred_tool(
    group="graph", tags=["core", "heartbeat"], source="mind.graph",
    description=(
        "合并两个节点：source 的所有关系改挂到 target，source 归档。"
        "典型场景：未入库人物（person:老王）确认真实身份后合并进 user:qq:123。"
    ),
)
async def graph_merge_nodes(source_key: str, target_key: str) -> str:
    """合并节点。

    Args:
        source_key: 被合并的节点 key（合并后归档）
        target_key: 保留的节点 key（继承全部关系）
    """
    graph = _graph()
    if graph is None:
        return _not_ready()
    try:
        report = await graph.merge_nodes(
            _normalize_node_key(source_key), _normalize_node_key(target_key),
        )
        return json.dumps({"ok": True, **report}, ensure_ascii=False)
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action="合并节点")


@deferred_tool(
    group="graph", tags=["always"], source="mind.graph",
    description=(
        "查询某节点（人物/概念）的关系网络：返回该节点及其邻域的全部关系三元组。"
        "想了解某人和谁有关系、某事物的关联时使用；depth=2 可看二度人脉。"
    ),
)
async def graph_query(
    node: str,
    depth: int = 1,
    predicate: str = "",
    limit: int = 30,
) -> str:
    """查询节点的关系邻域。

    Args:
        node: 节点 key（``user:123`` / ``person:老王`` 等，实体裸 id 自动补频道前缀）
        depth: 扩展层数 1-2（1=直接关系，2=二度关系）
        predicate: 只看某种关系类型（空串不限）
        limit: 返回边数上限（默认 30）
    """
    graph = _graph()
    if graph is None:
        return _not_ready()
    try:
        result = await graph.query_relations(
            _normalize_node_key(node), depth=depth, predicate=predicate.strip(),
            limit=max(1, min(100, int(limit))),
        )
        if not result["found"]:
            return json.dumps({
                "found": False,
                "message": f"图谱中没有节点 {node}（可能是从未记录过该实体的关系）",
            }, ensure_ascii=False)
        return json.dumps({
            "found": True,
            "node": result["node"],
            "edge_count": len(result["edges"]),
            "edges": [_edge_json(e) for e in result["edges"]],
        }, ensure_ascii=False)
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action="查询关系")


@deferred_tool(
    group="graph", tags=["core", "heartbeat"], source="mind.graph",
    description="查找两个节点之间的最短关系路径（如『我和苗苗是怎么认识的链条』）。无路径时返回空。",
)
async def graph_path(from_node: str, to_node: str, max_depth: int = 4) -> str:
    """查找关系路径（BFS 最短，无向）。

    Args:
        from_node: 起点节点 key
        to_node: 终点节点 key
        max_depth: 最大搜索深度 1-6（默认 4）
    """
    graph = _graph()
    if graph is None:
        return _not_ready()
    try:
        path = await graph.find_path(
            _normalize_node_key(from_node), _normalize_node_key(to_node),
            max_depth=max_depth,
        )
        return json.dumps({
            "found": bool(path),
            "hops": len(path),
            "triples": [format_triple(e) for e in path],
        }, ensure_ascii=False)
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action="查找关系路径")


@deferred_tool(
    group="graph", tags=["core", "heartbeat"], source="mind.graph",
    description="浏览/搜索图谱节点：按类型过滤或按名称模糊搜索，了解图谱里都有哪些实体与概念。",
)
async def graph_list_nodes(
    node_type: str = "",
    query: str = "",
    limit: int = 50,
) -> str:
    """列出图谱节点。

    Args:
        node_type: 类型过滤（user/group/person/topic/project/org/thing/concept，空串不限）
        query: 名称模糊搜索（匹配 key 与展示名）
        limit: 上限（默认 50）
    """
    graph = _graph()
    if graph is None:
        return _not_ready()
    try:
        nodes = await graph.list_nodes(
            node_type=node_type.strip(), query=query.strip(),
            limit=max(1, min(200, int(limit))),
        )
        stats = await graph.stats()
        return json.dumps({
            "total_nodes": stats["nodes"],
            "total_edges": stats["edges"],
            "node_types": stats["node_types"],
            "available_types": list(NODE_TYPES),
            "nodes": nodes,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="列出节点")
