"""SDK 进程边界的数据协议；不导入 Cognee，不传递可执行对象。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5


@dataclasses.dataclass(frozen=True)
class InputDocument:
    data: str
    label: str
    external_metadata: dict[str, Any]
    node_set: list[str]
    data_id: str
    literal_text: bool = True


def make_document(data: str, *, label: str, external_metadata: dict[str, Any],
                  dataset_name: str = "", node_set: list[str] | None = None) -> InputDocument:
    tags = sorted(set(node_set or []))
    identity = json.dumps([data, external_metadata, tags], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data_id = uuid5(NAMESPACE_URL, f"anelf:{dataset_name}:{label}:{hashlib.sha256(identity.encode()).hexdigest()}")
    return InputDocument(data, label, external_metadata, tags, str(data_id))


def encode(value: Any) -> Any:
    if isinstance(value, InputDocument):
        return {"__input_document__": dataclasses.asdict(value)}
    if isinstance(value, Enum):
        return encode(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (UUID, datetime, date)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [encode(item) for item in value]
    if hasattr(value, "model_dump"):
        return encode(value.model_dump(mode="python"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return encode(dataclasses.asdict(value))
    if hasattr(value, "__table__"):
        # ORM to_json 会漏掉标签和 external_metadata，必须逐列完整返回。
        return {column.name: encode(getattr(value, column.name)) for column in value.__table__.columns}
    raise TypeError(f"不支持的 Cognee 返回类型: {type(value).__name__}")


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"__input_document__"}:
            from cognee.tasks.ingestion.data_item import DataItem

            document = dict(value["__input_document__"])
            document["data_id"] = UUID(document["data_id"])
            return DataItem(**document)
        return {key: decode(item) for key, item in value.items()}
    return value
