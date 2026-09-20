"""路由层共享 Pydantic 模型。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CogneeChatModelUpdate(BaseModel):
    source: Optional[str] = None
    model_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    api_version: Optional[str] = None
    instructor_mode: Optional[str] = None
    max_completion_tokens: Optional[int] = None
    reasoning_effort: Optional[str] = None
    extra_args: Optional[dict] = None


class CogneeEmbeddingModelUpdate(BaseModel):
    source: Optional[str] = None
    model_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    dimensions: Optional[int] = None


class CogneeConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    sync_enabled: Optional[bool] = None
    recall_enabled: Optional[bool] = None
    data_root: Optional[str] = None
    dataset_prefix: Optional[str] = None
    timeout_seconds: Optional[float] = None
    pipeline_timeout_seconds: Optional[float] = None
    improve_interval_seconds: Optional[float] = None
    sync_interval_seconds: Optional[float] = None
    sync_batch_size: Optional[int] = None
    max_retries: Optional[int] = None
    compact_enabled: Optional[bool] = None
    compact_interval_seconds: Optional[float] = None
    compact_retention_days: Optional[float] = None
    write_breaker_enabled: Optional[bool] = None
    write_breaker_threshold_mb: Optional[float] = None
    write_breaker_window_seconds: Optional[float] = None
    write_breaker_cooldown_seconds: Optional[float] = None
    native_weight: Optional[float] = None
    cognee_weight: Optional[float] = None
    rrf_k: Optional[int] = None
    recall_pool_multiplier: Optional[int] = None
    search_types: Optional[list[str]] = None
    deep_search_types: Optional[list[str]] = None
    chat: Optional[CogneeChatModelUpdate] = None
    embedding: Optional[CogneeEmbeddingModelUpdate] = None


class CogneeBackfillRequest(BaseModel):
    limit: int = 0
    dry_run: bool = True


class CogneeImproveRequest(BaseModel):
    dataset_name: str
