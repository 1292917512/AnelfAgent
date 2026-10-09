"""取消事务不能被下一笔无关事务隐式提交。"""

import asyncio

import aiosqlite
import pytest

from agent.memory.store.connection import MemoryConnectionManager


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
