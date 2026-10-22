"""索引提交的独立清单与变更计划；只比较声明的变化，不调用 SDK 或修改权威数据。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from .documents import stored_node_tags
from .node_sets import projected_node_tags
from .wire import InputDocument


def document_identity(label: str, metadata: dict[str, Any], tags: Any) -> str:
    normalized = stored_node_tags(tags)
    metadata = dict(metadata)
    if "node_set" in metadata:
        if stored_node_tags(metadata.pop("node_set")) != normalized:
            raise RuntimeError("文档元数据标签与 NodeSet 不一致")
    payload = json.dumps([label, metadata, normalized], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class DocumentStamp(BaseModel):
    identity: str
    content_hash: str = ""
    raw_hash: str = ""
    source_digest: str = ""
    processed: bool = False
    chunks: str = ""


class DatasetStamp(BaseModel):
    name: str
    documents: dict[str, DocumentStamp] = Field(default_factory=dict)


class IndexManifest(BaseModel):
    version: Literal[1] = 1
    datasets: dict[str, DatasetStamp] = Field(default_factory=dict)

    def check_sources(self, mappings: list[dict[str, Any]]) -> None:
        """现有权威映射必须可追溯；在途写入日志由提供方排除，允许崩溃后幂等续跑。"""
        for row in mappings:
            dataset = self.datasets.get(str(row["dataset_id"]))
            document = dataset.documents.get(str(row["data_id"])) if dataset else None
            if document is None or document.content_hash != str(row["content_hash"]):
                raise RuntimeError(f"原生映射对应的索引缺失或指纹不符: {row['entry_kind']}:{row['entry_id']}")


class MutationPlan:
    """以写入前清单为基准，新增和删除只有经宿主明确登记才允许提交。"""

    def __init__(self, baseline: IndexManifest) -> None:
        self.baseline = baseline
        self.added: dict[str, DatasetStamp] = {}
        self.deleted: dict[str, set[str]] = {}
        self.retired: set[str] = set()
        self.required: dict[str, set[str]] = {}

    def add(self, dataset_id: str, name: str, documents: list[InputDocument]) -> None:
        target = self.added.setdefault(dataset_id, DatasetStamp(name=name))
        for document in documents:
            target.documents[document.data_id] = DocumentStamp(
                identity=document_identity(document.label, document.external_metadata,
                                           projected_node_tags(document.node_set)),
                content_hash=str(document.external_metadata.get("anelf_content_hash", "")),
                source_digest=hashlib.sha256(document.data.encode("utf-8")).hexdigest(),
            )

    def require_processed(self, dataset_id: str, data_ids: list[str]) -> None:
        self.required.setdefault(dataset_id, set()).update(data_ids)

    def validate(self, candidate: IndexManifest) -> None:
        expected_datasets = (self.baseline.datasets.keys() | self.added.keys()) - self.retired
        if candidate.datasets.keys() != expected_datasets:
            raise RuntimeError("索引数据集清单发生未声明变化，候选区不提交")
        for dataset_id in expected_datasets:
            before = self.baseline.datasets.get(dataset_id, self.added.get(dataset_id))
            assert before is not None
            after = candidate.datasets[dataset_id]
            additions = self.added.get(dataset_id, DatasetStamp(name=before.name)).documents
            expected = (before.documents.keys() | additions.keys()) - self.deleted.get(dataset_id, set())
            if after.name != before.name or after.documents.keys() != expected:
                raise RuntimeError(f"索引文档清单发生未声明变化（数据集 {dataset_id}），候选区不提交")
            for data_id in expected:
                previous = additions.get(data_id, before.documents.get(data_id))
                assert previous is not None
                current = after.documents[data_id]
                if previous.identity != current.identity or previous.content_hash != current.content_hash:
                    raise RuntimeError(f"索引文档内容或标签发生未声明变化: {data_id}")
                if previous.source_digest != current.source_digest:
                    raise RuntimeError(f"索引文档实际原文与提交内容不符: {data_id}")
                if data_id not in additions:
                    if previous.raw_hash != current.raw_hash or (previous.processed and not current.processed):
                        raise RuntimeError(f"既有文档原文或完成标记损坏: {data_id}")
                    if previous.chunks and current.chunks and previous.chunks != current.chunks:
                        raise RuntimeError(f"既有文档的分块覆盖发生变化: {data_id}")
            for data_id in self.required.get(dataset_id, set()) - self.deleted.get(dataset_id, set()):
                document = after.documents.get(data_id)
                if not document or not document.processed or not document.chunks:
                    raise RuntimeError(f"文档尚未完成抽取或实际分块验收: {data_id}")
