"""记忆库取消操作时回收连接并回滚未完成的事务。"""

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from agent.memory.store.connection import MemoryConnectionManager


@pytest.mark.parametrize("cancel", [False, True])
async def test_initialization_failure_closes_connection_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool,
) -> None:
    manager = MemoryConnectionManager(str(tmp_path / "memory.sqlite3"))
    entered = asyncio.Event()
    connections: list[aiosqlite.Connection] = []
    init_schema = manager._init_schema

    async def interrupted(db: aiosqlite.Connection) -> None:
        connections.append(db)
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        raise RuntimeError("initialization failed")

    monkeypatch.setattr(manager, "_init_schema", interrupted)
    task = asyncio.create_task(manager.get_db())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await task
        assert manager.db is None and not manager.initialized
        with pytest.raises(ValueError, match="no active connection"):
            await connections[0].execute("SELECT 1")
        monkeypatch.setattr(manager, "_init_schema", init_schema)
        db = await manager.get_db()
        async with db.execute("SELECT COUNT(*) FROM memories") as cursor:
            row = await cursor.fetchone()
            assert row is not None and row[0] == 0
        assert manager.initialized
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await manager.close()


async def test_cancel_rolls_back_before_next_write():
    manager = MemoryConnectionManager(":memory:")
    async with aiosqlite.connect(":memory:") as db:
        await db.execute("CREATE TABLE t (value TEXT)")
        await db.commit()
        entered = asyncio.Event()

        async def cancelled_write():
            async with manager.tx(db):
                await db.execute("INSERT INTO t VALUES ('cancelled')")
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(cancelled_write())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with manager.tx(db):
            await db.execute("INSERT INTO t VALUES ('valid')")
        async with db.execute("SELECT value FROM t") as cursor:
            assert await cursor.fetchall() == [("valid",)]
