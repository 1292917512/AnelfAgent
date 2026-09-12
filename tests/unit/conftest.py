"""unit 层共享业务 fixture。

- store：临时库 MemoryStore（memory / task / services 多处共用的标准形态；
  需要额外注册/解绑的用例在本地 fixture 中包装，勿在此堆叠特化逻辑）
- _isolate_delegation_dir：委托运行日志目录重定向（任何经
  DelegationManager 的用例都会落进度流/transcript/ledger，防污染真实数据目录）
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


@pytest.fixture(autouse=True)
def _isolate_delegation_dir(tmp_path_factory, monkeypatch):
    """DELEGATION_DIR 重定向到会话级 tmp 目录（进度流/transcript/ledger
    全落测试临时区；共享目录避免逐用例建目录）。"""
    from core import path as path_mod

    target = tmp_path_factory.getbasetemp() / "delegations"
    target.mkdir(exist_ok=True)
    monkeypatch.setattr(
        path_mod.ConfigPaths, "DELEGATION_DIR", str(target), raising=False,
    )
    yield
