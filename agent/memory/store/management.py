"""记忆表的管理编辑规则；派生全文索引与正文使用调用者的同一事务。

模型看到的记忆来源和召回通道不变；无新增 token；不修改上下文前缀。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from .tokenizer import tokenize_for_index


async def sync_memory_fts(conn: aiosqlite.Connection, rowid: int, content: str) -> None:
    """更新手工维护的全文索引；失败向上传播，由调用者回滚整个编辑事务。"""
    tokenized = await asyncio.to_thread(tokenize_for_index, content)
    await conn.execute("DELETE FROM memories_fts WHERE rowid=?", (rowid,))
    await conn.execute("INSERT INTO memories_fts(rowid, content) VALUES(?, ?)", (rowid, tokenized))


async def maintain_memory_update(
    conn: aiosqlite.Connection, rowid: int, values: Mapping[str, Any],
    columns: Sequence[Mapping[str, Any]], *, vector_invalidated: bool,
) -> None:
    """维护手工编辑后的记忆版本及派生索引；不提交调用者的事务。"""
    if "version" not in values and any(column["name"] == "version" for column in columns):
        await conn.execute("UPDATE memories SET version=COALESCE(version, 0)+1 WHERE rowid=?", (rowid,))
    if vector_invalidated:
        exists = await (await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_vec'"
        )).fetchone()
        if exists:
            await conn.execute("DELETE FROM memories_vec WHERE rowid=?", (rowid,))
    if "content" in values:
        await sync_memory_fts(conn, rowid, str(values["content"]))
