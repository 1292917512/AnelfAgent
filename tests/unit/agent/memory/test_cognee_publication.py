"""索引发布的故障边界：真实文件、真实子进程及队列提交顺序。"""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
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
from agent.memory.cognee.diagnostics import WorkerDiagnostics
from agent.memory.cognee.manifest import DatasetStamp, DocumentStamp, IndexManifest, MutationPlan
from agent.memory.cognee.preflight import preserve_owned_document
from agent.memory.cognee.process import CogneeProcess, WorkerCrashed
from agent.memory.cognee.types import CogneeAvailability
from agent.memory.store.projection_state import ProjectionState


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


async def test_rpc_reports_exit_while_helper_retains_stderr(tmp_path):
    import psutil

    pidfile = tmp_path / "helper.pid"
    rpc = CogneeProcess()
    rpc.process = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-c",
        "import subprocess,sys,os; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], stdout=subprocess.DEVNULL); "
        "ready=Path(sys.argv[1]); pending=ready.with_suffix('.tmp'); "
        "pending.write_text(str(p.pid)); pending.replace(ready); sys.stdin.readline(); os._exit(138)", str(pidfile),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    rpc._reader = asyncio.create_task(rpc._read())
    rpc._stderr_reader = asyncio.create_task(rpc._read_stderr())
    try:
        with pytest.raises(WorkerCrashed, match="138"):
            await rpc.call("list_datasets", timeout=10)
        assert psutil.Process(int(pidfile.read_text())).is_running()
    finally:
        try:
            if pidfile.exists():
                try:
                    psutil.Process(int(pidfile.read_text())).kill()
                except psutil.NoSuchProcess:
                    pass
        finally:
            await rpc.stop()


@pytest.mark.parametrize("clean", [False, True])
async def test_stop_reaps_helper_holding_diagnostic_pipe(tmp_path, clean):
    import psutil

    pidfile = tmp_path / "helper.pid"
    script = (
        "import subprocess,sys,time,json,os; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], stdout=subprocess.DEVNULL); "
        "ready=Path(sys.argv[1]); pending=ready.with_suffix('.tmp'); "
        "pending.write_text(str(p.pid)); pending.replace(ready); "
        "m=json.loads(sys.stdin.readline()); "
        "print(json.dumps({'protocol':1,'id':m['id'],'result':None}),flush=True); os._exit(0)"
    )
    rpc = CogneeProcess()
    rpc.process = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-c", script, str(pidfile),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=sys.platform != "win32",
    )
    rpc._owns_group = sys.platform != "win32"
    process = rpc.process
    rpc._reader = asyncio.create_task(rpc._read())
    rpc._stderr_reader = asyncio.create_task(rpc._read_stderr())
    try:
        async with asyncio.timeout(10):
            while not pidfile.exists():
                await asyncio.sleep(0.02)
        helper = psutil.Process(int(pidfile.read_text()))
        await asyncio.wait_for(rpc.stop(clean=clean), timeout=15)
        assert rpc.process is None and process.returncode is not None
        assert not helper.is_running() or helper.status() == psutil.STATUS_ZOMBIE
        assert rpc._reader.done() and rpc._stderr_reader.done()
    finally:
        try:
            if pidfile.exists():
                try:
                    helper = psutil.Process(int(pidfile.read_text()))
                    for child in [*helper.children(recursive=True), helper]:
                        child.kill()
                except psutil.NoSuchProcess:
                    pass
        finally:
            await rpc.stop()


