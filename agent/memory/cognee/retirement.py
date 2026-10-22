"""已验收索引的旧数据集清理；先无损归档及备份，再调用 SDK 删除。

模型看到什么：维护状态仅经状态工具按需返回，不参与召回。
token 影响：不调用抽取或生成模型。
缓存影响：不触碰对话前缀。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from agent.storage.volume_restore import backups_root

from ..memory_store import MemoryStore
from ..store.projection_state import ProjectionState
from .client import CogneeClient
from .errors import error_text
from .preflight import preserve_owned_document
from .storage import cognee_storage_stats


def _field(value: Any, key: str) -> str:
    return str(value.get(key, "") if isinstance(value, dict) else getattr(value, key, ""))


def previous_namespace(name: str, state: ProjectionState) -> str:
    """最长归属匹配，避免旧 base 前缀匹配当前代际。"""
    for prefix in sorted({state.namespace, *state.previous_namespaces}, key=len, reverse=True):
        if name.startswith(prefix + "_"):
            return prefix if prefix != state.namespace else ""
    return ""


class RetirementStatus(BaseModel):
    running: bool = False
    dataset_id: str = ""
    dataset_name: str = ""
    stage: str = ""
    documents: int = 0
    preserved: int = 0
    backup_path: str = ""
    error: str = ""
    completed_at: float = 0


class DatasetRetirement:
    """由协调器持有并关停的维护任务；与同步、配置和重建共用互斥锁。"""

    def __init__(self, store: MemoryStore, client: CogneeClient, lock: asyncio.Lock,
                 guard: Callable[[], ProjectionState],
                 verify: Callable[[ProjectionState, list[dict[str, Any]]], Awaitable[int]]) -> None:
        self.store, self.client, self.lock, self.guard = store, client, lock, guard
        self.verify = verify
        self.status = RetirementStatus()
        self._task: asyncio.Task[None] | None = None

    async def request(self, dataset_id: str) -> RetirementStatus:
        if self.status.running:
            raise ValueError("已有旧数据清理任务正在执行")
        state = self.guard()
        dataset = next((row for row in await self.client.list_datasets() if _field(row, "id") == dataset_id), None)
        name = _field(dataset, "name")
        if not dataset or not previous_namespace(name, state):
            raise ValueError("只能清理已记录的旧代际，不能清理当前或外部数据集")
        # list_datasets 期间另一个请求可能已开始；不重叠维护任务。
        if self.status.running:
            raise ValueError("已有旧数据清理任务正在执行")
        self.status = RetirementStatus(running=True, dataset_id=dataset_id, dataset_name=name, stage="checking")
        self._task = asyncio.create_task(self._run(dataset_id, name), name="memory.cognee.retire")
        return self.status.model_copy()

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self, dataset_id: str, name: str) -> None:
        try:
            async with self.lock:
                state = self.guard()
                namespace = previous_namespace(name, state)
                if not namespace:
                    raise ValueError("构建代际已变化，请重新确认清理目标")
                audit = await self.store.projection_state.audit()
                if (not audit["consistent"] or audit["pending"] or audit["failed"]
                        or audit["missing_or_stale"] or audit["extra"]):
                    raise ValueError("当前索引有待同步或覆盖不一致，请继续同步并验收后再清理")
                mappings = await self.store.projection_state.mappings()
                if any(str(row["dataset_id"]) == dataset_id for row in mappings):
                    raise ValueError("数据集仍被当前映射引用，禁止清理")
                await self.verify(state, mappings)
                documents = await self.client.list_data(dataset_id)
                self.status.documents = len(documents)
                self.status.stage = "preserving"
                for item in documents:
                    if not await preserve_owned_document(self.store.projection_state, state, item, dataset_id, name,
                                                         include_current_format=True):
                        raise ValueError(f"文档 {_field(item, 'id')} 无法确认原生归属，旧数据未清理")
                    self.status.preserved += 1
                self.status.stage = "backup"
                identity = hashlib.sha256(state.namespace.encode()).hexdigest()[:24]
                backup = backups_root() / "cognee-retirement" / f"memory-{identity}.sqlite3"
                staging = backup.with_name(f".{backup.stem}-{uuid.uuid4().hex}.partial")
                try:
                    await self.store.projection_state.backup(staging)
                    # 同一当前代际的退役归档累计进入这一份完整备份，校验成功后才替换。
                    os.replace(staging, backup)
                finally:
                    staging.unlink(missing_ok=True)
                self.status.backup_path = str(backup)
                self.status.stage = "deleting"
                await self.client.delete_dataset(dataset_id)
                remaining = await self.client.list_datasets()
                if any(_field(row, "id") == dataset_id for row in remaining):
                    raise RuntimeError("SDK 返回后仍存在目标数据集；保留归档与备份，请重试核验")
                if not any(previous_namespace(_field(row, "name"), state) == namespace for row in remaining):
                    state.previous_namespaces = [value for value in state.previous_namespaces if value != namespace]
                    await self.store.projection_state.save(state)
                self.status.stage = "completed"
                self.status.completed_at = time.time()
        except asyncio.CancelledError:
            self.status.stage, self.status.error = "interrupted", "清理已中断；归档保留，可再次选择剩余旧数据集继续"
            raise
        except Exception as exc:
            self.status.stage, self.status.error = "failed", error_text(exc)
        finally:
            self.status.running = False
            cognee_storage_stats.invalidate(self.client.config.absolute_data_root)
            cognee_storage_stats.schedule_refresh(self.client.config.absolute_data_root)
