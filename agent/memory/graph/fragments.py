"""大节点按稳定关系 ID 区间拆分，避免一条关系变化重抽整个邻域。

模型看到什么：每篇保留节点上下文和完整关系证据，归属由结构化元数据携带。
token 影响：首次拆分重复少量节点标题；后续只抽取变化区间，最多扩散到 32 条关系。
缓存影响：只改变派生索引文档，不触碰主对话稳定前缀。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

# 文本长度限制用于约束投影单元，不冒充特定模型的精确 token 限额。
MAX_DOCUMENT_CHARS = 6000
EDGES_PER_RANGE = 32


def edge_ranges(edges: list[dict[str, Any]]) -> list[tuple[int, list[dict[str, Any]]]]:
    """按不可复用的边 ID 分组；插入、删除不会移动其他区间的边。"""
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for edge in sorted(edges, key=lambda item: item["id"]):
        groups[int(edge["id"]) // EDGES_PER_RANGE].append(edge)
    return sorted(groups.items()) or [(0, [])]


def split_document(text: str) -> list[str]:
    """优先按完整关系行切分；超长单条证据无损连续切片，绝不截断丢弃。"""
    header, separator, body = text.partition("\n")
    prefix = header + separator
    capacity = MAX_DOCUMENT_CHARS - len(prefix)
    if capacity <= 0:
        raise ValueError("关系节点标题超过投影文档容量，请缩短节点名称")
    if not body:
        return [text]
    result: list[str] = []
    while body:
        end = min(capacity, len(body))
        if end < len(body):
            boundary = body.rfind("\n", 0, end)
            if boundary >= 0:
                end = boundary + 1
        result.append(prefix + body[:end])
        body = body[end:]
    return result