async def test_stop_tolerates_recycled_process_group(tmp_path, monkeypatch):
    import psutil

    pidfile = tmp_path / "helper.pid"
    script = (
        "import subprocess,sys,json,os; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], stdout=subprocess.DEVNULL); "
        "ready=Path(sys.argv[1]); pending=ready.with_suffix('.tmp'); "
        "pending.write_text(str(p.pid)); pending.replace(ready); "
        "m=json.loads(sys.stdin.readline()); "
        "print(json.dumps({'protocol':1,'id':m['id'],'result':None}),flush=True); os._exit(0)"
    )
    rpc = CogneeProcess()
    rpc.process = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-c", script, str(pidfile),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=sys.platform != "win32",
    )
    rpc._owns_group = sys.platform != "win32"
    rpc._reader = asyncio.create_task(rpc._read())
    rpc._stderr_reader = asyncio.create_task(rpc._read_stderr())

    def foreign_group(pgid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", foreign_group)
    try:
        async with asyncio.timeout(10):
            while not pidfile.exists():
                await asyncio.sleep(0.02)
        helper = psutil.Process(int(pidfile.read_text()))
        await asyncio.wait_for(rpc.stop(clean=True), timeout=15)
        assert rpc.process is None
        assert not helper.is_running() or helper.status() == psutil.STATUS_ZOMBIE
        assert rpc._reader.done() and rpc._stderr_reader.done()
    finally:
        monkeypatch.undo()
        try:
            if pidfile.exists():
                try:
                    helper = psutil.Process(int(pidfile.read_text()))
                    for child in [*helper.children(recursive=True), helper]:
                        child.kill()
                except psutil.NoSuchProcess:
                    pass
        finally:
            await rpc.stop()


async def test_cancelled_stop_keeps_handle_until_reaped(monkeypatch):
    rpc = CogneeProcess()
    rpc.process = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)")
    entered, release = asyncio.Event(), asyncio.Event()
    reap = rpc._reap

    async def delayed(process, children):
        entered.set()
        await release.wait()
        await reap(process, children)

    monkeypatch.setattr(rpc, "_reap", delayed)
    task = asyncio.create_task(rpc.stop())
    try:
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and rpc.process is not None
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert rpc.process is None


async def test_failed_worker_reap_keeps_client_ownership(tmp_path):
    from filelock import FileLock

    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    process = AsyncMock()
    process.stop.side_effect = WorkerCrashed("pipe did not close")
    client._process = process
    owner = FileLock(tmp_path / "owner.lock")
    owner.acquire()
    client._owner = owner
    try:
        with pytest.raises(WorkerCrashed):
            await client.close()
        assert client._process is process and owner.is_locked
        process.stop.side_effect = None
        process.process = None
        process.written_bytes = 0
        await client.close()
        assert client._process is None and not owner.is_locked
    finally:
        owner.release()


@pytest.mark.parametrize("fail", [False, True])
async def test_catalog_excludes_uncommitted_changes_and_tracks_successful_publication(tmp_path, monkeypatch, fail):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    checkpoints = IndexCheckpoints(tmp_path)
    checkpoints.load()
    client._checkpoints = checkpoints
    baseline = IndexManifest(datasets={"old": DatasetStamp(name="old")})
    candidate = IndexManifest(datasets={**baseline.datasets, "new": DatasetStamp(name="new")})
    rows = [{"id": "old", "name": "old"}]

    async def invoke(method, *_args, **_kwargs):
        return {"dataset_id": "new"} if method == "add" else rows

    monkeypatch.setattr(client, "_invoke", invoke)
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True)))
    monkeypatch.setattr(client, "_worker", AsyncMock())
    monkeypatch.setattr(client, "_inspect", AsyncMock(side_effect=[baseline, candidate, candidate]))
    monkeypatch.setattr(client, "_stop", AsyncMock())
    assert not client.dataset_catalog()["ready"]
    await client.list_datasets()
    before = client.dataset_catalog()
    try:
        async with client.write_transaction():
            await client.add([], dataset_name="new")
            rows.append({"id": "new", "name": "new"})
            assert len(await client.list_datasets()) == 2
            observed = client.dataset_catalog()
            assert observed["items"] == before["items"] and observed["busy"]
            if fail:
                raise RuntimeError("candidate rejected")
    except RuntimeError as exc:
        assert fail and str(exc) == "candidate rejected"
    after = client.dataset_catalog()
    assert after["items"] == (before["items"] if fail else [
        {**row, "document_count": 0, "processed_count": 0} for row in rows])
    assert not after["busy"]
    after["items"].clear()
    assert client.dataset_catalog()["items"]
    client.reconfigure(CogneeConfig(data_root=str(tmp_path / "another")))
    assert not client.dataset_catalog()["ready"] and not client.dataset_catalog()["items"]


