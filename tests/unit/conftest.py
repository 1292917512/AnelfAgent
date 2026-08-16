"""unit 层共享业务 fixture。

- store：临时库 MemoryStore（memory / task / services 多处共用的标准形态；
  需要额外注册/解绑的用例在本地 fixture 中包装，勿在此堆叠特化逻辑）
"""

from __future__ import annotations

import pytest


@pytest.fixture
async def store(tmp_path):
    """临时 SQLite 记忆库，用例结束自动关闭。"""
    from agent.memory.memory_store import MemoryStore

    s = MemoryStore(str(tmp_path / "memory.sqlite3"))
    yield s
    await s.close()
