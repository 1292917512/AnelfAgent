"""索引发布的故障边界：真实文件、真实子进程及队列提交顺序。"""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import weakref
from contextlib import asynccontextmanager, closing, nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from agent.memory.cognee.checkpoints import IndexCheckpoints
from agent.memory.cognee.client import CogneeClient
from agent.memory.cognee.config import CogneeConfig
from agent.memory.cognee.diagnostics import WorkerDiagnostics
from agent.memory.cognee.manifest import DatasetStamp, DocumentStamp, IndexManifest, MutationPlan
from agent.memory.cognee.preflight import preserve_owned_document
from agent.memory.cognee.process import CogneeProcess, WorkerCrashed
from agent.memory.cognee.types import CogneeAvailability, CogneeIndexBusy
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


def test_version_growth_metrics_use_only_committed_files_and_backfill_without_rebuilding(tmp_path, monkeypatch):
    root = tmp_path / "index"
    table = root / "system/databases/user/test.lance.db/facts.lance"
    versions = table / "_versions"
    versions.mkdir(parents=True)
    (versions / "1.manifest").write_bytes(b"one")
    (versions / "2.manifest").write_bytes(b"two")
    (table / "data").mkdir()
    (table / "data/current.lance").write_bytes(b"rows")
    (root / "system/graph.lbug").write_bytes(b"excluded graph")
    checkpoints = IndexCheckpoints(root)
    checkpoints.load()
    assert checkpoints.state["lance_version_files"] == 2
    assert checkpoints.state["lance_bytes"] == 10
    committed = checkpoints.state["committed"]
    proof = (checkpoints.control / committed / "proof.json").read_bytes()
    # 已有恢复点的计量从校验后的清单补齐，不复制数据库或改写恢复点。
    for key in ("lance_version_files", "lance_bytes"):
        checkpoints.state.pop(key)
    checkpoints._save()
    before = (checkpoints.control / "state.json").read_bytes()
    adopted = IndexCheckpoints(root)
    with monkeypatch.context() as patch:
        patch.setattr(adopted, "_copy", Mock(side_effect=AssertionError("must not rebuild")))
        adopted.load()
    assert adopted.state["committed"] == committed
    assert adopted.state["lance_version_files"] == 2 and adopted.state["lance_bytes"] == 10
    assert (checkpoints.control / "state.json").read_bytes() == before
    assert (checkpoints.control / committed / "proof.json").read_bytes() == proof
    adopted.mark_open()
    (versions / "3.manifest").write_bytes(b"uncommitted")
    client = CogneeClient(CogneeConfig(data_root=str(root)))
    client._checkpoints = adopted
    assert client.publication_status["lance_version_files"] == 2
    restored = IndexCheckpoints(root)
    restored.load()
    assert restored.state["lance_version_files"] == 2 and restored.state["lance_bytes"] == 10
    assert not (versions / "3.manifest").exists()


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


@pytest.mark.parametrize("directory", [False, True])
def test_snapshot_rejects_nested_links_and_preserves_recovery_point(tmp_path, directory):
    root = tmp_path / "index"
    (root / "data/nested").mkdir(parents=True)
    (root / "data/nested/raw").write_bytes(b"safe")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"do not copy or remove")
    checkpoints = IndexCheckpoints(root)
    checkpoints.load()
    committed = checkpoints.state["committed"]
    link = root / "data/nested/link"
    try:
        link.symlink_to(outside if directory else outside / "secret", target_is_directory=directory)
    except OSError as exc:
        if sys.platform == "win32" and exc.winerror == 1314:
            pytest.skip("Windows symlink privilege unavailable")
        raise
    checkpoints.mark_open()
    with pytest.raises(RuntimeError, match="符号链接"):
        checkpoints.commit()
    assert checkpoints.state["committed"] == committed
    checkpoints.restore()
    assert not link.exists()
    assert (root / "data/nested/raw").read_bytes() == b"safe"
    assert (outside / "secret").read_bytes() == b"do not copy or remove"


