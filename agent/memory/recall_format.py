"""召回结果的注入行格式化权威：归属标注 / 时间尾注 / 记忆行组装。

被动召回（memory_retriever）与异步深探（probe）共用同一套行格式——
同一种事实一种长相：``💡 mem:ID 归属标注（出处、时间、私密度）正文``。归属标注
（[channel:xxx][name:称呼][uid:xxx]）与会话消息标签同构，模型可直接对照当前对话
对象确认归属。temporal_scope 超期的 state/episode 追加"过去时"尾注，
让模型以"曾经"而非"现在"的口径转述。

叶子模块：只依赖标签前缀常量、图谱查询面与 store 纯函数（注入侧无
I/O 之外的逻辑）。

Model Experience：沿用 channel/uid/group_id/name/scope 标签区分涉及对象与材料会话。
每条增加约 20～60 tokens 的出处与索引；身份批量查询，无新增模型调用。
只改变召回与工具结果，不改变上下文分层；同组事实去重和排序保持字节稳定。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.tags import tag_label

from .memory_types import MemorySearchResult, excerpt
from .provenance import origins_from, visible_provenance
from .store._shared import temporal_is_past
from .store.tag_intel import ENTITY_PREFIXES

if TYPE_CHECKING:
    from .graph.store import GraphStore


async def load_entity_nodes(
    graph: GraphStore | None, tags: Iterable[str],
) -> dict[str, Any]:
    """一次查询整批记忆涉及的身份，查询失败时保留裸频道与身份标记。"""
    keys = sorted({tag for tag in tags if tag.startswith(ENTITY_PREFIXES)})
    if graph is not None and keys:
        try:
            return await graph.get_nodes_by_keys(keys)
        except Exception:
            return {}
    return {}


async def humanize_entity_tags(
    graph: Optional["GraphStore"], tags: List[str], *, nodes: Mapping[str, Any] | None = None,
) -> List[str]:
    """将记忆标签转为 AI 可读的归属标注：实体标签带明确身份 ID，主题标签去前缀。

    实体标签保留 channel 与 uid/group_id，图谱有称呼时增加 name 标签。
    AI 可直接对照当前对话对象确认归属，
    避免仅凭称呼把别人的记忆安到当前对象头上（同名/称呼变更场景）。
    type: 等内部机制标签不展示。
    """
    display: List[str] = []
    entity_tags: List[str] = []
    for tag in sorted(set(tags)):
        if tag.startswith(ENTITY_PREFIXES):
            entity_tags.append(tag)
        elif tag.startswith(("topic:", "goal:")):
            value = tag.split(":", 1)[1].strip()
            if value and value not in display:
                display.append(value)
        # 内部标签（type: 等）对 AI 无信息量，不注入
    node_map = await load_entity_nodes(graph, entity_tags) if nodes is None else nodes
    # 归属主体排在主题之前（行首先看"是谁的事"，再看话题）
    labels: List[str] = []
    for tag in entity_tags:
        kind, _, raw = tag.partition(":")
        adapter, separator, uid = raw.partition(":")
        if not separator:
            uid, adapter = raw, ""
        id_key = "uid" if kind == "user" else "group_id"
        node = node_map.get(tag)
        name = str(node.get("label", "")).strip() if node else ""
        label = (tag_label("channel", adapter) if adapter else "")
        label += tag_label("name", name) if name else ""
        label += tag_label(id_key, uid)
        if label not in labels:
            labels.append(label)
    return labels + display


def format_memory_time(ts: float) -> str:
    """记忆时间的人类可读格式（年内省略年份；秒级粒度禁止入注入块）。"""
    if not ts:
        return ""
    import time as _time
    lt = _time.localtime(ts)
    now = _time.localtime()
    if lt.tm_year == now.tm_year:
        return _time.strftime("%m-%d", lt)
    return _time.strftime("%Y-%m-%d", lt)


async def format_memory_line(
    graph: Optional["GraphStore"],
    *,
    snippet: str,
    tags: Optional[List[str]] = None,
    provenance: Optional[Dict[str, Any]] = None,
    timestamp: float = 0.0,
    sensitivity: str = "normal",
    marker: str = "💡",
    nodes: Mapping[str, Any] | None = None,
    reference: str = "",
    source: str = "memory",
) -> str:
    """归属、出处和隐私标注置于正文前，预算截断不先丢掉解释事实的边界。

    注入纪律：只保留对 AI 有信息量的字段——归属（谁的/什么主题）、正文、
    记录时间；score/内部类型/数据集等调试信息不进上下文。归属标注自带
    方括号，不再外层包裹（避免括号嵌套）。
    """
    humanized = await humanize_entity_tags(graph, list(tags or []), nodes=nodes)
    head = f"{'·'.join(humanized)} " if humanized else ""
    if reference:
        head = f"{reference} " + head
    ts = timestamp or ((provenance or {}).get("timestamp", 0) or 0)
    tail_parts: List[str] = []
    activity = str((provenance or {}).get("activity_date", "") or "")
    if activity:
        tail_parts.append(f"发生于 {activity}")
    else:
        time_str = format_memory_time(ts)
        if time_str:
            tail_parts.append(f"{time_str} 记")
    # 时间语义：超期 state/episode 标注过去时，状态类信息防"现在时"误述
    prov = provenance or {}
    if source == "cognee_graph":
        tail_parts.append("图谱综合·非原话")
    origins = origins_from(prov)
    if origins:
        origin = origins[-1]
        kind = "对话提炼·材料" if origin.kind == "conversation_summary" else "AI记录于"
        attribution = kind + tag_label("scope", origin.scope)
        if origin.started_at:
            attribution += " " + format_memory_time(origin.started_at)
            if origin.ended_at and format_memory_time(origin.ended_at) != format_memory_time(origin.started_at):
                attribution += "至" + format_memory_time(origin.ended_at)
        if len(origins) > 1:
            attribution += f" 等{len(origins)}份来源"
        tail_parts.append(attribution)
    elif prov.get("recorded_by") == "auto_capture":
        tail_parts.append("对话提炼·材料出处未记录")
    else:
        tail_parts.append("出处未记录")
    if prov.get("temporal_scope") and temporal_is_past(dict(prov), ts):
        tail_parts.append("过去时·可能已变")
    if sensitivity in ("private", "secret"):
        tail_parts.append("私事")
    tail = f"（{'，'.join(tail_parts)}）" if tail_parts else ""
    return f"{marker} {head}{tail} {snippet}"


def format_recall_result(result: MemorySearchResult) -> Dict[str, Any]:
    """手动召回与关联结果共用的紧凑出处契约。"""
    return {
        "id": result.memory_reference() or result.id, "source": result.source,
        "content": excerpt(result.snippet, 300), "type": result.memory_type or "",
        "tags": result.tags, "score": round(result.score, 3),
        "provenance": ({"attribution": "file"} if result.source == "file"
                       else visible_provenance(result.provenance)),
        **({"time": format_memory_time(result.timestamp)} if result.timestamp else {}),
        **({"sensitivity": result.sensitivity} if result.sensitivity != "normal" else {}),
        **({"path": result.path, "start_line": result.start_line, "end_line": result.end_line}
           if result.source == "file" else {}),
        **({"dataset": result.dataset_name} if result.dataset_name else {}),
    }


async def format_recall_line(graph: Optional["GraphStore"], result: MemorySearchResult, *,
                             max_chars: int = 500, marker: str = "💡",
                             nodes: Mapping[str, Any] | None = None) -> str:
    """自动召回和后台补充共用的事实行，文件保留实际片段位置。"""
    snippet = excerpt(result.snippet, max_chars)
    if result.source == "file":
        location = f"{result.path}:{result.start_line}-{result.end_line}" if result.path else "文件位置未记录"
        return f"{marker} [{location}] {snippet}"
    return await format_memory_line(
        graph, snippet=snippet, tags=result.tags, provenance=result.provenance,
        timestamp=result.timestamp, sensitivity=result.sensitivity, marker=marker, nodes=nodes,
        reference=result.memory_reference(), source=result.source,
    )
