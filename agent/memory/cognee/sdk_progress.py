"""只读 SDK 元数据库中的本批完成标记，不读取正文、不打开图/向量库。"""

from __future__ import annotations

from datetime import timezone
from uuid import UUID

from .types import CogneeDocumentProgress


async def document_progress(dataset_id: str, documents: dict[str, bool]) -> CogneeDocumentProgress:
    """按文档主键分段查询；布尔值表示分片，缺失行保留在分母且不算完成。"""
    from cognee.infrastructure.databases.relational import get_relational_engine
    from cognee.modules.data.models import Data
    from cognee.modules.pipelines.models import is_data_item_completed
    from sqlalchemy import select

    selected = {UUID(key): fragment for key, fragment in documents.items()}
    result = CogneeDocumentProgress(total=len(selected), fragments=sum(selected.values()),
                                   completed=0, fragments_completed=0)
    identity = UUID(dataset_id)
    ids = list(selected)
    completed = fragments_completed = 0
    async with get_relational_engine().get_async_session() as session:
        for offset in range(0, len(ids), 400):
            rows = await session.execute(select(Data.id, Data.pipeline_status, Data.updated_at).where(
                Data.dataset_id == identity, Data.id.in_(ids[offset:offset + 400])))
            for data_id, status, updated_at in rows:
                state = (status or {}).get("cognify_pipeline") or {}
                if not is_data_item_completed(state.get(str(identity))):
                    continue
                completed += 1
                fragments_completed += int(selected[data_id])
                if updated_at is not None:
                    # SQLite 返回 naive datetime；Cognee 持久时间约定为 UTC。
                    timestamp = (updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)).timestamp()
                    result.last_completed_at = max(result.last_completed_at or 0, timestamp)
    result.completed, result.fragments_completed = completed, fragments_completed
    return result
