"""Cognee 边界层的稳定类型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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


class CogneeDocumentProgress(BaseModel):
    """本次摄入文档的抽取完成标记；不代表候选索引已安全提交。"""

    total: int = Field(ge=0)
    completed: int | None = Field(default=None, ge=0)
    fragments: int = Field(default=0, ge=0)
    fragments_completed: int | None = Field(default=None, ge=0)
    last_completed_at: float | None = None


class CogneePipelineProgress(BaseModel):
    """SDK 最近一次流水线报告；缺少计数表示尚未报告，不等于零。"""

    status: str = ""
    completed_items: int | None = Field(default=None, ge=0)
    total_items: int | None = Field(default=None, ge=0)
    current_stage: str = ""
    documents: CogneeDocumentProgress | None = None
    observation_error: str = ""


class CogneeBatchProgress(BaseModel):
    """当前宿主批次观测，不参与提交、重试或恢复判断。"""

    stage: str
    items: int
    attempt: int
    dataset_name: str = ""
    started_at: float
    elapsed_seconds: float = 0
    stage_elapsed_seconds: float = 0
    quiet_seconds: float = 0
    last_change_at: float
    timeout_seconds: float = 0
    pipeline: CogneePipelineProgress | None = None
    progress_checked_at: float = 0
    progress_error: str = ""
    documents: CogneeDocumentProgress | None = None
    documents_checked_at: float = 0


class CogneeMaintenanceStatus(BaseModel):
    """唯一维护任务的模式与阶段；不参与索引提交或恢复判断。"""

    mode: Literal["cleanup", "compact"]
    phase: Literal["queued", "running"] = "queued"


class CogneeSyncStatus(BaseModel):
    enabled: bool
    running: bool
    pending: int = 0
    failed: int = 0
    synced: int = 0
    processing: int = 0
    retrying: int = 0
    next_retry_at: float = 0
    active: CogneeBatchProgress | None = None
    pause_requested: bool = False
    configuration_pending: bool = False
    configuration_error: str = ""
    last_error: str = ""
    publication: dict[str, Any] = Field(default_factory=dict)
    last_compact_at: float = 0.0
    last_compact_summary: str = ""
    maintenance: CogneeMaintenanceStatus | None = None
    # 写盘熔断暂停（投影认领与自动压缩暂缓，冷却到期自动恢复）
    paused: bool = False
    paused_until: float = 0.0
    pause_reason: str = ""
