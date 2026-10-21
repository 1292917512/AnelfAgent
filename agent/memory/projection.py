"""权威记忆到派生索引的稳定契约，独立于后端软件版本。

模型看到什么：证据携带原文、精确标签和真实来源，进入既有动态召回通道。
token 影响：来源字段增加少量 token，不新增主对话调用。
缓存影响：仅影响派生文档和动态召回，不改变稳定前缀。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# 只随不兼容的文档/索引契约变化递增，与依赖版本和检索调参无关。
PROJECTION_VERSION = 2


def canonical_tags(tags: Any) -> list[str]:
    """去空白、去重、排序；保留大小写与原义，别名必须由图谱治理确认。"""
    if not isinstance(tags, (list, tuple)):
        return []
    return sorted({tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()})


def projection_dataset(prefix: str, *, graph: bool = False) -> str:
    """存储分区与人物、群标签解耦，多主体文档只存一份。"""
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)
    return f"{prefix}_{'relations' if graph else 'global'}"


def projection_hash(value: Any) -> str:
    """指纹覆盖索引契约，不包含库版本、召回参数或访问热度。"""
    canonical = json.dumps(
        {"projection_version": PROJECTION_VERSION, "value": value},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def memory_is_projectable(payload: dict[str, Any]) -> bool:
    """活跃事实进入索引；计划和合并行继续保留在权威库。"""
    metadata = payload.get("metadata") or {}
    return (
        payload.get("source") != "goal"
        and float(payload.get("importance", 0.5)) > 0
        and not metadata.get("merged_into")
    )


def graph_document(node: dict[str, Any], edges: list[dict[str, Any]],
                   attribution: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """将一致的关系快照转换成稳定文本和指纹；存储层不定义模型文档格式。"""
    node_id = node["id"]
    label = node["label"] or node["node_key"]
    lines = [f"[关系节点] {label}（{node['node_key']}，类型 {node['node_type']}）"]
    for edge in sorted(edges, key=lambda e: e["id"]):
        if edge["subject"]["id"] == node_id:
            target = edge["object"]
            t_name = f"{target['label']}（{target['node_key']}）" if target["label"] else target["node_key"]
            arrow = "↔" if edge["symmetric"] else "→"
            line = f"- {edge['predicate']} {arrow} {t_name}"
        else:
            source = edge["subject"]
            s_name = f"{source['label']}（{source['node_key']}）" if source["label"] else source["node_key"]
            arrow = "↔" if edge["symmetric"] else "←"
            line = f"- {edge['predicate']} {arrow} {s_name}"
        if edge["evidence"]:
            line += f"——{edge['evidence']}"
        lines.append(line)
    if len(lines) == 1:
        lines.append("- （暂无已知关系）")
    document = "\n".join(lines)
    return document, projection_hash({"text": document, "attribution": attribution}), attribution