def test_generation_inventory_uses_committed_counts_and_keeps_unknowns_unknown(tmp_path):
    from agent.memory.cognee.generations import generation_catalog

    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    state = ProjectionState(namespace="anelf_p2_current", previous_namespaces=["anelf", "anelf_p2_old"],
                            phase="building")
    manifest = IndexManifest(datasets={
        "base": DatasetStamp(name="anelf_global", documents={"a": DocumentStamp(identity="a", processed=True)}),
        "old": DatasetStamp(name="anelf_p2_old_global", documents={"b": DocumentStamp(identity="b")}),
        "current": DatasetStamp(name="anelf_p2_current_global", documents={"c": DocumentStamp(identity="c", processed=True)}),
        "outside": DatasetStamp(name="outside_global"),
    })
    client._remember_manifest(manifest)
    # SDK 的轻量目录刷新不丢失已经提交的数量，也不启动逐文档扫描。
    client._remember_catalog([{"id": identity, "name": row.name} for identity, row in manifest.datasets.items()])
    storage = {"total_bytes": 100, "data_bytes": 0, "graph_bytes": 40, "lance_bytes": 60,
               "metadata_bytes": 0, "other_bytes": 0, "computed_at": 1,
               "datasets": {"old": {"total_bytes": 100, "graph_bytes": 40, "lance_bytes": 60}}}
    groups = generation_catalog(client.dataset_catalog(), state, storage)
    assert [row["namespace"] for row in groups] == [state.namespace, "anelf_p2_old", "anelf"]
    assert [row["document_count"] for row in groups] == [1, 1, 1]
    assert [row["processed_count"] for row in groups] == [1, 0, 1]
    assert groups[1]["storage"] == storage["datasets"]["old"]
    assert groups[0]["storage"] is None and groups[1]["phase"] == "unknown"
    client._remember_catalog([*client.dataset_catalog()["items"], {"id": "new", "name": "anelf_p2_old_relations"}])
    unknown = generation_catalog(client.dataset_catalog(), state, storage)[1]
    assert unknown["document_count"] is None and unknown["storage"] is None
    assert generation_catalog(client.dataset_catalog(), None, storage) == []


async def test_dataset_tool_does_not_wait_for_active_index_write(tmp_path, monkeypatch):
    from agent.memory.tools import list_cognee_datasets

    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    client.projection_namespace = "anelf"
    monkeypatch.setattr(client, "_invoke", AsyncMock(return_value=[{"id": "current", "name": "anelf_global"}]))
    await client.list_datasets()
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_client", lambda: client)
    async with client._operation_lock:
        async with asyncio.timeout(5):
            result = json.loads(await list_cognee_datasets())
        assert result["catalog_ready"] and result["updating"]
        assert result["datasets"][0]["id"] == "current"


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
    monkeypatch.setattr(client, "_inspect", AsyncMock(return_value=IndexManifest()))
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
    monkeypatch.setattr(client, "_inspect", AsyncMock(return_value=IndexManifest()))
    monkeypatch.setattr(client, "_stop", AsyncMock())
    with pytest.raises(WorkerCrashed):
        async with client.write_transaction():
            checkpoints.mark_open()
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

    worker = AsyncMock()
    monkeypatch.setattr(client, "_worker", AsyncMock(return_value=worker))
    monkeypatch.setattr(client, "_stop", AsyncMock())
    before = IndexManifest(datasets={"dataset": DatasetStamp(name="main", documents={
        "older": DocumentStamp(identity="old", processed=True, chunks="old-chunks"),
    })})
    after = IndexManifest(datasets={"dataset": DatasetStamp(name="main")})
    monkeypatch.setattr(client, "_inspect", AsyncMock(side_effect=[before, after]))
    with pytest.raises(RuntimeError, match="未声明变化"):
        async with client.write_transaction():
            pass
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
    monkeypatch.setattr("agent.memory.cognee.client.CogneeProcess", lambda **kwargs: process)
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


def inventory() -> IndexManifest:
    return IndexManifest(datasets={
        "old": DatasetStamp(name="anelf_old_global", documents={"old-doc": DocumentStamp(identity="old")}),
        "current": DatasetStamp(name="anelf_p2_global", documents={"current-doc": DocumentStamp(
            identity="current", content_hash="source", raw_hash="raw", processed=True, chunks="both-chunks")}),
    })


async def test_published_p2_identity_survives_adapter_refactoring():
    from agent.memory.cognee.config import CogneeConfig
    from agent.memory.cognee.sdk_client import SDKClient
    from agent.memory.cognee.wire import decode, encode, make_document

    options = dict(label="anelf-memory-7", dataset_name="anelf_p2_fixture_global",
                   external_metadata={"anelf_projection_version": 2, "anelf_memory_id": "7"},
                   node_set=["project:alpha", "future:tag"])
    document = make_document("existing p2 memory", **options)
    assert document.data_id == "fd9c94ea-65d3-564e-a147-82751fa8ed74"
    assert decode(encode(document)) == document
    assert await SDKClient(CogneeConfig()).make_data_item("existing p2 memory", **options) == document


