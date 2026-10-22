"""Cognee NodeSet 身份适配；原始标签留在权威库及 attribution，不参与别名治理。"""

from __future__ import annotations

from uuid import NAMESPACE_OID, UUID, uuid5


def node_set_id(name: str) -> UUID:
    """映射 SDK 的持久身份规则；sdk_contract 在打开数据库前校验该规则。"""
    return uuid5(NAMESPACE_OID, f"NodeSet:{name}".lower().replace(" ", "_").replace("'", ""))


def projected_node_tags(tags: list[str]) -> list[str]:
    """按 SDK 摄入顺序保留同一 NodeSet 的首个拼写，不修改完整来源标签。"""
    seen: set[UUID] = set()
    result: list[str] = []
    for tag in tags:
        identity = node_set_id(tag)
        if identity not in seen:
            seen.add(identity)
            result.append(tag)
    return result
