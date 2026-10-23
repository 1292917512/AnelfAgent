"""权威记录与后端文档的一对多映射；空清单表示既有单文档映射。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, TypeAdapter


class MappedDocument(BaseModel):
    part_key: str = Field(min_length=1)
    data_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)


_DOCUMENTS = TypeAdapter(list[MappedDocument])


def mapped_documents(mapping: dict[str, Any] | None) -> list[MappedDocument]:
    """损坏或重复清单必须报错，不能退回单文档后误删其他部分。"""
    if not mapping:
        return []
    documents = _DOCUMENTS.validate_json(mapping.get("documents_json") or "[]")
    if len({row.part_key for row in documents}) != len(documents) or len({row.data_id for row in documents}) != len(documents):
        raise ValueError("投影文档清单含重复标识")
    return documents


def expand_mapping(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    documents = mapped_documents(mapping)
    return [{**mapping, **document.model_dump()} for document in documents] if documents else [mapping]
