"""agent 层共享业务 fixture。

- sqlite：临时 SqliteBackend 基座，conv_data / everything_data 等包装型
  fixture 依赖注入使用，连接随基座关闭（防 aiosqlite 线程残留阻塞退出）
"""

from __future__ import annotations

import pytest


@pytest.fixture
async def sqlite(tmp_path):
    """临时 SQLite 后端连接，用例结束自动关闭。"""
    from agent.storage.sqlite_backend import SqliteBackend

    backend = SqliteBackend(db_path=str(tmp_path / "agent.sqlite3"))
    yield backend
    await backend.close()
