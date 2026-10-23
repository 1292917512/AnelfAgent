"""SQLite 共用原语：结果非空契约与在线一致性备份。"""

from pathlib import Path
from sqlite3 import Row
from typing import Callable

import aiosqlite


def required_row(row: Row | None) -> Row:
    """读取聚合或刚写入的记录；违反必有一行的查询契约时明确报错。"""
    if row is None:
        raise RuntimeError("SQLite 查询未返回预期记录")
    return row


async def online_sqlite_backup(
    src: Path, dest: Path, *, progress: Callable[[int, int, int], None] | None = None,
) -> None:
    """SQLite 在线热备份（Backup API，对并发写安全，自包含输出无 -wal/-shm）。

    数据库管理 / 卷备份 / 数据目录迁移共用的唯一备份实现。
    """
    src_conn = await aiosqlite.connect(src.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        dest_conn = await aiosqlite.connect(str(dest))
        try:
            await src_conn.backup(dest_conn, pages=256, progress=progress)
        finally:
            await dest_conn.close()
    finally:
        await src_conn.close()
