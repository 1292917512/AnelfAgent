"""将权威记录转换为稳定的后端文档，不负责网络、队列或版本切换。

模型看到什么：抽取只接收事实原文；标签、时间、来源通过结构化元数据回链。
token 影响：索引时不把管理字段重复送入模型，召回沿用动态区预算。
缓存影响：不写入对话稳定前缀。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..projection import canonical_tags
from ..provenance import recall_provenance
from ..store._shared import projection_content_hash


@dataclass(frozen=True)
class ProjectionDocument:
    """同一权威快照生成的内容、指纹和出处，必须作为一个整体提交。"""

    text: str
    fingerprint: str
    attribution: dict[str, Any]
    part_key: str = ""


def stored_node_tags(value: Any) -> list[str]:
    """Cognee Data.node_set 在 SDK 中为 JSON 字符串，远端 DTO 可返回列表。"""
    if isinstance(value, str):
        value = json.loads(value)
    if value is not None and (not isinstance(value, list) or not all(isinstance(tag, str) for tag in value)):
        raise ValueError("后端 NodeSet 格式无效")
    return canonical_tags(value or [])


def memory_document(payload: dict[str, Any]) -> ProjectionDocument:
    tags = canonical_tags(payload.get("tags", []))
    metadata = payload.get("metadata", {})
    text = str(payload.get("content", ""))
    return ProjectionDocument(text, projection_content_hash(payload), {
        "tags": tags, "timestamp": payload.get("timestamp", 0),
        "memory_type": payload.get("type", "semantic"),
        "sensitivity": metadata.get("sensitivity", "normal"),
        "provenance": {**recall_provenance(metadata, source=str(payload.get("source", ""))),
                       "anelf_memory_id": str(payload.get("memory_id", ""))},
    })