def test_snapshot_proof_covers_nested_files_and_remains_isolated(tmp_path):
    for name, content in {"system/db/表.lance/_versions/1.manifest": b"version",
                          "system/db/表.lance/data/1.lance": b"rows", "data/empty": b""}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    checkpoints = IndexCheckpoints(tmp_path)
    checkpoints.load()
    snapshot = checkpoints.control / checkpoints.state["committed"]
    proof = json.loads((snapshot / "proof.json").read_text(encoding="utf-8"))
    assert proof["files"] == {"system/db/表.lance/_versions/1.manifest": 7,
                              "system/db/表.lance/data/1.lance": 4, "data/empty": 0}
    checkpoints.mark_open()
    (tmp_path / "system/db/表.lance/data/1.lance").write_bytes(b"new rows")
    assert (snapshot / "system/db/表.lance/data/1.lance").read_bytes() == b"rows"
    checkpoints.restore()
    assert (tmp_path / "system/db/表.lance/data/1.lance").read_bytes() == b"rows"


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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups are not available on Windows")
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

    process = AsyncMock()
    process.call.side_effect = invoke
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True)))
    monkeypatch.setattr(client, "_worker", AsyncMock(return_value=process))
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


async def test_external_reads_do_not_wait_for_or_abort_active_write(tmp_path, monkeypatch):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    checkpoints = IndexCheckpoints(tmp_path)
    checkpoints.load()
    client._checkpoints = checkpoints
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True)))
    process = AsyncMock()
    process.call.return_value = []
    monkeypatch.setattr(client, "_worker", AsyncMock(return_value=process))
    monkeypatch.setattr(client, "_inspect", AsyncMock(return_value=IndexManifest()))
    monkeypatch.setattr(client, "_stop", AsyncMock())
    abort = AsyncMock()
    monkeypatch.setattr(client, "_abort", abort)
    writing = asyncio.Event()

    async def external_read():
        await writing.wait()
        for read in (lambda: client.available_datasets(["global"]), lambda: client.recall("最新项目地址")):
            with pytest.raises(CogneeIndexBusy):
                await read()

    # 读取任务不能继承写事务所有者，也不能触发该事务的恢复。
    read = asyncio.create_task(external_read())
    try:
        async with client.write_transaction():
            await client.cognify()
            writing.set()
            await asyncio.wait_for(read, timeout=1)
            assert client._operation_lock.locked()
            abort.assert_not_awaited()
    finally:
        read.cancel()
        await asyncio.gather(read, return_exceptions=True)
    assert checkpoints.state["commits"] == 2
    process.call.assert_awaited_once()
    await client.recall("最新项目地址")
    assert process.call.await_count == 2


async def test_read_cancellation_returns_before_recovery_but_keeps_index_locked(tmp_path, monkeypatch):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True)))
    process = AsyncMock()
    entered, stopping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    async def stop(**kwargs):
        stopping.set()
        await release.wait()

    async def recover():
        client._phase = "idle"

    process.call.side_effect = blocked
    monkeypatch.setattr(client, "_worker", AsyncMock(return_value=process))
    monkeypatch.setattr(client, "_stop", stop)
    monkeypatch.setattr(client, "_recover", recover)
    read = asyncio.create_task(client.recall("以前的项目地址"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        read.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(read, timeout=1)
        await asyncio.wait_for(stopping.wait(), timeout=1)
        assert client._operation_lock.locked() and client._reads
        with pytest.raises(CogneeIndexBusy):
            await client.recall("另一个问题")
        assert not release.is_set()
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*client._reads, return_exceptions=True), timeout=1)
        await asyncio.gather(read, return_exceptions=True)
    assert not client._operation_lock.locked() and not client._reads


async def test_management_read_waits_for_safe_window_instead_of_reporting_configuration_failure(tmp_path, monkeypatch):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True)))
    process = AsyncMock()
    process.call.return_value = [{"id": "dataset", "name": "global"}]
    monkeypatch.setattr(client, "_worker", AsyncMock(return_value=process))
    await client._operation_lock.acquire()
    client._phase = "recovering"
    read = asyncio.create_task(client.list_datasets())
    try:
        await asyncio.sleep(0)
        assert not read.done()
        with pytest.raises(CogneeIndexBusy):
            await client.recall("项目事实")
        process.call.assert_not_awaited()
    finally:
        client._phase = "idle"
        client._operation_lock.release()
    assert await asyncio.wait_for(read, timeout=1) == [{"id": "dataset", "name": "global"}]


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


