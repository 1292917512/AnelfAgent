"""重建前核对旧投影归属，并将无法通过映射追踪的历史快照无损留存。

模型看到什么：历史快照仅供审计，当前权威记忆继续决定召回内容。
token 影响：本地核验不调用模型，不重复抽取过时事实。
缓存影响：不触碰对话前缀或动态上下文。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname
from uuid import UUID

from ..projection import PROJECTION_VERSION
from ..store.projection_state import ProjectionState, ProjectionStateStore
from .ports import ProjectionBackend


def _field(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _identity(value: Any) -> str:
    text = str(value or "")
    try:
        return str(UUID(text))
    except ValueError:
        return text


def _read_text(root: str, location: str) -> str:
    """只读取宿主 Cognee data 目录内的 UTF-8 原文；越界、缺失和截断均拒绝。"""
    uri = urlsplit(location)
    if uri.scheme != "file" or uri.netloc not in {"", "localhost"} or uri.query or uri.fragment:
        raise ValueError("旧文档必须有可核验的本地 file URI")
    path = Path(url2pathname(uri.path)).resolve(strict=True)
    if not path.is_relative_to((Path(root) / "data").resolve()):
        raise ValueError("旧文档路径越出 Cognee data 目录")
    with path.open("rb") as stream:
        content = stream.read(32 * 1024 * 1024 + 1)
    if len(content) > 32 * 1024 * 1024:
        raise ValueError("旧文档超过 32 MiB，需先单独核验；未截断留存")
    return content.decode("utf-8")


def _legacy_body(text: str, kind: str, entry_id: int, source: dict[str, Any]) -> str | None:
    """仅识别应用生成的旧包装格式；正文保留原样，不将包装字段作为事实导入。"""
    normalized = text.replace("\r\n", "\n")
    if kind == "graph_node":
        header = normalized.split("\n", 1)[0]
        suffix = f"（{source['node_key']}，类型 {source['node_type']}）"
        return text if header.startswith("[关系节点] ") and header.endswith(suffix) else None
    header, separator, body = normalized.partition("\n\n")
    if not separator or not body:
        return None
    fields: dict[str, str] = {}
    for line in header.split("\n"):
        key, delimiter, value = line.partition(": ")
        if not delimiter or key in fields:
            return None
        fields[key] = value
    required = {"Memory type", "Source", "Importance", "Tags", "Metadata"}
    if not required <= fields.keys() or fields.keys() - required - {"Memory ID", "Timestamp"}:
        return None
    if "Memory ID" in fields and fields["Memory ID"] != str(entry_id):
        return None
    try:
        if not isinstance(json.loads(fields["Metadata"]), dict):
            return None
        float(fields["Importance"])
    except (ValueError, TypeError):
        return None
    return body


async def preserve_owned_document(
    store: ProjectionStateStore, state: ProjectionState, item: Any,
    dataset_id: str, dataset_name: str, *, include_current_format: bool = False,
) -> bool:
    """完整保存可确认原生归属的投影原文；未知格式不会因清理请求而被静默接受。"""
    metadata = _field(item, "external_metadata", {})
    if not isinstance(metadata, dict):
        return False
    current_format = metadata.get("anelf_projection_version") == PROJECTION_VERSION
    if "anelf_projection_version" in metadata and not (include_current_format and current_format):
        return False
    identities = [(kind, metadata[key]) for kind, key in (
        ("memory", "anelf_memory_id"), ("graph_node", "anelf_graph_node_id"),
    ) if key in metadata]
    if len(identities) != 1:
        return False
    kind, raw_id = identities[0]
    if not str(raw_id).isdecimal() or int(raw_id) <= 0:
        return False
    entry_id = int(raw_id)
    label_kind = "memory" if kind == "memory" else "graph-node"
    if _field(item, "label") != f"anelf-{label_kind}-{entry_id}":
        return False
    source = await store.legacy_source(kind, entry_id)
    if source is None:
        return False
    data_id = _identity(_field(item, "id"))
    try:
        text = await asyncio.to_thread(_read_text, state.root, str(_field(item, "raw_data_location", "")))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"旧文档 {data_id} 原文无法完整读取: {exc}；映射未重置") from exc
    body = text if current_format else _legacy_body(text, kind, entry_id, source)
    if body is None:
        return False
    classification = "matching_memory" if kind == "memory" and body == source.get("content") else f"historical_{kind}"
    # SDK ORM 的 to_json 会遗漏 external_metadata；显式保存内容、标签及来源字段。
    document = {key: _field(item, key) for key in (
        "label", "name", "legacy_id", "mime_type", "extension", "raw_data_location", "original_data_location",
        "content_hash", "raw_content_hash", "external_metadata", "system_metadata", "node_set",
    )}
    document["legacy_id"] = _identity(document["legacy_id"])
    document.update(dataset_name=dataset_name, text=text, text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    await store.preserve_legacy_document(
        dataset_id=dataset_id, data_id=data_id, kind=kind, entry_id=entry_id,
        classification=classification, document=document,
    )
    return True


async def check_legacy_documents(
    store: ProjectionStateStore, client: ProjectionBackend, state: ProjectionState,
) -> None:
    """映射、写入日志和严格旧格式归属均不足时阻止重建，保留所有旧数据集。"""
    mappings = await store.mappings()
    sources = await store.preserved_sources()
    known = {(_identity(r["dataset_id"]), _identity(r["data_id"])) for r in mappings
             if (str(r["entry_kind"]), int(r["entry_id"])) in sources}
    mapped_datasets = {_identity(r["dataset_id"]) for r in mappings}
    staged = {(str(r["dataset_name"]), _identity(r["data_id"])) for r in await store.staged()
              if (str(r["entry_kind"]), int(r["entry_id"])) in sources}
    unknown: list[str] = []
    state.legacy_preserved = 0
    active_prefix = state.previous_namespaces[-1] if state.previous_namespaces else state.base_prefix
    for dataset in await client.list_datasets():
        name = str(_field(dataset, "name", ""))
        dataset_id = _identity(_field(dataset, "id"))
        if dataset_id not in mapped_datasets and not name.startswith(active_prefix + "_"):
            continue
        for item in await client.list_data(dataset_id):
            data_id = _identity(_field(item, "id"))
            legacy_id = _identity(_field(item, "legacy_id"))
            if ((dataset_id, data_id) in known or (dataset_id, legacy_id) in known or (name, data_id) in staged):
                continue
            if await preserve_owned_document(store, state, item, dataset_id, name):
                state.legacy_preserved += 1
            else:
                unknown.append(f"{dataset_id}/{data_id}")
    if unknown:
        raise RuntimeError(
            f"发现 {len(unknown)} 个无法确认原生归属的旧文档；需先确认或导入其独有内容，旧数据未清理。"
            f"文档 ID: {', '.join(unknown[:5])}"
        )
