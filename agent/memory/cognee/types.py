"""Cognee 边界层的稳定类型。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class CogneeOperation(str, Enum):
    UPSERT = "upsert"
    DELETE = "delete"


class CogneePipelineError(RuntimeError):
    """把流水线失败结果转换为异常，并保留根因类型供重试分类。"""

    def __init__(self, operation: str, error_class: str, message: str) -> None:
        self.error_class = error_class
        super().__init__(f"Cognee {operation} 失败: {error_class or 'UnknownError'}: {message}")


class CogneeAvailability(BaseModel):
    installed: bool
    enabled: bool
    ready: bool
    configuration_required: bool = False
    version: str = ""
    reason: str = ""


class CogneeRecallItem(BaseModel):
    id: str
    content: str
    score: float = 0.0
    source: Literal["cognee_graph", "cognee_chunk"] = "cognee_chunk"
    dataset_id: str = ""
    dataset_name: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: Any = None


class CogneeSyncItem(BaseModel):
    queue_id: int
    memory_id: int
    operation: CogneeOperation
    attempts: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class CogneeSyncStatus(BaseModel):
    enabled: bool
    running: bool
    pending: int = 0
    failed: int = 0
    synced: int = 0
    last_error: str = ""
    last_compact_at: float = 0.0
    last_compact_summary: str = ""
    # 写盘熔断暂停（投影认领与自动压缩暂缓，冷却到期自动恢复）
    paused: bool = False
    paused_until: float = 0.0
    pause_reason: str = ""


class CogneeCallResult(BaseModel):
    """任意公共 API 调用的稳定结果包装。"""

    ok: bool
    value: Any = None
    error: Optional[str] = None
