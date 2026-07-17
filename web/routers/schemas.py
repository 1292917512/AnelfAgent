"""路由层共享 Pydantic 模型。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class MindConfigUpdate(BaseModel):
    heartbeat_interval: Optional[float] = None
    meta_decision_temperature: Optional[float] = None
    conversation_analysis_threshold: Optional[int] = None
    max_tool_iterations: Optional[int] = None
    log_ai_output: Optional[bool] = None
    send_interim_text: Optional[bool] = None
    vector_search_batch_size: Optional[int] = None
    memory_recall_top_k: Optional[int] = None
    memory_recall_min_score: Optional[float] = None
    memory_time_decay_days: Optional[int] = None
    memory_warn_threshold: Optional[int] = None
    memory_max_per_type: Optional[int] = None
    heartbeat_max_entries: Optional[int] = None
    auto_consolidate_enabled: Optional[bool] = None
    short_term_memory_size: Optional[int] = None
    tool_recall_top_n: Optional[int] = None
    llm_timeout: Optional[float] = None
    llm_max_retries: Optional[int] = None
    tool_system_rules: Optional[list] = None
    cross_channel_enabled: Optional[bool] = None
    cross_channel_window_minutes: Optional[int] = None
    cross_channel_recall_min_score: Optional[float] = None
    cross_channel_recall_max_results: Optional[int] = None
    cross_channel_recall_scan_limit: Optional[int] = None
    cross_channel_narrative_max_items: Optional[int] = None
    reasoning_effort: Optional[str] = None


class CogneeConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    sync_enabled: Optional[bool] = None
    recall_enabled: Optional[bool] = None
    data_root: Optional[str] = None
    dataset_prefix: Optional[str] = None
    timeout_seconds: Optional[float] = None
    sync_interval_seconds: Optional[float] = None
    sync_batch_size: Optional[int] = None
    max_retries: Optional[int] = None
    native_weight: Optional[float] = None
    cognee_weight: Optional[float] = None
    rrf_k: Optional[int] = None
    recall_pool_multiplier: Optional[int] = None
    search_types: Optional[list[str]] = None


class CogneeBackfillRequest(BaseModel):
    limit: int = 0
    dry_run: bool = True


class CogneeImproveRequest(BaseModel):
    dataset_name: str
