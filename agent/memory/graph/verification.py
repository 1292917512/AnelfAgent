"""关系核验契约：来源与核验状态独立于模型评分，所有事实出口共用。

Model Experience: 自动提取保留为待核验线索，显式查询展示状态、证据与修订号。
Token effect: 自动事实区减少未核验关系；管理回执增加少量核验字段，不新增模型调用。
Cache effect: 核验仅影响工具结果、动态召回和派生索引，不改变稳定前缀。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STATUSES = frozenset({"pending", "verified", "disputed", "rejected"})


def status(edge: dict[str, Any]) -> str:
    """没有核验记录的历史关系保持待核验，不从来源或高分推断真伪。"""
    metadata = edge.get("metadata")
    record = metadata.get("verification") if isinstance(metadata, dict) else None
    value = record.get("status") if isinstance(record, dict) else None
    return value if isinstance(value, str) and value in STATUSES else "pending"


def revision(edge: dict[str, Any]) -> str:
    """核验基线包含关系身份、证据和裁决；访问计数与强度衰减不使基线失效。"""
    value = {key: edge.get(key) for key in ("id", "predicate", "symmetric", "evidence", "source_memory_id",
                                           "origin", "metadata", "archived")}
    value.update(subject=edge["subject"]["node_key"], object=edge["object"]["node_key"])
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
