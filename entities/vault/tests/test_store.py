"""密码本数据库初始化与关闭的异常、取消和并发边界。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from entities.vault.store import VaultStore


@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_failed_initialization_closes_connection_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    store = VaultStore(str(tmp_path / "vault.sqlite3"))
    connections: list[aiosqlite.Connection] = []
    execute_schema = aiosqlite.Connection.executescript

    async def fail_once(db: aiosqlite.Connection, sql: str) -> aiosqlite.Cursor:
        connections.append(db)
        if len(connections) == 1:
            raise error_type("schema interrupted")
        return await execute_schema(db, sql)

    monkeypatch.setattr(aiosqlite.Connection, "executescript", fail_once)
    try:
        with pytest.raises(error_type, match="schema interrupted"):
            await store.initialize()
        with pytest.raises(ValueError):
            await connections[0].execute("SELECT 1")
        await store.set_meta("ready", "yes")
        assert await store.get_meta("ready") == "yes"
    finally:
        await store.close()
        for connection in connections:
            await connection.close()


@pytest.mark.timeout(30)
async def test_close_waits_for_inflight_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = VaultStore(str(tmp_path / "vault.sqlite3"))
    schema_started = asyncio.Event()
    finish_schema = asyncio.Event()
    execute_schema = aiosqlite.Connection.executescript

    async def delayed_schema(db: aiosqlite.Connection, sql: str) -> aiosqlite.Cursor:
        schema_started.set()
        await finish_schema.wait()
        return await execute_schema(db, sql)

    monkeypatch.setattr(aiosqlite.Connection, "executescript", delayed_schema)
    initialization = asyncio.create_task(store.initialize())
    closing: asyncio.Task[None] | None = None
    try:
        await schema_started.wait()
        closing = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not closing.done(), "关闭必须等待初始化释放连接"
        finish_schema.set()
        # 事件决定并发顺序；整体超时只检测死锁，不约束共享 runner 的磁盘延迟。
        await asyncio.gather(initialization, closing)
        assert store._db is None
        await store.set_meta("reopened", "yes")
        assert await store.get_meta("reopened") == "yes"
    finally:
        finish_schema.set()
        tasks = [initialization] if closing is None else [initialization, closing]
        await asyncio.gather(*tasks, return_exceptions=True)
        await store.close()
