"""从 Cognee 结构化证据恢复关系事实，避免把检索提示模板当作记忆。"""

from __future__ import annotations

from typing import Any


def graph_evidence_text(evidence: Any) -> str:
    """只渲染带实体名称的真实边；无可读证据时返回空，不猜测模板边界。"""
    if not isinstance(evidence, list):
        return ""
    entries = [item for item in evidence if isinstance(item, dict)]
    names = {str(item.get("artifact_id")): str(item["label"]) for item in entries
             if item.get("kind") == "graph_node" and item.get("label")}
    lines: list[str] = []
    for item in entries:
        if item.get("kind") != "graph_edge":
            continue
        source = names.get(str(item.get("source_node_id")))
        target = names.get(str(item.get("target_node_id")))
        relation = item.get("relationship_name")
        # NodeSet 的结构连接只用于检索定向，不是业务关系事实。
        if source and target and relation and relation != "belongs_to_set":
            lines.append(f"{source} → {relation} → {target}")
    return "\n".join(dict.fromkeys(lines))