@pytest.mark.parametrize("damage", ["metadata", "document", "dataset", "chunk", "raw", "source", "status"])
def test_retirement_cannot_publish_damage_to_retained_dataset(damage):
    before = inventory()
    plan = MutationPlan(before)
    plan.retired.add("old")
    after = before.model_copy(deep=True)
    del after.datasets["old"]
    document = after.datasets["current"].documents["current-doc"]
    if damage == "metadata":
        document.identity = "changed-tags"
    elif damage == "document":
        after.datasets["current"].documents.clear()
    elif damage == "dataset":
        after.datasets.clear()
    elif damage == "chunk":
        document.chunks = "only-one-chunk"
    elif damage == "raw":
        document.raw_hash = "changed"
    elif damage == "source":
        document.source_digest = "truncated-source"
    else:
        document.processed = False
    with pytest.raises(RuntimeError):
        plan.validate(after)


def test_valid_retirement_preserves_current_and_authoritative_reference():
    before = inventory()
    plan = MutationPlan(before)
    plan.retired.add("old")
    after = before.model_copy(deep=True)
    del after.datasets["old"]
    plan.validate(after)
    mapping = {"entry_kind": "memory", "entry_id": 1, "dataset_id": "current",
               "data_id": "current-doc", "content_hash": "source"}
    after.check_sources([mapping])
    mapping["data_id"] = "missing-from-both-index-and-metadata"
    with pytest.raises(RuntimeError, match="原生映射"):
        after.check_sources([mapping])


def test_new_document_must_preserve_exact_input_bytes(tmp_path):
    from agent.memory.cognee.manifest import document_identity
    from agent.memory.cognee.sdk_client import _source_digest
    from agent.memory.cognee.wire import make_document

    item = make_document("原文\r\n第二行", label="memory", external_metadata={}, dataset_name="test")
    plan = MutationPlan(IndexManifest())
    plan.add("dataset", "test", [item])
    raw = tmp_path / "text.txt"
    raw.write_bytes(item.data.encode("utf-8"))
    stamp = DocumentStamp(identity=document_identity(item.label, {}, []),
                          source_digest=_source_digest(tmp_path, raw.as_uri()))
    candidate = IndexManifest(datasets={"dataset": DatasetStamp(name="test", documents={item.data_id: stamp})})
    plan.validate(candidate)
    raw.write_bytes(b"truncated")
    stamp.source_digest = _source_digest(tmp_path, raw.as_uri())
    with pytest.raises(RuntimeError, match="实际原文"):
        plan.validate(candidate)


@pytest.mark.parametrize("tags", [
    ["topic:WOL", "topic:WoL"],
    ["topic:two words", "topic:two_words"],
    ["topic:O'Brien", "topic:OBrien"],
    ["标签:İ", "标签:i\u0307", "标签:ß", "标签:ss"],
])
def test_manifest_accepts_sdk_nodeset_identity_but_preserves_exact_metadata(tags):
    from cognee.tasks.ingestion.ingest_data import _union_node_sets

    from agent.memory.cognee.manifest import document_identity
    from agent.memory.cognee.wire import make_document

    item = make_document("原文", label="memory", external_metadata={"anelf_attribution": {"tags": tags}},
                         node_set=tags)
    plan = MutationPlan(IndexManifest())
    plan.add("dataset", "test", [item])
    stored_tags = _union_node_sets(None, item.node_set)
    metadata = {**item.external_metadata, "node_set": stored_tags}
    stamp = DocumentStamp(identity=document_identity(item.label, metadata, stored_tags),
                          source_digest=hashlib.sha256(item.data.encode()).hexdigest())
    candidate = IndexManifest(datasets={"dataset": DatasetStamp(name="test", documents={item.data_id: stamp})})
    plan.validate(candidate)
    # 实际来源标签丢失与物理索引的合法去重不同，仍须拒绝发布。
    metadata["anelf_attribution"] = {"tags": tags[:1]}
    stamp.identity = document_identity(item.label, metadata, stored_tags)
    with pytest.raises(RuntimeError, match="内容或标签"):
        plan.validate(candidate)
    stamp.identity = document_identity(item.label, item.external_metadata, [])
    with pytest.raises(RuntimeError, match="内容或标签"):
        plan.validate(candidate)


