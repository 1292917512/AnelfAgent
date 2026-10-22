"""索引发布的故障边界：真实文件、真实子进程及队列提交顺序。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.memory.cognee.checkpoints import IndexCheckpoints
from agent.memory.cognee.client import CogneeClient
from agent.memory.cognee.config import CogneeConfig
from agent.memory.cognee.process import CogneeProcess, WorkerCrashed
from agent.memory.cognee.types import CogneeAvailability


def test_restore_all_index_components_without_touching_source(tmp_path):
    root = tmp_path / "cognee"
    (root / "system").mkdir(parents=True)
    (root / "data").mkdir()
    for part in ("system/graph.lbug", "system/metadata.db", "data/raw.txt", "anelf-embedding.json"):
        (root / part).write_text("committed", encoding="utf-8")
    source = tmp_path / "memory.sqlite3"
    source.write_bytes(b"authoritative-before")
    checkpoints = IndexCheckpoints(root)
    checkpoints.load()
    checkpoints.mark_open()
    for part in ("system/graph.lbug", "system/metadata.db", "data/raw.txt", "anelf-embedding.json"):
        (root / part).write_text("partial", encoding="utf-8")
    (root / "system/graph.lbug.wal.checkpoint").write_bytes(b"broken recovery")
    source.write_bytes(b"authoritative-after")
    restarted = IndexCheckpoints(root)
    restarted.load()
    assert not restarted.state["dirty"]
    assert (root / "data/raw.txt").read_text() == "committed"
    assert (root / "system/metadata.db").read_text() == "committed"
    assert not (root / "system/graph.lbug.wal.checkpoint").exists()
    assert source.read_bytes() == b"authoritative-after"


def test_failed_snapshot_keeps_previous_commit_and_copy_budget(tmp_path, monkeypatch):
    root = tmp_path / "index"
    (root / "data").mkdir(parents=True)
    (root / "data/raw").write_bytes(b"old")
    monkeypatch.setattr("agent.memory.cognee.checkpoints._clone", lambda *_args: False)
    checkpoints = IndexCheckpoints(root, copy_limit_bytes=4)
    checkpoints.load()
    committed = checkpoints.state["committed"]
    checkpoints.mark_open()
    (root / "data/raw").write_bytes(b"new content exceeds copy budget")
    with pytest.raises(RuntimeError, match="预算"):
        checkpoints.commit()
    assert json.loads((checkpoints.control / "state.json").read_text())["committed"] == committed
    checkpoints.restore()
    assert (root / "data/raw").read_bytes() == b"old"


def test_interrupted_restore_is_repeatable(tmp_path, monkeypatch):
    root = tmp_path / "index"
    (root / "data").mkdir(parents=True)
    (root / "data/raw").write_text("safe")
    checkpoints = IndexCheckpoints(root)
    checkpoints.load()
    checkpoints.mark_open()
    (root / "data/raw").write_text("bad")
    replace = os.replace

    def fail(source, destination):
        if Path(destination) == root / "data":
            raise OSError("power loss between components")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail)
        with pytest.raises(OSError, match="power loss"):
            checkpoints.restore()
    restarted = IndexCheckpoints(root)
    restarted.load()
    assert (root / "data/raw").read_text() == "safe"


@pytest.mark.timeout(60)
def test_native_process_crash_restores_committed_graph(tmp_path):
    """真实 Ladybug 文件在未正常 close 的进程退出后，从封存快照恢复。"""
    root = tmp_path / "index"
    (root / "system").mkdir(parents=True)
    path = root / "system/native.lbug"
    bootstrap = (
        "import sys,os,cognee_db_workers; from ladybug import Database,Connection; "
        "db=Database(sys.argv[1],buffer_pool_size=67108864,max_num_threads=2); c=Connection(db); "
    )

    def run(code):
        return subprocess.run([sys.executable, "-c", bootstrap + code, str(path)], capture_output=True, timeout=20)

    created = run("c.execute('CREATE NODE TABLE Fact(id STRING PRIMARY KEY)'); "
                  "c.execute(\"CREATE (:Fact {id:'committed'})\"); c.close(); db.close()")
    assert created.returncode == 0, created.stderr.decode(errors="replace")
    checkpoints = IndexCheckpoints(root)
    checkpoints.load()
    checkpoints.mark_open()
    interrupted = run("c.execute(\"CREATE (:Fact {id:'uncommitted-batch'})\"); os._exit(138)")
    assert interrupted.returncode == 138
    IndexCheckpoints(root).load()
    checked = run("print(c.execute('MATCH (n:Fact) RETURN n.id').get_as_df().to_json()); c.close(); db.close()")
    assert checked.returncode == 0, checked.stderr.decode(errors="replace")
    assert b"committed" in checked.stdout and b"uncommitted-batch" not in checked.stdout


async def test_rpc_detects_real_process_exit_without_terminating_host():
    rpc = CogneeProcess()
    rpc.process = await asyncio.create_subprocess_exec(sys.executable, "-c",
        "import sys,os; sys.stdin.buffer.readline(); os._exit(138)",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    rpc._reader = asyncio.create_task(rpc._read())
    try:
        with pytest.raises(WorkerCrashed, match="138"):
            await rpc.call("list_datasets", timeout=10)
    finally:
        await rpc.stop()


async def test_cancelled_transaction_does_not_publish(tmp_path, monkeypatch):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    checkpoints = IndexCheckpoints(tmp_path)
    checkpoints.load()
    before = checkpoints.state["committed"]
    client._checkpoints = checkpoints
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True)))
    worker = AsyncMock()
    monkeypatch.setattr(client, "_worker", worker)
    monkeypatch.setattr(client, "_stop", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        async with client.write_transaction():
            checkpoints.mark_open()
            (tmp_path / "data").mkdir()
            (tmp_path / "data/unfinished").write_text("candidate")
            raise asyncio.CancelledError
    assert checkpoints.state["committed"] == before
    assert not (tmp_path / "data").exists()


async def test_reopen_failure_prevents_publication_and_latches_fault(tmp_path, monkeypatch):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    checkpoints = IndexCheckpoints(tmp_path)
    checkpoints.load()
    before = checkpoints.state["committed"]
    client._checkpoints = checkpoints
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True)))
    worker = AsyncMock()
    monkeypatch.setattr(client, "_worker", AsyncMock(side_effect=[worker, worker, WorkerCrashed("native crash")]))
    monkeypatch.setattr(client, "_stop", AsyncMock())
    with pytest.raises(WorkerCrashed):
        async with client.write_transaction():
            checkpoints.mark_open()
            client._verifications.append(("dataset", ["document"]))
    assert checkpoints.state["committed"] == before
    assert client.publication_status["error"]
    client.reset_fault()
    assert not client.publication_status["error"]


async def test_missing_previously_synced_document_rejects_new_batch(tmp_path, monkeypatch):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    checkpoints = IndexCheckpoints(tmp_path)
    checkpoints.load()
    previous = checkpoints.state["committed"]
    client._checkpoints = checkpoints
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True)))

    async def call(method, *args, **kwargs):
        if method == "list_datasets":
            return [{"id": "dataset"}]
        if method == "list_data":
            return [{"id": "older", "pipeline_status": {"cognify_pipeline": {
                "dataset": "DATA_ITEM_PROCESSING_COMPLETED"}}}]
        if method == "verify_graph_documents" and "older" in args[1]:
            raise RuntimeError("previously synced document missing")
        return None

    worker = AsyncMock()
    worker.call.side_effect = call
    monkeypatch.setattr(client, "_worker", AsyncMock(return_value=worker))
    monkeypatch.setattr(client, "_stop", AsyncMock())
    with pytest.raises(RuntimeError, match="previously synced"):
        async with client.write_transaction():
            client._verifications.append(("dataset", ["new"]))
    assert checkpoints.state["committed"] == previous


async def test_failed_restore_blocks_reopening_until_restore_succeeds(tmp_path, monkeypatch):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    checkpoints = IndexCheckpoints(tmp_path)
    checkpoints.load()
    client._checkpoints = checkpoints
    original = checkpoints.restore
    attempts = 0

    def restore():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary disk error")
        original()

    monkeypatch.setattr(checkpoints, "restore", restore)
    process = AsyncMock()
    monkeypatch.setattr("agent.memory.cognee.client.CogneeProcess", lambda: process)
    with pytest.raises(OSError):
        await client._abort(RuntimeError("batch failed"))
    with pytest.raises(RuntimeError, match="恢复尚未完成"):
        await client._worker()
    process.start.assert_not_called()
    client.reset_fault()
    await client._worker()
    assert attempts == 2
    process.start.assert_awaited_once()
    await client.close()
