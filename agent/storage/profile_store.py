"""画像正文与操作回执的原子存储；计数与身份映射保持各自的存储契约。"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

import aiosqlite


class ProfileConflict(ValueError):
    """生成或编辑期间画像已变化，调用方必须重新读取后再决策。"""


def profile_revision(scope_type: str, scope_id: str, content: str) -> str:
    """绑定主身份与正文的编辑基线；计数变化不使基线失效。"""
    return hashlib.sha256(f"{scope_type}\0{scope_id}\0{content}".encode()).hexdigest()


@dataclass(frozen=True)
class ProfileOperation:
    id: int
    operation_id: str
    scope_type: str
    scope_id: str
    action: str
    actor: str
    reason: str
    source_memory_id: int | None
    before_content: str
    after_content: str
    created_at: float
    synced: bool
    error: str
    settled: bool = True

    def summary(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k not in {"before_content", "after_content"}}


async def init_profile_operations(db: aiosqlite.Connection) -> None:
    """只增加操作表，不改写旧画像、别名、计数或 schema user_version。"""
    await db.execute("""CREATE TABLE IF NOT EXISTS profile_operations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id TEXT NOT NULL UNIQUE,
        scope_type TEXT NOT NULL, scope_id TEXT NOT NULL,
        action TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
        source_memory_id INTEGER,
        before_content TEXT NOT NULL, after_content TEXT NOT NULL,
        created_at REAL NOT NULL, synced INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '', settled INTEGER NOT NULL DEFAULT 1
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_profile_operations_scope "
                     "ON profile_operations(scope_type, scope_id, id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_profile_operations_pending "
                     "ON profile_operations(id) WHERE synced=0")


def _operation(row: aiosqlite.Row) -> ProfileOperation:
    return ProfileOperation(
        id=int(row[0]), operation_id=str(row[1]), scope_type=str(row[2]), scope_id=str(row[3]),
        action=str(row[4]), actor=str(row[5]), reason=str(row[6]), source_memory_id=row[7],
        before_content=str(row[8]), after_content=str(row[9]), created_at=float(row[10]),
        synced=bool(row[11]), error=str(row[12]), settled=bool(row[13]),
    )


