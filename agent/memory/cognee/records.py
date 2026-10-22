"""进程边界的项目数据契约；业务代码不接收 SDK ORM 或枚举对象。"""

from __future__ import annotations

from typing import Any

from typing_extensions import Required, TypedDict


class DatasetRecord(TypedDict):
    id: str
    name: str


class DocumentRecord(TypedDict, total=False):
    id: Required[str]
    label: str | None
    name: str | None
    legacy_id: str | None
    mime_type: str | None
    extension: str | None
    raw_data_location: str | None
    original_data_location: str | None
    content_hash: str | None
    raw_content_hash: str | None
    external_metadata: dict[str, Any] | None
    system_metadata: dict[str, Any] | None
    node_set: str | list[str] | None
    pipeline_status: dict[str, Any] | None


class IngestionResult(TypedDict, total=False):
    dataset_id: Required[str]
    status: str


def document_processed(item: DocumentRecord, dataset_id: str) -> bool:
    status = (item.get("pipeline_status") or {}).get("cognify_pipeline", {}).get(dataset_id)
    if isinstance(status, dict):
        status = status.get("status")
    return status == "DATA_ITEM_PROCESSING_COMPLETED"
