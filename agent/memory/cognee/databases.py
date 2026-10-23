"""Cognee 数据库管理适配：声明组件并复用占用快照，不打开引擎。

三个组件同属一个存储卷，维护入口仍为 Cognee 协调器和卷管理。
元数据库只提供短连接只读浏览，图/向量由 Cognee 管理其内部布局。
"""

from __future__ import annotations

from functools import partial
from typing import Literal

from core.database import DatabaseCapability, DatabaseComponent, DatabaseObservation

from .storage import cognee_storage_stats


async def _observe(key: Literal["metadata_bytes", "graph_bytes", "lance_bytes"], root: str) -> DatabaseObservation:
    stats = await cognee_storage_stats.get(root)
    measured_at = stats.get("computed_at")
    return DatabaseObservation(
        size_bytes=stats[key] if measured_at else None,
        measured_at=measured_at,
    )


DATABASE_COMPONENTS = (
    DatabaseComponent(
        database_id="cognee", name="Cognee SQLite", description="数据集、文档与管线元数据",
        engine="sqlite", relative_path="system/databases/cognee_db",
        capabilities=frozenset({DatabaseCapability.BROWSE, DatabaseCapability.QUERY}),
        managed_by="Cognee", management_url="/memory?tab=cognee",
        observe=partial(_observe, "metadata_bytes"),
    ),
    DatabaseComponent(
        database_id="cognee_graph", name="Ladybug", description="Cognee 图节点与关系索引（包含保留轮次）",
        engine="ladybug", relative_path="system/databases",
        managed_by="Cognee", management_url="/memory?tab=cognee",
        observe=partial(_observe, "graph_bytes"),
    ),
    DatabaseComponent(
        database_id="cognee_vector", name="LanceDB", description="Cognee 向量索引及版本文件（包含保留轮次）",
        engine="lancedb", relative_path="system/databases",
        managed_by="Cognee", management_url="/memory?tab=cognee",
        observe=partial(_observe, "lance_bytes"),
    ),
)