@pytest.mark.timeout(180)
async def test_real_ingestion_publishes_colliding_tags_without_changing_document_identity(tmp_path, monkeypatch):
    from agent.memory.cognee.config import CogneeChatModelConfig, CogneeEmbeddingModelConfig

    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: None)
    # 摄入与索引提交不需要模型；仅关闭供应商连通性探测，不替换 SDK 或持久化。
    monkeypatch.setenv("COGNEE_SKIP_CONNECTION_TEST", "true")
    config = CogneeConfig(data_root=str(tmp_path / "index"), write_breaker_enabled=False,
        chat=CogneeChatModelConfig(source="custom", model="gpt-4o", api_key="offline-test",
                                  endpoint="http://127.0.0.1:1/v1"),
        embedding=CogneeEmbeddingModelConfig(source="custom", provider="openai", model="text-embedding-3-small",
                                             api_key="offline-test", endpoint="http://127.0.0.1:1/v1", dimensions=1536))
    client = CogneeClient(config)
    tags = ["topic:WOL", "topic:WoL", "topic:two words", "topic:two_words"]
    metadata = {"anelf_content_hash": "source-hash", "anelf_attribution": {"tags": tags}}
    item = await client.make_data_item("离线摄入：保留所有标签与原文。", label="memory", external_metadata=metadata,
                                       node_set=tags, dataset_name="anelf_global")
    try:
        result = await client.add([item], dataset_name="anelf_global", incremental_loading=True)
        rows = await client.list_data(result["dataset_id"])
        assert len(rows) == 1 and rows[0]["id"] == item.data_id
        assert rows[0]["external_metadata"]["anelf_attribution"]["tags"] == tags
        assert json.loads(rows[0]["node_set"]) == ["topic:WOL", "topic:two words"]
        # 使用同一个稳定 ID 重试，已提交文档及来源不能重复或缩减。
        await client.add([item], dataset_name="anelf_global", incremental_loading=True)
        assert len(await client.list_data(result["dataset_id"])) == 1
        assert client.publication_status["commits"] == 2
    finally:
        await client.close()


async def test_query_vector_is_reused_across_rpc_scopes_and_released():
    from agent.memory.cognee.adapters import _wrap_embed, close_query_cache, query_embeddings

    class Engine:
        def __init__(self):
            self.calls = 0

        async def embed_text(self, text):
            self.calls += 1
            return [[0.1, 0.2]]

    engine = Engine()
    _wrap_embed(engine)
    shared = {}
    async with query_embeddings(shared):
        first = await engine.embed_text(["same query"])
    async with query_embeddings(shared):
        second = await engine.embed_text(["same query"])
    assert first == second and engine.calls == 1
    await close_query_cache(shared)
    assert shared == {}


async def test_filtered_recall_expands_bounded_candidates():
    from agent.memory.cognee.fusion import RecallDiagnostics, _filtered_recall
    from agent.memory.cognee.types import CogneeRecallItem
    from agent.memory.tag_query import TagQuery

    def item(index, tag):
        return CogneeRecallItem(id=str(index), content="fact", metadata={"anelf_attribution": {
            "tags": [tag], "timestamp": 0, "memory_type": "semantic", "sensitivity": "normal"}})

    client = AsyncMock()
    client.recall.side_effect = [[item(1, "state:old"), item(2, "state:old")],
                                [item(1, "state:old"), item(2, "state:old"), item(3, "project:future")]]
    diagnostics = RecallDiagnostics()
    result = await _filtered_recall(client, "query", {"top_k": 2}, TagQuery(none_of=("state:old",)), 1, diagnostics)
    assert result[-1].id == "3"
    assert [call.kwargs["top_k"] for call in client.recall.call_args_list] == [2, 4]
    assert not diagnostics.tag_filter_limited


async def test_filtered_recall_marks_exhausted_budget_instead_of_claiming_no_knowledge():
    from agent.memory.cognee.fusion import RecallDiagnostics, _filtered_recall
    from agent.memory.cognee.types import CogneeRecallItem
    from agent.memory.tag_query import TagQuery

    async def recall(query, **kwargs):
        return [CogneeRecallItem(id=str(index), content="fact") for index in range(kwargs["top_k"])]

    client = AsyncMock()
    client.recall.side_effect = recall
    diagnostics = RecallDiagnostics(native="complete", cognee="complete")
    await _filtered_recall(client, "query", {"top_k": 2}, TagQuery(all_of=("project:rare/*",)), 1, diagnostics)
    assert [call.kwargs["top_k"] for call in client.recall.call_args_list] == [2, 4, 8]
    assert diagnostics.result()["status"] == "partial"


