"""持久事件收件箱、投递状态和出站对象去重。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, TypeVar

from .events import RawEvent
from .state import github_data_dir

T = TypeVar("T")


class InboxFull(RuntimeError):
    """收件箱容量不足，事件尚未接收。"""


def event_identity(raw: RawEvent) -> str:
    """优先按跨来源业务标识去重，缺失时使用来源投递标识。"""
    payload = raw.payload
    if raw.source == "test":
        return uuid.uuid4().hex
    identity: list[object] = [raw.repo_full_name.lower(), raw.name, raw.action]
    if raw.name == "push" and (payload.get("after") or payload.get("head")) and payload.get("before"):
        identity += [payload.get("ref"), payload["before"], payload.get("after") or payload["head"]]
    else:
        subject: dict[str, Any] = next((payload[key] for key in ("comment", "review", "release", "issue", "pull_request", "discussion", "workflow_run")
                        if isinstance(payload.get(key), dict)), {})
        timestamp = subject.get("published_at") if raw.name == "release" else subject.get("updated_at") or subject.get("created_at")
        if subject.get("id") and timestamp:
            identity += [subject["id"], timestamp]
        else:
            identity += [raw.source, raw.event_id or uuid.uuid4().hex]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class EventJournal:
    """原子接收事件，成功投递后确认，失败事件保留供恢复。"""

    def __init__(self, directory: str = "") -> None:
        self.path = Path(directory or github_data_dir()) / "events.sqlite3"
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """在工作线程内串行执行独立事务。"""
        async with self._lock:
            return await asyncio.to_thread(self._transaction, operation)

    def _transaction(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            if not self._initialized:
                connection.executescript("""
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS inbox (
                        id TEXT PRIMARY KEY, raw TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                        attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
                        error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS inbox_pending ON inbox(state, available_at, created_at);
                    CREATE TABLE IF NOT EXISTS outgoing (object_key TEXT PRIMARY KEY, created_at REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS deliveries (delivery_key TEXT PRIMARY KEY, event_key TEXT NOT NULL);
                """)
                self._initialized = True
            with connection:
                return operation(connection)
        finally:
            connection.close()

    async def accept(self, raw: RawEvent, capacity: int) -> tuple[str, bool]:
        """持久化完整载荷后返回，重复事件不占用容量。"""
        key = event_identity(raw)
        body = json.dumps(asdict(raw), ensure_ascii=False)
        now = time.time()
        delivery = f"{raw.source}:{raw.account}:{raw.event_id}" if raw.event_id else ""

        def insert(connection: sqlite3.Connection) -> tuple[str, bool]:
            if delivery:
                existing = connection.execute("SELECT event_key FROM deliveries WHERE delivery_key=?", (delivery,)).fetchone()
                if existing:
                    return existing[0], False
            if connection.execute("SELECT 1 FROM inbox WHERE id=?", (key,)).fetchone():
                if delivery:
                    connection.execute("INSERT INTO deliveries VALUES(?,?)", (delivery, key))
                return key, False
            count = connection.execute("SELECT count(*) FROM inbox WHERE state!='done'").fetchone()[0]
            if count >= capacity:
                raise InboxFull("GitHub 事件收件箱已满，请处理失败事件后重新投递")
            connection.execute("INSERT INTO inbox(id, raw, created_at, updated_at) VALUES(?,?,?,?)", (key, body, now, now))
            if delivery:
                connection.execute("INSERT INTO deliveries VALUES(?,?)", (delivery, key))
            return key, True

        return await self._run(insert)

    async def pending(self, limit: int = 100) -> list[tuple[str, RawEvent]]:
        """按接收顺序读取可处理的事件。"""
        def read(connection: sqlite3.Connection) -> list[tuple[str, RawEvent]]:
            rows = connection.execute(
                "SELECT id, raw FROM inbox WHERE state='pending' AND available_at<=? ORDER BY created_at LIMIT ?",
                (time.time(), limit),
            ).fetchall()
            return [(row["id"], RawEvent(**json.loads(row["raw"]))) for row in rows]
        return await self._run(read)

    async def complete(self, keys: list[str]) -> None:
        """确认已投递或按配置忽略的事件。"""
        await self._run(lambda connection: connection.executemany(
            "UPDATE inbox SET state='done', error='', updated_at=? WHERE id=?",
            [(time.time(), key) for key in keys],
        ))

    async def claim(self, key: str) -> bool:
        """原子领取到期记录，排除旧快照和已完成事件。"""
        return await self._run(lambda connection: connection.execute(
            "UPDATE inbox SET state='processing' WHERE id=? AND state='pending' AND available_at<=?",
            (key, time.time()),
        ).rowcount == 1)

    async def restore_processing(self) -> None:
        """启动时恢复中断的处理和聚合记录。"""
        await self._run(lambda connection: connection.execute("UPDATE inbox SET state='pending' WHERE state='processing'"))

    async def failed(self, key: str, error: str) -> None:
        """记录失败并退避，连续失败达到上限后等待人工重试。"""
        def update(connection: sqlite3.Connection) -> None:
            row = connection.execute("SELECT attempts FROM inbox WHERE id=?", (key,)).fetchone()
            if row is None:
                return
            attempts = row[0] + 1
            connection.execute(
                "UPDATE inbox SET attempts=?, state=?, available_at=?, error=?, updated_at=? WHERE id=?",
                (attempts, "failed" if attempts >= 8 else "pending", time.time() + min(2 ** attempts, 300),
                 error[:500], time.time(), key),
            )
        await self._run(update)

    async def retry(self, key: str = "") -> int:
        """将失败事件恢复为待处理状态。"""
        def update(connection: sqlite3.Connection) -> int:
            return connection.execute(
                "UPDATE inbox SET state='pending', attempts=0, available_at=0, error='' WHERE state='failed' AND (?='' OR id=?)",
                (key, key),
            ).rowcount
        return await self._run(update)

    async def snapshot(self, limit: int = 50) -> dict[str, Any]:
        """返回队列计数和待处理记录，隐藏原始载荷。"""
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            counts = {row[0]: row[1] for row in connection.execute("SELECT state,count(*) FROM inbox GROUP BY state")}
            rows = connection.execute(
                "SELECT id,state,attempts,error,created_at FROM inbox WHERE state!='done' ORDER BY created_at LIMIT ?", (limit,),
            ).fetchall()
            return {"counts": counts, "events": [dict(row) for row in rows]}
        return await self._run(read)

    async def event(self, event_id: str) -> dict[str, Any] | None:
        """按收件箱 ID 或平台投递 ID 查询完整事件载荷。"""
        def read(connection: sqlite3.Connection) -> dict[str, Any] | None:
            row = connection.execute(
                "SELECT raw FROM inbox WHERE id=? OR json_extract(raw, '$.event_id')=? ORDER BY created_at DESC LIMIT 1",
                (event_id, event_id),
            ).fetchone()
            return json.loads(row[0]) if row else None
        return await self._run(read)

    async def remember_outgoing(self, repo: str, kind: str, object_id: object) -> None:
        """记录本频道写入的评论对象。"""
        if object_id is not None:
            await self._run(lambda connection: connection.execute(
                "INSERT OR REPLACE INTO outgoing VALUES(?,?)", (f"{repo.lower()}:{kind}:{object_id}", time.time()),
            ))

    async def is_outgoing(self, raw: RawEvent) -> bool:
        """识别本频道评论回调，保留其他机器人的事件。"""
        comment = raw.payload.get("comment") or {}
        if not isinstance(comment, dict) or not comment.get("id") or raw.action != "created":
            return False
        key = f"{raw.repo_full_name.lower()}:comment:{comment['id']}"
        return await self._run(lambda connection: connection.execute(
            "SELECT 1 FROM outgoing WHERE object_key=?", (key,),
        ).fetchone() is not None)

    async def prune(self) -> None:
        """清理过期的已完成事件和出站标识。"""
        def remove(connection: sqlite3.Connection) -> None:
            connection.execute("DELETE FROM inbox WHERE state='done' AND updated_at<?", (time.time() - 7 * 86400,))
            connection.execute("DELETE FROM outgoing WHERE created_at<?", (time.time() - 30 * 86400,))
            connection.execute("DELETE FROM deliveries WHERE event_key NOT IN (SELECT id FROM inbox)")
        await self._run(remove)
