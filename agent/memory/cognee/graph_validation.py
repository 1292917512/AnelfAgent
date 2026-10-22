"""Ladybug 实体覆盖验收；将 Cognee 图存储格式依赖限制在 SDK 边界。

文档 ID 由 Cognee classify_documents 继承自 Data.id；DocumentChunk 经
is_part_of 关联文档。检查真实节点和边，不能用 SQL pipeline_status 替代。
"""

from __future__ import annotations

from uuid import UUID


async def verify_graph_documents(dataset_id: UUID, data_ids: list[str]) -> None:
    """每页核验图谱中的文档及至少一个关联分块，任一缺失即阻止验收。"""
    from cognee.context_global_variables import set_database_global_context_variables
    from cognee.infrastructure.databases.graph import get_graph_engine

    async with set_database_global_context_variables(dataset_id):
        graph = await get_graph_engine()
        for offset in range(0, len(data_ids), 200):
            expected = set(data_ids[offset:offset + 200])
            rows = await graph.query(
                "MATCH (c:Node)-[r:EDGE]->(d:Node) "
                "WHERE d.id IN $ids AND c.type = 'DocumentChunk' "
                "AND r.relationship_name = 'is_part_of' RETURN DISTINCT d.id",
                {"ids": sorted(expected)},
            )
            missing = expected - {str(row[0]) for row in rows}
            if missing:
                raise RuntimeError(
                    f"实际图谱缺少 {len(missing)} 个文档或分块关联（数据集 {dataset_id}）；"
                    "请重新开始全量构建，验收前不能清理旧数据"
                )