def test_old_checkpoint_adopts_proof_without_rewriting_source(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "old.txt").write_bytes(b"existing source")
    original = IndexCheckpoints(tmp_path)
    original.load()
    state = json.loads((original.control / "state.json").read_text())
    state.pop("proof_sha256")
    (original.control / "state.json").write_text(json.dumps(state))
    adopted = IndexCheckpoints(tmp_path)
    adopted.load()
    assert adopted.read_manifest() is None
    adopted.commit(inventory().model_dump(mode="json"))
    assert IndexManifest.model_validate(adopted.read_manifest()) == inventory()
    assert (tmp_path / "data" / "old.txt").read_bytes() == b"existing source"


def test_missing_snapshot_file_cannot_overwrite_working_copy(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "raw.txt").write_bytes(b"baseline")
    checkpoints = IndexCheckpoints(tmp_path)
    checkpoints.load()
    checkpoints.mark_open()
    (checkpoints.control / checkpoints.state["committed"] / "data" / "raw.txt").unlink()
    with pytest.raises(RuntimeError, match="文件缺失"):
        checkpoints.restore()
    assert (tmp_path / "data" / "raw.txt").read_bytes() == b"baseline"


async def test_p2_archives_after_projection_version_upgrade(tmp_path, monkeypatch):
    root = tmp_path / "cognee"
    (root / "data").mkdir(parents=True)
    raw = root / "data" / "p2.txt"
    raw.write_text("历史完整正文", encoding="utf-8")
    source = AsyncMock()
    source.legacy_source.return_value = {"content": "现在的正文"}
    item = {"id": "old", "label": "anelf-memory-1", "raw_data_location": raw.as_uri(),
            "external_metadata": {"anelf_projection_version": 2, "anelf_memory_id": "1"}}
    unchanged = copy.deepcopy(item)
    monkeypatch.setattr("agent.memory.cognee.preflight.PROJECTION_VERSION", 3)
    assert await preserve_owned_document(source, ProjectionState(root=str(root)), item, "dataset", "old",
                                         include_current_format=True)
    saved = source.preserve_legacy_document.call_args.kwargs["document"]
    assert saved["text"] == "历史完整正文" and item == unchanged
    item["external_metadata"]["anelf_projection_version"] = 99
    assert not await preserve_owned_document(source, ProjectionState(root=str(root)), item, "dataset", "old",
                                             include_current_format=True)


def test_worker_diagnostics_redacts_exact_credentials_and_rotates(tmp_path):
    secret = "test-provider-credential-without-known-prefix"
    path = tmp_path / "worker.log"
    log = WorkerDiagnostics(path, {secret})
    try:
        for _ in range(150):
            log.write("x" * 12000 + secret + " api_key=another-sensitive-value")
    finally:
        log.close()
    files = list(tmp_path.glob("worker.log*"))
    assert len(files) == 2
    assert all(file.stat().st_size < 1024**2 + 16384 for file in files)
    assert all(secret not in file.read_text() and "another-sensitive-value" not in file.read_text() for file in files)


def test_host_does_not_inherit_or_import_native_sdk():
    root = Path(__file__).resolve().parents[4]
    result = subprocess.run([sys.executable, "-c",
        "import sys; from agent.memory.cognee.client import CogneeClient; "
        "assert CogneeClient.__bases__ == (object,); "
        "assert not any(x in sys.modules for x in ('cognee','ladybug','lancedb')); print('isolated')"],
        cwd=root, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"isolated" in result.stdout


def test_native_imports_stay_inside_worker_adapters():
    root = Path(__file__).resolve().parents[4]
    allowed = {"adapters.py", "sdk_client.py", "ladybug_guard.py", "storage.py", "graph_validation.py"}
    violations = []
    for directory in ("agent", "services", "scripts"):
        for path in (root / directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                modules = ([node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0
                           else [name.name for name in node.names] if isinstance(node, ast.Import) else [])
                if any(name.split(".")[0] in {"cognee", "ladybug", "lancedb", "cognee_db_workers"} for name in modules):
                    if path.parent != root / "agent/memory/cognee" or path.name not in allowed:
                        violations.append(str(path.relative_to(root)))
    assert violations == []