@pytest.mark.parametrize("maintenance,reclaimed", [(False, 0), (True, 0), (True, 8)])
async def test_only_actual_maintenance_changes_publish_a_recovery_point(tmp_path, monkeypatch, maintenance, reclaimed):
    garbage = tmp_path / "system/databases/test.lance.db/data/old.lance"
    garbage.parent.mkdir(parents=True)
    garbage.write_bytes(b"obsolete")
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    checkpoints = IndexCheckpoints(tmp_path)
    checkpoints.load()
    client._checkpoints = checkpoints
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True)))
    process = AsyncMock()
    async def compact(*args, **kwargs):
        if reclaimed:
            garbage.unlink()
        return {"errors": [], "executed_tasks": 0, "old_versions_removed": 0,
                "bytes_reclaimed": reclaimed, "next_rotation": 1}

    process.call.side_effect = compact
    monkeypatch.setattr(client, "_worker", AsyncMock(return_value=process))
    inspect = AsyncMock(return_value=IndexManifest())
    stop = AsyncMock()
    commit = Mock(wraps=checkpoints.commit)
    monkeypatch.setattr(client, "_inspect", inspect)
    monkeypatch.setattr(client, "_stop", stop)
    monkeypatch.setattr(checkpoints, "commit", commit)
    async with client.write_transaction():
        if maintenance:
            await client.compact_vector_storage(1, prune_only=True)
    assert inspect.await_count == (3 if reclaimed else 1)
    assert stop.await_count == (2 if reclaimed else 0)
    assert commit.call_count == (1 if reclaimed else 0)
    assert client.publication_status["lance_bytes"] == (0 if reclaimed else 8)
    if reclaimed:
        IndexCheckpoints(tmp_path).load()
        assert not garbage.exists()


async def test_reopen_failure_prevents_publication_and_latches_fault(tmp_path, monkeypatch):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    checkpoints = IndexCheckpoints(tmp_path)
    checkpoints.load()
    before = checkpoints.state["committed"]
    client._checkpoints = checkpoints
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True)))
    worker = AsyncMock()
    monkeypatch.setattr(client, "_worker", AsyncMock(side_effect=[worker, worker, worker, WorkerCrashed("native crash")]))
    monkeypatch.setattr(client, "_inspect", AsyncMock(return_value=IndexManifest()))
    monkeypatch.setattr(client, "_stop", AsyncMock())
    with pytest.raises(WorkerCrashed):
        async with client.write_transaction():
            checkpoints.mark_open()
            await client.cognify()
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
            await client.cognify()
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


@pytest.mark.parametrize("old_root", [
    "file:///E:/old%20project/config/memory/cognee/data",
    "file:///home/旧项目/config/memory/cognee/data",
    "file://server/share/cognee/data",
    r"D:\code\AnelfAgent\config\memory\cognee\data",
])
async def test_relocated_sources_preserve_identity_and_restore_from_unmodified_snapshot(tmp_path, monkeypatch, old_root):
    from types import SimpleNamespace

    from agent.memory.cognee import sdk_client
    from agent.memory.cognee.relocation import relocate_sources
    from agent.memory.cognee.sdk_client import _source_digest

    root = tmp_path / "moved cognee"
    owner = uuid4()
    relative = f"{owner}/text_中文.txt"
    raw = root / "data" / relative
    raw.parent.mkdir(parents=True)
    raw.write_bytes("完整原文\r\n第二行".encode())
    database = root / "system/databases/cognee_db"
    database.parent.mkdir(parents=True)
    old = f"{old_root}/{relative}"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE data (id TEXT PRIMARY KEY, owner_id TEXT, raw_data_location TEXT, "
                   "original_data_location TEXT, pipeline_status TEXT, external_metadata TEXT)")
        db.executemany("INSERT INTO data VALUES (?, ?, ?, ?, ?, ?)", [
            ("one", owner.hex, old, old, "COMPLETED", '{"id": 1}'),
            ("two", owner.hex, old, "file:///external/original.txt", "PENDING", '{"id": 2}'),
            ("remote", owner.hex, "s3://bucket/key", "https://example.org/source", "COMPLETED", "{}"),
        ])
    checkpoints = IndexCheckpoints(root)
    checkpoints.load()
    saved = checkpoints.control / checkpoints.state["committed"] / "system/databases/cognee_db"
    before = saved.read_bytes()
    checkpoints.mark_open()
    assert relocate_sources(root) == 2
    assert relocate_sources(root) == 0
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT * FROM data WHERE id='one'").fetchone() == (
            "one", owner.hex, raw.as_uri(), raw.as_uri(), "COMPLETED", '{"id": 1}',
        )
        assert db.execute("SELECT original_data_location FROM data WHERE id='two'").fetchone()[0] == (
            "file:///external/original.txt"
        )
        assert db.execute("SELECT raw_data_location FROM data WHERE id='remote'").fetchone()[0] == "s3://bucket/key"
    assert _source_digest(root / "data", raw.as_uri()) == hashlib.sha256(raw.read_bytes()).hexdigest()
    assert saved.read_bytes() == before
    checkpoints.restore()
    assert database.read_bytes() == before
    client = sdk_client.SDKClient(CogneeConfig(data_root=str(root)))
    monkeypatch.setattr(client, "_import_cognee", lambda: SimpleNamespace(config=Mock()))
    monkeypatch.setattr(sdk_client, "validate_embedding", lambda _root, payload: payload)
    await client.configure_resolved({}, {})
    assert client._configured
    assert relocate_sources(root) == 0
    checkpoints.commit()
    assert relocate_sources(root) == 0


