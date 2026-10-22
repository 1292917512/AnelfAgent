"""批次观测：只在状态查询时读取 SDK，不创建后台任务或改变写入语义。

模型看到什么：状态工具按需返回进度，不主动注入对话。
token 影响：查询时增加少量状态字段，不调用模型。
缓存影响：仅工具结果，不触碰稳定前缀。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from .errors import error_text
from .types import CogneeBatchProgress, CogneePipelineProgress


class ProjectionProgress:
    """宿主阶段即时更新；SDK 计数短缓存，读取失败不影响投影。"""

    def __init__(self) -> None:
        self._active: CogneeBatchProgress | None = None
        self._dataset_id = ""
        self._started = self._stage_started = self._changed = 0.0
        self._read_after = 0.0
        self._read_lock = asyncio.Lock()

    @contextmanager
    def batch(self, entries: list[dict[str, Any]]) -> Iterator[None]:
        now = time.time()
        self._active = CogneeBatchProgress(
            stage="prepare", items=len(entries), attempt=max(int(e.get("attempts", 0)) for e in entries) + 1,
            started_at=now, last_change_at=now,
        )
        self._started = self._stage_started = self._changed = time.monotonic()
        try:
            yield
        finally:
            self._active = None
            self._dataset_id = ""

    def stage(self, name: str, *, dataset_name: str = "", dataset_id: str = "", timeout: float = 0) -> None:
        if self._active is None:
            return
        # 用新对象隔离尚未完成的观测请求，避免上一阶段的响应污染下一阶段。
        self._active = self._active.model_copy(update={
            "stage": name, "dataset_name": dataset_name, "timeout_seconds": timeout,
            "last_change_at": time.time(), "pipeline": None,
            "progress_checked_at": 0, "progress_error": "",
        })
        self._dataset_id = dataset_id
        self._stage_started = self._changed = time.monotonic()
        self._read_after = 0

    async def snapshot(
        self, read: Callable[[str], Awaitable[CogneePipelineProgress]],
    ) -> CogneeBatchProgress | None:
        active = self._active
        if active is None:
            return None
        if active.stage == "cognify" and self._dataset_id:
            async with self._read_lock:
                if active is self._active and time.monotonic() >= self._read_after:
                    try:
                        async with asyncio.timeout(3):
                            pipeline = await read(self._dataset_id)
                        if active is self._active:
                            # SDK 当前阶段是聚合报告，不能解释为每篇文档都正在此阶段。
                            if pipeline != active.pipeline:
                                self._changed = time.monotonic()
                                active.last_change_at = time.time()
                            active.pipeline = pipeline
                            active.progress_error = ""
                            active.progress_checked_at = time.time()
                    except Exception as exc:
                        if active is self._active:
                            active.progress_error = error_text(exc)
                    finally:
                        if active is self._active:
                            self._read_after = time.monotonic() + 2
        active = self._active
        if active is None:
            return None
        now = time.monotonic()
        return active.model_copy(update={
            "elapsed_seconds": max(0, now - self._started),
            "stage_elapsed_seconds": max(0, now - self._stage_started),
            "quiet_seconds": max(0, now - self._changed),
        })