class ProfileStore:
    """由主库托管的画像存储；lock 串行化正文提交、镜像与在线缓存同步。"""

    def __init__(
        self, get_db: Callable[[], Awaitable[aiosqlite.Connection]],
        write_tx: Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]],
    ) -> None:
        self._get_db = get_db
        self._write_tx = write_tx
        self.lock = asyncio.Lock()

    async def get_operation(self, operation_id: str) -> ProfileOperation | None:
        db = await self._get_db()
        cursor = await db.execute("SELECT * FROM profile_operations WHERE operation_id=?", (operation_id,))
        row = await cursor.fetchone()
        return _operation(row) if row else None

    async def commit(
        self, scope_type: str, scope_id: str, content: str, *, expected_revision: str,
        actor: str, reason: str = "", operation_id: str = "",
        source_memory_id: int | None = None, delete: bool = False,
    ) -> ProfileOperation | None:
        """正文与回执同事务提交；旧操作重试不重写正文，无变化的普通保存不留空修订。"""
        async with self._write_tx() as db:
            await db.execute("BEGIN IMMEDIATE")
            if operation_id:
                cursor = await db.execute("SELECT * FROM profile_operations WHERE operation_id=?", (operation_id,))
                row = await cursor.fetchone()
                if row:
                    op = _operation(row)
                    if (op.scope_type, op.scope_id, op.after_content, op.source_memory_id) != (
                        scope_type, scope_id, content, source_memory_id,
                    ):
                        raise ValueError("画像操作 ID 已用于不同内容")
                    await db.commit()
                    return op
            cursor = await db.execute(
                "SELECT personality FROM entity_profile WHERE scope_type=? AND scope_id=?", (scope_type, scope_id),
            )
            row = await cursor.fetchone()
            before = str(row[0] or "") if row else ""
            if profile_revision(scope_type, scope_id, before) != expected_revision:
                raise ProfileConflict("画像或主身份已变化，请重新读取后修订")
            if before == content and not delete and not operation_id:
                await db.commit()
                return None
            if delete:
                await db.execute("DELETE FROM entity_profile WHERE scope_type=? AND scope_id=?", (scope_type, scope_id))
            elif before != content or row is None:
                await db.execute(
                    "INSERT INTO entity_profile(scope_type,scope_id,personality,updated_ts_ns) VALUES(?,?,?,?) "
                    "ON CONFLICT(scope_type,scope_id) DO UPDATE SET "
                    "personality=excluded.personality,updated_ts_ns=excluded.updated_ts_ns",
                    (scope_type, scope_id, content, time.time_ns()),
                )
            op = ProfileOperation(
                id=0, operation_id=operation_id or uuid4().hex, scope_type=scope_type, scope_id=scope_id,
                action="delete" if delete else ("update" if before else "create"), actor=actor, reason=reason,
                source_memory_id=source_memory_id, before_content=before, after_content=content,
                created_at=time.time(), synced=False, error="", settled=source_memory_id is None,
            )
            cursor = await db.execute(
                "INSERT INTO profile_operations(operation_id,scope_type,scope_id,action,actor,reason,source_memory_id,"
                "before_content,after_content,created_at,settled) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (op.operation_id, scope_type, scope_id, op.action, actor, reason, source_memory_id,
                 before, content, op.created_at, source_memory_id is None),
            )
            await db.commit()
            return ProfileOperation(**{**asdict(op), "id": int(cursor.lastrowid or 0)})

    async def mark_synced(self, operation: ProfileOperation, *, error: str = "") -> None:
        async with self._write_tx() as db:
            await db.execute("UPDATE profile_operations SET synced=?,error=? WHERE id=?",
                             (not error, error[:1000], operation.id))
            await db.commit()

    async def pending(self, limit: int = 50) -> list[ProfileOperation]:
        db = await self._get_db()
        cursor = await db.execute("SELECT * FROM profile_operations WHERE synced=0 ORDER BY id LIMIT ?", (limit,))
        return [_operation(row) for row in await cursor.fetchall()]

    async def unsettled(self, limit: int = 50) -> list[ProfileOperation]:
        db = await self._get_db()
        cursor = await db.execute("SELECT * FROM profile_operations WHERE settled=0 ORDER BY id LIMIT ?", (limit,))
        return [_operation(row) for row in await cursor.fetchall()]

    async def settle(self, operation_id: str) -> None:
        """来源状态已持久化后释放回执保留锁；未完成的跨库操作不能被历史清理删掉。"""
        async with self._write_tx() as db:
            await db.execute("UPDATE profile_operations SET settled=1 WHERE operation_id=?", (operation_id,))
            await db.commit()

    async def prune(self, *, days: int, per_profile: int, total: int) -> int:
        """仅裁剪已收尾历史，保留当前画像、未同步操作和待收尾晋升。"""
        cutoff = time.time() - max(1, days) * 86400
        async with self._write_tx() as db:
            cursor = await db.execute(
                "DELETE FROM profile_operations WHERE synced=1 AND settled=1 AND id IN ("
                "SELECT id FROM (SELECT id,created_at,"
                "ROW_NUMBER() OVER(PARTITION BY scope_type,scope_id ORDER BY id DESC) AS scope_rank,"
                "ROW_NUMBER() OVER(ORDER BY id DESC) AS total_rank "
                "FROM profile_operations WHERE synced=1 AND settled=1) "
                "WHERE created_at<? OR scope_rank>? OR total_rank>?)",
                (cutoff, max(1, per_profile), max(1, total)),
            )
            await db.commit()
            return max(0, cursor.rowcount)

    async def timeline(
        self, *, scope_type: str = "", scope_id: str = "", before_id: int = 0, limit: int = 30,
    ) -> list[ProfileOperation]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM profile_operations WHERE (?='' OR (scope_type=? AND scope_id=?)) "
            "AND (?=0 OR id<?) ORDER BY id DESC LIMIT ?",
            (scope_type, scope_type, scope_id, before_id, before_id, limit),
        )
        return [_operation(row) for row in await cursor.fetchall()]