@pytest.mark.parametrize("escape", [False, True])
def test_relocation_rolls_back_all_paths_when_target_is_missing_or_escapes(tmp_path, escape):
    from agent.memory.cognee.relocation import relocate_sources

    root = tmp_path / "index"
    owner = uuid4()
    current = root / "data" / str(owner)
    current.mkdir(parents=True)
    (current / "one.txt").write_text("one")
    previous = tmp_path / "old/data" / str(owner)
    previous.mkdir(parents=True)
    (previous / "two.txt").write_text("old copy must never be used")
    if escape:
        try:
            (current / "two.txt").symlink_to(previous / "two.txt")
        except OSError:
            pytest.skip("symlinks require permission")
    database = root / "system/databases/cognee_db"
    database.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE data (id TEXT PRIMARY KEY, owner_id TEXT, raw_data_location TEXT, "
                   "original_data_location TEXT)")
        db.executemany("INSERT INTO data VALUES (?, ?, ?, ?)", [
            (name, owner.hex, (previous / name).as_uri(), (previous / name).as_uri())
            for name in ("one.txt", "two.txt")
        ])
        before = db.execute("SELECT * FROM data ORDER BY id").fetchall()
    with pytest.raises((RuntimeError, FileNotFoundError), match="迁移"):
        relocate_sources(root)
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT * FROM data ORDER BY id").fetchall() == before
    assert (previous / "two.txt").is_file()


async def test_inspection_batches_raw_reads_but_rechecks_bytes_on_every_inspection(tmp_path, monkeypatch):
    from agent.memory.cognee import sdk_client

    data_root = tmp_path / "data"
    data_root.mkdir()
    raw = data_root / "shared.txt"
    raw.write_text("完整原文", encoding="utf-8")
    client = sdk_client.SDKClient(CogneeConfig(data_root=str(tmp_path)))
    monkeypatch.setattr(client, "list_datasets", AsyncMock(return_value=[
        {"id": "a", "name": "a"}, {"id": "b", "name": "b"},
    ]))
    monkeypatch.setattr(client, "list_data", AsyncMock(return_value=[
        {"id": str(i), "raw_data_location": raw.as_uri(), "label": str(i)} for i in range(20)
    ]))
    monkeypatch.setattr(client, "document_processed", lambda *args: False)
    original = sdk_client._source_digests
    reads = []

    def record(root, locations):
        reads.append(locations)
        return original(root, locations)

    monkeypatch.setattr(sdk_client, "_source_digests", record)
    before = IndexManifest.model_validate(await client.inspect_index([]))
    assert reads == [[raw.as_uri()]]
    assert sum(len(dataset.documents) for dataset in before.datasets.values()) == 40
    raw.write_text("截断", encoding="utf-8")
    after = IndexManifest.model_validate(await client.inspect_index([]))
    assert len(reads) == 2
    with pytest.raises(RuntimeError, match="实际原文"):
        MutationPlan(before).validate(after)
    raw.unlink()
    with pytest.raises(FileNotFoundError):
        await client.inspect_index([])


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


@pytest.fixture
def offline_index_config(tmp_path, monkeypatch) -> CogneeConfig:
    from agent.memory.cognee.config import CogneeChatModelConfig, CogneeEmbeddingModelConfig

    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: None)
    # 摄入与索引提交不需要模型；仅关闭供应商连通性探测，不替换 SDK 或持久化。
    monkeypatch.setenv("COGNEE_SKIP_CONNECTION_TEST", "true")
    return CogneeConfig(data_root=str(tmp_path / "index"), write_breaker_enabled=False,
        chat=CogneeChatModelConfig(source="custom", model="gpt-4o", api_key="offline-test",
                                  endpoint="http://127.0.0.1:1/v1"),
        embedding=CogneeEmbeddingModelConfig(source="custom", provider="openai", model="text-embedding-3-small",
                                             api_key="offline-test", endpoint="http://127.0.0.1:1/v1", dimensions=1536))


