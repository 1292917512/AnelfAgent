"""召回结果的注入行格式化权威：归属标注 / 时间尾注 / 记忆行组装。

被动召回（memory_retriever）与异步深探（probe）共用同一套行格式——
同一种事实一种长相：``💡 归属标注 正文（时间 记，私事）``。归属标注
（称呼[uid:xxx]）与会话消息 [uid:] 标签同构，模型可直接对照当前对话
对象确认归属。

叶子模块：只依赖标签前缀常量与图谱查询面（注入侧无 I/O 之外的逻辑）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .store.tag_intel import ENTITY_PREFIXES

if TYPE_CHECKING:
    from .graph.store import GraphStore


async def humanize_entity_tags(graph: Optional["GraphStore"], tags: List[str]) -> List[str]:
    """将记忆标签转为 AI 可读的归属标注：实体标签带明确身份 ID，主题标签去前缀。

    实体标签渲染为「称呼[uid:xxx]」（图谱有称呼时）或「[uid:xxx]」——
    ID 与会话消息的 [uid:xxx] 标签同构，AI 可直接对照当前对话对象确认归属，
    避免仅凭称呼把别人的记忆安到当前对象头上（同名/称呼变更场景）。
    type:/merged/channel:/date: 等内部机制标签不展示。
    """
    display: List[str] = []
    entity_tags: List[str] = []
    for tag in tags:
        if tag.startswith(ENTITY_PREFIXES):
            entity_tags.append(tag)
        elif tag.startswith(("topic:", "goal:")):
            value = tag.split(":", 1)[1].strip()
            if value and value not in display:
                display.append(value)
        # 内部标签（type/merged/channel/date 等）对 AI 无信息量，不注入
    # 批量取节点（单条 IN 查询），替代逐标签串行往返
    node_map: Dict[str, Any] = {}
    if entity_tags and graph is not None:
        try:
            node_map = await graph.get_nodes_by_keys(entity_tags)
        except Exception:
            node_map = {}
    # 归属主体排在主题之前（行首先看"是谁的事"，再看话题）
    labels: List[str] = []
    for tag in entity_tags:
        kind, _, raw = tag.partition(":")
        # 剥离 adapter 段：user:qq:123 → 123，与消息 [uid:xxx] 标签对齐
        uid = raw.rsplit(":", 1)[-1] if raw else ""
        id_key = "uid" if kind == "user" else "group_id"
        node = node_map.get(tag)
        name = str(node.get("label", "")).strip() if node else ""
        label = f"{name}[{id_key}:{uid}]" if name else f"[{id_key}:{uid}]"
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
) -> str:
    """组装单条记忆注入行：``{marker} {归属标注}{正文}（时间 记，私事）``。

    注入纪律：只保留对 AI 有信息量的字段——归属（谁的/什么主题）、正文、
    记录时间；score/内部类型/数据集等调试信息不进上下文。归属标注自带
    方括号，不再外层包裹（避免括号嵌套）。
    """
    humanized = await humanize_entity_tags(graph, list(tags or []))
    head = f"{'·'.join(humanized)} " if humanized else ""
    ts = timestamp or ((provenance or {}).get("timestamp", 0) or 0)
    tail_parts: List[str] = []
    activity = str((provenance or {}).get("activity_date", "") or "")
    if activity:
        tail_parts.append(f"发生于 {activity}")
    else:
        time_str = format_memory_time(ts)
        if time_str:
            tail_parts.append(f"{time_str} 记")
    if sensitivity in ("private", "secret"):
        tail_parts.append("私事")
    tail = f"（{'，'.join(tail_parts)}）" if tail_parts else ""
    return f"{marker} {head}{snippet}{tail}"
