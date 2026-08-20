"""存储默认路径构造回归 — 各存储无参构造必须把卷解析路径传给连接层。

锁定 2026-08 生产事故：MemoryStore() 默认构造曾把 None 参数（而非
解析后的 ``self._db_path``）传给 MemoryConnectionManager，init_memory
节点以 ``expected str, bytes or os.PathLike object, not NoneType`` 崩溃。
显式传参的测试路径覆盖不到该缺陷——生产 bootstrap 走的正是无参构造。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_main_path(tmp_path, monkeypatch):
    """主库路径指向 tmp（同族库 stem 派生随之隔离）。"""
    monkeypatch.delenv("ANELF_BOT_SQLITE_PATH", raising=False)
    monkeypatch.setenv("ANELF_BOT_SQLITE_PATH", str(tmp_path / "agent.sqlite3"))


async def test_sqlite_backend_default_construction(tmp_path):
    from agent.storage.sqlite_backend import SqliteBackend

    backend = SqliteBackend()
    assert backend.db_path == str(tmp_path / "agent.sqlite3")


async def test_memory_store_default_construction_opens_db(tmp_path):
    """事故回归：默认构造的连接层必须拿到真实路径并可建连。"""
    from agent.memory.memory_store import MemoryStore

    store = MemoryStore()
    assert store._db_path == str(tmp_path / "agent_memory.sqlite3")
    db = await store._get_db()
    assert db is not None
    await store.close()


async def test_entity_stores_default_construction(tmp_path):
    from entities.share.store import ShareStore
    from entities.sticker.store import StickerStore
    from entities.voiceprint.store import VoiceprintStore

    assert StickerStore()._db_path == str(tmp_path / "agent_stickers.sqlite3")
    assert VoiceprintStore()._db_path == str(tmp_path / "agent_voiceprints.sqlite3")

    share = ShareStore()
    assert share._db_path == str(tmp_path / "agent_share.sqlite3")
    db = await share._get_db()
    assert db is not None
    await share.close()


async def test_skill_index_default_db_file(tmp_path):
    from agent.skills.skill_index import SkillIndex

    index = SkillIndex(store=object(), embedder=None)
    assert str(index._db_file()) == str(tmp_path / "agent_skill_vectors.sqlite3")
