"""自动捕获的批次检查点与写入出处；恢复不重复生成已保存的提取结果。"""

from __future__ import annotations

import hashlib
import json
import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .memory_store import MemoryStore


class CaptureOutcome(Enum):
    """区分无需处理、成功消费和已记录的失败。"""

    IDLE = "idle"
    COMPLETED = "completed"
    FAILED = "failed"


class CaptureItem(BaseModel):
    """提取器的一条完整结果；无效字段不得被当作空提取。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    content: str = Field(min_length=4)
    type: Literal["fact", "event"] = "fact"
    speaker: str = ""
    topic: str = ""
    importance: float = Field(default=0.6, ge=0.3, le=1, allow_inf_nan=False)
    sensitivity: Literal["normal", "private", "secret"] = "normal"
    date: str = Field(default="", pattern=r"^(?:\d{4}-\d{2}-\d{2})?$")
    temporal_scope: Literal["", "episode", "state", "pattern"] = ""


class CaptureBatch(BaseModel):
    """待提交的材料和提取结果；消息范围在重试期间固定。"""

    rows: list[dict[str, Any]]
    background: list[dict[str, Any]] = Field(default_factory=list)
    items: list[CaptureItem] | None = None
    next_item: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    feedback_done: bool = False

    def item_key(self, scope: str, index: int) -> str:
        payload = [scope, [row["id"] for row in self.rows], index]
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


class CaptureState(BaseModel):
    """一个会话的消费游标和未完成批次。"""

    last_msg_id: int = 0
    counted_msg_id: int = 0
    pending_turns: int = 0
    warmup_threshold: int = 1
    batch: CaptureBatch | None = None


class CaptureJournal:
    """在记忆库中保存捕获进度，并核对已经随记忆事务提交的出处。"""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    async def load(self, scope: str) -> CaptureState:
        db = await self.store._get_db()
        cursor = await db.execute(
            "SELECT last_msg_id, counted_msg_id, pending_turns, warmup_threshold, pending_json "
            "FROM capture_cursors WHERE scope_key=?", (scope,),
        )
        row = await cursor.fetchone()
        if row is None:
            return CaptureState()
        return CaptureState(
            last_msg_id=row["last_msg_id"], counted_msg_id=row["counted_msg_id"],
            pending_turns=row["pending_turns"], warmup_threshold=row["warmup_threshold"],
            batch=CaptureBatch.model_validate_json(row["pending_json"]) if row["pending_json"] else None,
        )

    async def save(self, scope: str, state: CaptureState, *, failure: str = "") -> None:
        db = await self.store._get_db()
        async with self.store._tx(db):
            await db.execute(
                "INSERT INTO capture_cursors "
                "(scope_key, last_msg_id, counted_msg_id, pending_turns, warmup_threshold, pending_json, updated_ns) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope_key) DO UPDATE SET "
                "last_msg_id=excluded.last_msg_id, counted_msg_id=excluded.counted_msg_id, "
                "pending_turns=excluded.pending_turns, warmup_threshold=excluded.warmup_threshold, "
                "pending_json=excluded.pending_json, updated_ns=excluded.updated_ns",
                (scope, state.last_msg_id, state.counted_msg_id, state.pending_turns,
                 state.warmup_threshold, state.batch.model_dump_json() if state.batch else "", time.time_ns()),
            )
            if failure:
                await db.execute(
                    "INSERT INTO memory_audit(memory_id, action, detail, ts_ns, actor) VALUES(0,?,?,?,?)",
                    ("capture_failed", failure, time.time_ns(), "auto_capture"),
                )

    async def has_item(self, key: str) -> bool:
        """活跃或归档条目保留过本次写入出处时，不再执行该写入。"""
        db = await self.store._get_db()
        cursor = await db.execute(
            "SELECT 1 FROM memories, json_each(memories.metadata_json, '$.origins') AS origin "
            "WHERE json_extract(origin.value, '$.capture_key')=? "
            "UNION ALL SELECT 1 FROM memories_archive, "
            "json_each(memories_archive.metadata_json, '$.origins') AS origin "
            "WHERE json_extract(origin.value, '$.capture_key')=? LIMIT 1",
            (key, key),
        )
        return await cursor.fetchone() is not None