@pytest.mark.timeout(180)
async def test_real_ingestion_publishes_colliding_tags_without_changing_document_identity(offline_index_config):
    config = offline_index_config
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


@pytest.mark.timeout(240)
async def test_real_cleanup_continuations_preserve_tagged_versions_and_published_documents(offline_index_config, store, monkeypatch):
    import lancedb

    from agent.memory.cognee.coordinator import CogneeCoordinator

    config = offline_index_config
    config.compact_max_versions_per_run = 2
    config.compact_retention_days = 0
    config.compact_cleanup_budget_seconds = 180
    db_dir = Path(config.data_root) / "system/databases/offline/backlog.lance.db"
    connection = lancedb.connect(str(db_dir))
    table = connection.create_table("facts", [{"id": 0, "vector": [0.0, 1.0]}])
    for identity in range(1, 7):
        table.add([{"id": identity, "vector": [float(identity), 1.0]}])
    table.to_lance().tags.create("preserve", 2)
    del table, connection
    client = CogneeClient(config)
    coordinator = CogneeCoordinator(store, client, config)
    try:
        item = await client.make_data_item("完整保留的源文档", label="memory", dataset_name="anelf_global",
                                           external_metadata={"anelf_content_hash": "cleanup-test"})
        ingestion = await client.add([item], dataset_name="anelf_global", incremental_loading=True)
        before = client.publication_status["commits"]
        version_files = client.publication_status["lance_version_files"]
        assert version_files >= 7 and client.publication_status["lance_bytes"] > 0
        original = client.compact_vector_storage
        calls = 0

        async def fail_second_round(retention: float, **kwargs: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 2:
                async with client.write_transaction():
                    await original(retention, **kwargs)
                    raise RuntimeError("candidate cleanup failed before publication")
            return await original(retention, **kwargs)

        monkeypatch.setattr(client, "compact_vector_storage", fail_second_round)
        await coordinator.request_compact(prune_only=True)
        failed = await coordinator._maintenance_task
        assert failed["rounds"] == 1 and "error" in failed
        assert client.publication_status["commits"] == before + 1
        assert client.publication_status["lance_version_files"] == version_files - 1
        recovered = lancedb.connect(str(db_dir)).open_table("facts")
        assert {version["version"] for version in recovered.list_versions()} == {2, 3, 4, 5, 6, 7}
        del recovered
        monkeypatch.setattr(client, "compact_vector_storage", original)
        await coordinator.request_compact(prune_only=True)
        result = await coordinator._maintenance_task
        assert result["rounds"] > 1 and result["old_versions_removed"] == 4
        assert result["executed_tasks"] == 0 and not result["errors"]
        assert client.publication_status["commits"] == before + 5
        assert client.publication_status["lance_version_files"] == version_files - 5
        assert {row["id"] for row in await client.list_data(ingestion["dataset_id"])} == {item.data_id}
    finally:
        await coordinator.close()
    # 关闭后的恢复流程和重新打开都保留最新行、用户标记版本及已提交的清理结果。
    reopened = lancedb.connect(str(db_dir)).open_table("facts")
    assert reopened.count_rows() == 7
    assert {version["version"] for version in reopened.list_versions()} == {2, 7}
    assert reopened.to_lance().checkout_version("preserve").count_rows() == 2


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


@pytest.mark.parametrize("mode", ["original", "located", "batched"])
async def test_deletion_reads_reduce_real_graph_scans_without_changing_shared_ownership(tmp_path, monkeypatch, mode):
    import cognee.context_global_variables as context
    import cognee.infrastructure.databases.unified as unified_module
    from cognee.infrastructure.databases.graph.ladybug.adapter import LadybugAdapter
    from cognee.infrastructure.databases.provenance import EdgeIdentity, make_chunk_source_ref_key, make_source_ref_key
    from cognee.infrastructure.databases.provenance.markers import mark_graph_provenance_if_empty
    from cognee.infrastructure.databases.unified import provenance_delete_planner
    from cognee.infrastructure.databases.unified.capabilities import EngineCapability
    from cognee.infrastructure.databases.unified.unified_store_engine import UnifiedStoreEngine
    from cognee.infrastructure.engine import DataPoint
    from cognee.modules.graph.models.EdgeType import EdgeType

    from agent.memory.cognee.deletion import DeletionWindow, install

    class Entity(DataPoint):
        name: str
        metadata: dict = {"index_fields": ["name"]}

    class Vectors:
        def __init__(self):
            self.deleted: set[str] = set()
            self.fail_once = True

        async def has_collection(self, name):
            return True

        async def delete_data_points(self, name, ids):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("injected vector failure")
            self.deleted.update(map(str, ids))

    class ScanRows(dict):
        __slots__ = ("__weakref__",)

    @asynccontextmanager
    async def database_context(*args, **kwargs):
        yield

    graph = LadybugAdapter(str(tmp_path / "graph"), kuzu_buffer_pool_size=64 * 1024 * 1024, kuzu_num_threads=2)
    vector = Vectors()
    engine = UnifiedStoreEngine(graph_engine=graph, vector_engine=vector,
        capabilities=EngineCapability.GRAPH | EngineCapability.VECTOR)
    dataset, other_dataset = uuid4(), uuid4()
    count = 32 if os.getenv("COGNEE_DELETION_BENCH") == "1" else 8
    background = 5000 if os.getenv("COGNEE_DELETION_BENCH") == "1" else 400
    documents = [uuid4() for _ in range(count)]
    keep_key = make_source_ref_key(dataset, uuid4())
    cross_key = make_source_ref_key(other_dataset, uuid4())
    keys = [make_source_ref_key(dataset, doc) if i % 2 == 0
            else make_chunk_source_ref_key(dataset, doc, uuid4()) for i, doc in enumerate(documents)]
    nodes = [Entity(name=f"retired-{i}") for i in range(count)]
    texts = ["orphan relation", 'shared "关系" \\ text']
    types = [EdgeType(relationship_name=text, number_of_edges=1) for text in texts]
    shared, cross, late = Entity(name="shared"), Entity(name="cross-dataset"), Entity(name="live-owner")
    unrelated = [Entity(name=f"unrelated-{i}") for i in range(background)]
    install()
    optimized = mode != "original"
    if mode == "located":
        monkeypatch.setattr(provenance_delete_planner, "_cleanup_orphaned_edge_types",
                            provenance_delete_planner._cleanup_orphaned_edge_types.__wrapped__)
    monkeypatch.setattr(context, "set_database_global_context_variables", database_context)
    monkeypatch.setattr(unified_module, "get_unified_engine", AsyncMock(return_value=engine))
    try:
        assert await mark_graph_provenance_if_empty(graph)
        await graph.add_nodes([*nodes, shared, cross, late, *unrelated, *types])
        for node, key in zip(nodes, keys, strict=True):
            await graph.attach_node_source_refs([str(node.id)], [key], str(uuid4()))
        await graph.attach_node_source_refs([str(shared.id)], [keys[0], keep_key], str(uuid4()))
        await graph.attach_node_source_refs([str(cross.id)], [keys[0], cross_key], str(uuid4()))
        await graph.attach_node_source_refs([str(late.id)], keys[:2], str(uuid4()))
        await graph.attach_node_source_refs([str(node.id) for node in unrelated], [keep_key], str(uuid4()))
        edge = EdgeIdentity(str(shared.id), str(cross.id), "linked")
        await graph.add_edges([(edge.source_id, edge.target_id, edge.relationship_name, {"edge_text": "linked"})])
        await graph.attach_edge_source_refs([edge], [keys[0], keys[1], keep_key], str(uuid4()))
        for i, node in enumerate(nodes):
            for text in texts:
                retired = EdgeIdentity(str(node.id), str(shared.id), text)
                await graph.add_edges([(retired.source_id, retired.target_id, retired.relationship_name,
                                        {"edge_text": f"  {text}  "})])
                await graph.attach_edge_source_refs([retired], [keys[i]], str(uuid4()))
        await graph.add_edges([(str(shared.id), str(cross.id), "different predicate", {"edge_text": texts[1]})])
        query = AsyncMock(wraps=graph.query)
        monkeypatch.setattr(graph, "query", query)
        read_nodes, read_edges = graph.find_node_source_refs_by_dataset, graph.find_edge_source_refs_by_dataset
        snapshot = None

        async def node_rows(dataset_id):
            nonlocal snapshot
            rows = ScanRows(await read_nodes(dataset_id))
            snapshot = weakref.ref(rows)
            return rows

        async def edge_rows(dataset_id):
            assert snapshot is None or snapshot() is None, "Full node scan must be released before the edge scan"
            return await read_edges(dataset_id)

        node_scan = AsyncMock(wraps=node_rows)
        edge_scan = AsyncMock(wraps=edge_rows)
        monkeypatch.setattr(graph, "find_node_source_refs_by_dataset", node_scan)
        monkeypatch.setattr(graph, "find_edge_source_refs_by_dataset", edge_scan)
        reads = DeletionWindow()
        if mode == "located":
            monkeypatch.setattr(reads._checkpoints, "scope", nullcontext)
        started = time.monotonic()
        if optimized:
            await reads.prepare([(str(dataset), str(documents[0]))])
            assert not reads._documents and node_scan.await_count == edge_scan.await_count == 0
            await reads.prepare(json.loads(json.dumps([(str(dataset), str(doc)) for doc in documents])))
            assert len(reads._documents) == count  # 不保存无关文档的完整来源图。
        # 定位完成后增加保留者，证明删除决策重读实时归属，而非使用定位快照。
        await graph.attach_node_source_refs([str(late.id)], [keep_key], str(uuid4()))
        query.reset_mock()
        with reads.scope() if optimized else nullcontext():
            with pytest.raises(RuntimeError, match="vector failure"):
                await engine.delete_by_document(str(dataset), str(documents[0]))
            before_retry = await graph.get_node_delete_data([str(nodes[0].id)])
            assert before_retry[str(nodes[0].id)].source_ref_keys == [keys[0]]
            for doc in documents:
                await engine.delete_by_document(str(dataset), str(doc))
                if optimized:
                    await reads.completed(str(dataset), str(doc))
        elapsed = time.monotonic() - started
        assert node_scan.await_count == edge_scan.await_count == (1 if optimized else count + 1)
        remaining = await graph.get_node_delete_data([str(node.id) for node in [*nodes, shared, cross, late]])
        assert set(remaining) == {str(shared.id), str(cross.id), str(late.id)}
        assert remaining[str(shared.id)].source_ref_keys == remaining[str(late.id)].source_ref_keys == [keep_key]
        assert remaining[str(cross.id)].source_ref_keys == [cross_key]
        assert (await graph.get_edge_delete_data([edge]))[edge].source_ref_keys == [keep_key]
        assert {str(node.id) for node in nodes} <= vector.deleted
        assert not {str(shared.id), str(cross.id), str(late.id)} & vector.deleted
        assert not reads._documents
        assert set(await graph.get_node_delete_data([str(row.id) for row in types])) == {str(types[1].id)}
        assert str(types[0].id) in vector.deleted and str(types[1].id) not in vector.deleted
        checkpoints = sum(call.args[0].strip().upper().startswith("CHECKPOINT") for call in query.await_args_list)
        assert (checkpoints == 1 if mode == "batched" else checkpoints > count), checkpoints
        print(json.dumps({"mode": mode, "documents": count, "full_scans": node_scan.await_count + edge_scan.await_count,
                          "checkpoints": checkpoints, "seconds": round(elapsed, 4)}))
    finally:
        await graph.close()


@pytest.mark.parametrize("deferred", [False, True])
async def test_deletion_window_coalesces_checkpoints_before_native_capacity_is_exhausted(tmp_path, deferred):
    from cognee.infrastructure.databases.graph.ladybug.adapter import LadybugAdapter

    from agent.memory.cognee.deletion import DeletionWindow, install

    install()
    path = str(tmp_path / "graph")
    graph = LadybugAdapter(path, kuzu_buffer_pool_size=64 << 20, kuzu_max_db_size=128 << 20, kuzu_num_threads=2)
    window = DeletionWindow()
    # 不需要来源扫描：本例隔离检查点引起的原生映射区耗尽。
    await window.prepare([("dataset", "document")])
    completed = 0
    try:
        with window.scope() if deferred else nullcontext():
            try:
                for i in range(200):
                    await graph.query("MERGE (n:Node {id: 'counter'}) SET n.name=$name", {"name": str(i)})
                    await graph.checkpoint()
                    completed += 1
            except RuntimeError as exc:
                assert not deferred and "Maximum database size" in str(exc)
        if deferred:
            await window.completed("dataset", "document")
            assert completed == 200 and not window._checkpoints._graphs
        else:
            assert completed < 200
    finally:
        await graph.close()
    if deferred:
        reopened = LadybugAdapter(path, kuzu_buffer_pool_size=64 << 20, kuzu_max_db_size=128 << 20, kuzu_num_threads=2)
        try:
            assert [list(row) for row in await reopened.query("MATCH (n:Node {id: 'counter'}) RETURN n.name")] == [["199"]]
        finally:
            await reopened.close()


async def test_relation_type_cleanup_protects_normalized_shared_ids_and_empty_text_fallback():
    from cognee.modules.graph.models.EdgeType import EdgeType

    from agent.memory.cognee.deletion import _prune_edge_types

    assert EdgeType.id_for("Case relation") == EdgeType.id_for("CASE RELATION")
    graph, vector = AsyncMock(), AsyncMock()
    graph.query.return_value = [("another predicate", json.dumps("CASE RELATION")), ("Fallback", None)]
    vector.has_collection.return_value = True
    await _prune_edge_types(graph, vector, {"Case relation", "fallback", "orphan"})
    expected = [str(EdgeType.id_for("orphan"))]
    graph.delete_nodes.assert_awaited_once_with(expected)
    vector.delete_data_points.assert_awaited_once_with("EdgeType_relationship_name", expected)


def test_native_imports_stay_inside_worker_adapters():
    root = Path(__file__).resolve().parents[4]
    allowed = {"adapters.py", "sdk_client.py", "sdk_progress.py", "ladybug_guard.py", "storage.py", "graph_validation.py",
               "deletion.py"}
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


@pytest.mark.parametrize("method", ["cognify", "improve"])
@pytest.mark.parametrize("flush_fails", [False, True])
async def test_sdk_pipeline_flushes_coalesced_native_writes_before_return(tmp_path, monkeypatch, method, flush_fails):
    from cognee.infrastructure.databases.graph.ladybug.adapter import LadybugAdapter

    from agent.memory.cognee.deletion import install
    from agent.memory.cognee.sdk_client import SDKClient

    install()
    graph = LadybugAdapter(str(tmp_path / "graph"), kuzu_buffer_pool_size=64 << 20,
                           kuzu_max_db_size=128 << 20, kuzu_num_threads=2)
    query = graph.query
    checkpoints = 0

    async def checked_query(statement, *args, **kwargs):
        nonlocal checkpoints
        if statement.startswith("CHECKPOINT"):
            checkpoints += 1
            if flush_fails:
                raise OSError("checkpoint flush failed")
        return await query(statement, *args, **kwargs)

    async def pipeline(*args, **kwargs):
        for i in range(200):
            await graph.query("MERGE (n:Node {id: 'counter'}) SET n.name=$name", {"name": str(i)})
            await graph.checkpoint()
        assert checkpoints == 0
        return {"stages": []}

    client = SDKClient(CogneeConfig(data_root=str(tmp_path)))
    monkeypatch.setattr(client, "_call", pipeline)
    monkeypatch.setattr(graph, "query", checked_query)
    try:
        if flush_fails:
            with pytest.raises(OSError, match="checkpoint flush failed"):
                await getattr(client, method)()
        else:
            assert await getattr(client, method)() == {"stages": []}
        assert checkpoints == 1
    finally:
        await graph.close()


@pytest.mark.parametrize("processed,chunks", [(True, "full"), (True, ""), (False, "full")])
async def test_transaction_graph_check_is_coalesced_without_weakening_publication(tmp_path, monkeypatch, processed, chunks):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    checkpoints = IndexCheckpoints(tmp_path)
    checkpoints.load()
    committed = checkpoints.state["committed"]
    client._checkpoints = checkpoints
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True)))
    process = AsyncMock()
    monkeypatch.setattr(client, "_worker", AsyncMock(return_value=process))
    monkeypatch.setattr(client, "_stop", AsyncMock())
    baseline = IndexManifest(datasets={"dataset": DatasetStamp(name="current", documents={
        "doc": DocumentStamp(identity="original", processed=False),
    })})
    candidate = baseline.model_copy(deep=True)
    candidate.datasets["dataset"].documents["doc"].processed = processed
    candidate.datasets["dataset"].documents["doc"].chunks = chunks
    inspect = AsyncMock(side_effect=[baseline, candidate, candidate])
    monkeypatch.setattr(client, "_inspect", inspect)

    async def publish():
        async with client.write_transaction():
            await client.cognify()
            await client.verify_graph_documents("dataset", ["doc"])
            assert [call.args[0] for call in process.call.await_args_list] == ["cognify"]

    if not processed or not chunks:
        with pytest.raises(RuntimeError, match="尚未完成抽取"):
            await publish()
        assert checkpoints.state["committed"] == committed
    else:
        await publish()
        assert checkpoints.state["committed"] != committed
        assert inspect.await_count == 3  # 基线、发布前、关闭重开后仍独立检查。
