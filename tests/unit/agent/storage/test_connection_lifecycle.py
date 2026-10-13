"""数据库初始化失败或取消时的连接归属与恢复。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from agent.storage.sqlite_backend import SqliteBackend


@pytest.mark.parametrize("cancel", [False, True])
async def test_unpublished_connection_is_closed_and_initialization_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool,
) -> None:
    backend = SqliteBackend(str(tmp_path / "agent.sqlite3"))
    entered = asyncio.Event()
    connections: list[aiosqlite.Connection] = []
    execute = aiosqlite.Connection.execute

    async def interrupted(db: aiosqlite.Connection, sql: str, parameters=None):
        connections.append(db)
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        raise RuntimeError("initialization failed")

    monkeypatch.setattr(aiosqlite.Connection, "execute", interrupted)
    task = asyncio.create_task(backend._ensure_init())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await task
        assert backend._db is None and not backend._initialized
        monkeypatch.setattr(aiosqlite.Connection, "execute", execute)
        with pytest.raises(ValueError, match="no active connection"):
            await connections[0].execute("SELECT 1")
        await backend.append_conversation(
            scope_type="user", scope_id="qq:1", role="user", content="recovered", ts_ns=1,
        )
        rows = await backend.fetch_live_conversation(scope_type="user", scope_id="qq:1", limit=10)
        assert rows[0]["content"] == "recovered"
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await backend.close()
