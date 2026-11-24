"""用合成数据验证离线评测、在线备份及失败/取消后的资源回收。"""

import asyncio
import json
import sqlite3
from functools import partial
from tempfile import TemporaryDirectory

import pytest

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from scripts import eval_memory_recall as evaluation


@pytest.fixture
def snapshots(tmp_path, monkeypatch):
    directory = tmp_path / "snapshots"
    directory.mkdir()
    monkeypatch.setattr(evaluation, "TemporaryDirectory", partial(TemporaryDirectory, dir=directory))
    return directory


async def test_bundled_corpus_and_metrics(snapshots, capsys):
    assert await evaluation.run(evaluation.DEFAULT_CASES, None, 5) == 0
    output = capsys.readouterr().out
    assert "Hit@5: 5/5" in output and "Empty queries: 2/2" in output
    assert "Recall@5 (macro, labeled targets): 100.0%" in output
    assert await evaluation.run(evaluation.DEFAULT_CASES, None, 1) == 1
    output = capsys.readouterr().out
    assert "Hit@1: 5/5" in output and "Recall@1 (macro, labeled targets): 80.0%" in output
    assert list(snapshots.iterdir()) == []


async def test_live_wal_snapshot_reads_commits_and_legacy_alternatives(tmp_path, snapshots):
    source = tmp_path / "source.db"
    db = MemoryStore(str(source))
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"query": "birchfolio", "expect": ["absent", "birchfolio"]},
                                 {"query": "uncommitted", "empty": True}]), encoding="utf-8")
    try:
        await db.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, content="birchfolio"))
        writer = sqlite3.connect(source)
        try:
            assert writer.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            writer.execute("UPDATE memories SET content='uncommitted'")
            assert await evaluation.run(cases, source, 3) == 0
            assert writer.in_transaction
            writer.rollback()
            assert writer.execute("SELECT content FROM memories").fetchall() == [("birchfolio",)]
        finally:
            writer.close()
    finally:
        await db.close()
    assert list(snapshots.iterdir()) == []


@pytest.mark.parametrize("fault", [RuntimeError, asyncio.CancelledError])
async def test_failure_and_cancellation_remove_open_snapshot(snapshots, monkeypatch, fault):
    async def fail(*args, **kwargs):
        raise fault()
    monkeypatch.setattr(MemoryStore, "search_unified", fail)
    with pytest.raises(fault):
        await evaluation.run(evaluation.DEFAULT_CASES, None, 5)
    assert list(snapshots.iterdir()) == []
