"""存储卷管理操作单元测试 — 备份/恢复往返 / 便签树覆盖 / 迁移指派 / 目标校验 / 表过滤。"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import tarfile
import threading
import time
from pathlib import Path

import aiosqlite
import pytest

import core.storage_volume as sv
from agent.memory.cognee.volume import backup_members, observe_size, run_snapshot
from agent.storage import volume_restore
from core.storage_volume import VolumeDescriptor, VolumeKind, VolumeRegistry
from services import volume_ops as ops
from services.db_connections import EXPORT_MANIFEST_TABLE, SqlTransferClient, sqlite_affinity
from services.volume_ops import VolumeOperationError


@pytest.mark.parametrize("dirty", [False, True])
async def test_cognee_archive_deduplicates_clean_snapshot_and_preserves_dirty_recovery(tmp_path, monkeypatch, dirty):
    from filelock import FileLock

    from agent.memory.cognee.checkpoints import IndexCheckpoints

    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_coordinator", lambda: None)
    root = tmp_path / "cognee"
    (root / "data").mkdir(parents=True)
    (root / "data/raw").write_text("committed", encoding="utf-8")
    checkpoints = IndexCheckpoints(root)
    checkpoints.load()
    if dirty:
        checkpoints.mark_open()
        (root / "data/raw").write_text("partial", encoding="utf-8")
    archive = tmp_path / "backup.tgz"
    with FileLock(str(root / ".publication/owner.lock")):
        await ops._archive_tree(VolumeDescriptor(
            volume_id="cognee", name="", description="", kind=VolumeKind.COGNEE_TREE,
            default_path=lambda: str(root), backup_members=backup_members,
            snapshot=run_snapshot, observe_size=observe_size,
        ), root, archive, {})
    restored = tmp_path / "restored"
    with tarfile.open(archive) as bundle:
        names = bundle.getnames()
        assert not any(name.endswith(("owner.lock", "worker.lock")) for name in names)
        assert any(".publication" in name for name in names) is dirty
        bundle.extractall(restored, filter="data")
    IndexCheckpoints(restored).load()
    assert (restored / "data/raw").read_text() == "committed"


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离环境：ANELF_DATA_DIR 指向 tmp + 全新卷注册表（假卷，不触碰真实存储模块）。"""
    monkeypatch.delenv("ANELF_DATA_DIR", raising=False)
    monkeypatch.setenv("ANELF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ANELF_BOT_SQLITE_PATH", raising=False)

    registry = VolumeRegistry(assignment_path=str(tmp_path / "storage_volumes.json"))
    registry.register(VolumeDescriptor(
        volume_id="testvol",
        name="测试 SQLite 卷",
        description="",
        kind=VolumeKind.SQLITE,
        default_path=lambda: str(tmp_path / "data" / "data" / "testvol.sqlite3"),
    ))
    registry.register(VolumeDescriptor(
        volume_id="testnotes",
        name="测试便签卷",
        description="",
        kind=VolumeKind.NOTES_TREE,
        default_path=lambda: str(tmp_path / "notes-root"),
    ))
    monkeypatch.setattr(sv, "_registry", registry)
    monkeypatch.setattr(ops, "_closing", False)
    monkeypatch.setattr(ops, "_op_states", {})
    return registry


def _make_sqlite(path, rows=(("original",),)):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", rows)
    conn.commit()
    conn.close()
    return path


def _read_sqlite(path, sql="SELECT v FROM t", params=()):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


async def _wait_op(volume_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = ops.operation_status(volume_id)
        if state["state"] in ("done", "error", "idle", "cancelled"):
            return state
        await asyncio.sleep(0.05)
    raise AssertionError(f"操作超时: {ops.operation_status(volume_id)}")


class TestSqliteBackupRestore:
    async def test_backup_restore_roundtrip(self, sandbox, tmp_path):
        db = _make_sqlite(sandbox.get("testvol").default_path())

        ops.create_backup("testvol")
        state = await _wait_op("testvol")
        assert state["state"] == "done", state.get("error")
        backups = ops.list_backups("testvol")
        assert len(backups) == 1 and backups[0]["table_count"] == 1

        # 篡改后恢复
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE t SET v = 'tampered'")
        conn.commit()
        conn.close()

        ops.restore_backup("testvol", backups[0]["backup_id"])
        result = await _wait_op("testvol")
        assert result["result"]["needs_restart"] is True
        assert "testvol" in (volume_restore.pending_summary() or [])

        volume_restore.consume_pending_restores()
        assert _read_sqlite(db) == [("original",)]
        assert list(db.parent.glob("testvol.sqlite3.pre-restore-*")), "安全副本应保留"
        assert volume_restore.pending_summary() is None

    async def test_restore_removes_stale_wal(self, sandbox):
        db = _make_sqlite(sandbox.get("testvol").default_path())
        ops.create_backup("testvol")
        await _wait_op("testvol")
        bid = ops.list_backups("testvol")[0]["backup_id"]

        # 旧库遗留 WAL/SHM 侧文件：交换时必须清除，否则旧 WAL 会回放进新库
        (db.parent / "testvol.sqlite3-wal").write_bytes(b"stale")
        (db.parent / "testvol.sqlite3-shm").write_bytes(b"stale")
        ops.restore_backup("testvol", bid)
        assert (await _wait_op("testvol"))["state"] == "done"
        volume_restore.consume_pending_restores()
        assert not (db.parent / "testvol.sqlite3-wal").exists()
        assert not (db.parent / "testvol.sqlite3-shm").exists()
        assert db.is_file()

    async def test_same_second_backups_unique(self, sandbox):
        _make_sqlite(sandbox.get("testvol").default_path())
        ops.create_backup("testvol")
        await _wait_op("testvol")
        ops.create_backup("testvol")
        await _wait_op("testvol")
        ids = [b["backup_id"] for b in ops.list_backups("testvol")]
        assert len(ids) == 2 and len(set(ids)) == 2

    async def test_delete_backup(self, sandbox):
        _make_sqlite(sandbox.get("testvol").default_path())
        ops.create_backup("testvol")
        await _wait_op("testvol")
        bid = ops.list_backups("testvol")[0]["backup_id"]
        ops.delete_backup("testvol", bid)
        assert ops.list_backups("testvol") == []

    async def test_retention_prune(self, sandbox):
        from core.config import ConfigManager

        ConfigManager.set("volume_backup_retention", 2)
        _make_sqlite(sandbox.get("testvol").default_path())
        for _ in range(3):
            ops.create_backup("testvol")
            await _wait_op("testvol")
        assert len(ops.list_backups("testvol")) == 2

    def test_unknown_volume_404(self, sandbox):
        with pytest.raises(VolumeOperationError) as exc:
            ops.create_backup("nope")
        assert exc.value.status_code == 404


class TestNotesTree:
    async def test_notes_size_counts_members_only(self, sandbox):
        """便签卷占用只统计卷成员（数据根其余内容如大文件不计入）。"""
        root = Path(sandbox.get("testnotes").default_path())
        root.mkdir(parents=True, exist_ok=True)
        (root / "memory.md").write_bytes(b"a" * 100)
        (root / "events").mkdir()
        (root / "events" / "d.md").write_bytes(b"b" * 50)
        (root / "huge_unrelated.bin").write_bytes(b"x" * 100_000)

        items = {v["volume_id"]: v for v in await ops.list_volumes()}
        assert items["testnotes"]["size_bytes"] == 150

    async def test_backup_selective_restore_overlay(self, sandbox):
        root = Path(sandbox.get("testnotes").default_path())
        root.mkdir(parents=True, exist_ok=True)
        (root / "memory.md").write_text("v1", encoding="utf-8")
        (root / "events").mkdir()
        (root / "events" / "2026-08-20.md").write_text("evt1", encoding="utf-8")
        (root / "unrelated_dir").mkdir()
        (root / "unrelated_dir" / "keep.txt").write_text("keep", encoding="utf-8")

        ops.create_backup("testnotes")
        state = await _wait_op("testnotes")
        assert state["state"] == "done", state.get("error")

        # 篡改便签（新增 + 修改 + 删除子目录内容）
        (root / "memory.md").write_text("v2", encoding="utf-8")
        (root / "extra.md").write_text("extra", encoding="utf-8")
        (root / "events" / "2026-08-20.md").unlink()

        bid = ops.list_backups("testnotes")[0]["backup_id"]
        ops.restore_backup("testnotes", bid)
        assert (await _wait_op("testnotes"))["state"] == "done"
        volume_restore.consume_pending_restores()

        assert (root / "memory.md").read_text(encoding="utf-8") == "v1"
        assert (root / "events" / "2026-08-20.md").read_text(encoding="utf-8") == "evt1"
        # 覆盖仅限备份涵盖条目：无关目录原样保留，备份未涵盖的新文件保留
        assert (root / "unrelated_dir" / "keep.txt").read_text(encoding="utf-8") == "keep"
        assert (root / "extra.md").exists()
        previous = list(root.parent.glob(f"{root.name}.pre-restore-*"))
        assert len(previous) == 1
        assert (previous[0] / "memory.md").read_text() == "v2"

    async def test_cognee_tree_whole_replace(self, sandbox):
        root = Path(sandbox.get("testnotes").default_path())
        sandbox.register(VolumeDescriptor(
            volume_id="testcognee", name="", description="",
            kind=VolumeKind.COGNEE_TREE, default_path=lambda: str(root),
            backup_members=backup_members, snapshot=run_snapshot, observe_size=observe_size,
        ))
        root.mkdir(parents=True, exist_ok=True)
        (root / "system").mkdir()
        (root / "system" / "db.bin").write_bytes(b"data")

        ops.create_backup("testcognee")
        await _wait_op("testcognee")
        bid = ops.list_backups("testcognee")[0]["backup_id"]

        # 整树替换语义：新增的无关文件也回滚（与便签树的选择性覆盖相反）
        (root / "junk.txt").write_text("junk", encoding="utf-8")
        ops.restore_backup("testcognee", bid)
        assert (await _wait_op("testcognee"))["state"] == "done"
        volume_restore.consume_pending_restores()
        assert (root / "system" / "db.bin").read_bytes() == b"data"
        assert not (root / "junk.txt").exists()


class TestRelocate:
    async def test_relocate_writes_assignment(self, sandbox, tmp_path):
        db = _make_sqlite(sandbox.get("testvol").default_path())
        target = tmp_path / "moved"
        sandbox.mark_active("testvol", str(db))

        await ops.relocate_volume("testvol", str(target))
        state = await _wait_op("testvol")
        assert state["state"] == "done", state.get("error")

        assert (target / "testvol.sqlite3").is_file()
        assert _read_sqlite(target / "testvol.sqlite3") == [("original",)]
        assert sandbox.resolve_path("testvol") == str(db)
        from agent.storage.migration import finalize_pending_migration
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO t(v) VALUES ('after-copy')")
        finalize_pending_migration(sandbox.write_location)
        assert _read_sqlite(target / "testvol.sqlite3") == [("original",), ("after-copy",)]
        assert sandbox.resolve_path("testvol") == str(target / "testvol.sqlite3")
        assert sandbox.needs_restart("testvol") is True
        # 源文件保留
        assert db.is_file()

    async def test_check_target_problems(self, sandbox, tmp_path):
        db = _make_sqlite(sandbox.get("testvol").default_path())
        base = db.parent

        assert "empty" in (await ops.check_relocate_target("testvol", ""))["problems"]
        assert "not_absolute" in (await ops.check_relocate_target("testvol", "relative"))["problems"]
        assert "same_as_current" in (await ops.check_relocate_target("testvol", str(base)))["problems"]
        assert "inside_current" in (await ops.check_relocate_target("testvol", str(base / "sub")))["problems"]

        occupied = tmp_path / "occupied"
        occupied.mkdir()
        (occupied / "testvol.sqlite3").write_bytes(b"x")
        assert "target_file_exists" in (await ops.check_relocate_target("testvol", str(occupied)))["problems"]

    async def test_notes_volume_not_relocatable(self, sandbox):
        with pytest.raises(VolumeOperationError):
            await ops.check_relocate_target("testnotes", "/tmp/anywhere")


class TestExportTableFilter:
    async def test_exportable_tables_skips_derived(self, tmp_path):
        db = tmp_path / "f.sqlite3"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE normal (id INTEGER PRIMARY KEY, v TEXT)")
        # 虚拟表自带 _fts_* 影子表，二者都不应参与传输
        conn.execute("CREATE VIRTUAL TABLE normal_fts USING fts5(v)")
        conn.commit()
        conn.close()

        aconn = await aiosqlite.connect(str(db))
        try:
            tables = await ops._exportable_tables(aconn)
        finally:
            await aconn.close()
        assert tables == ["normal"]

    def test_manifest_table_name_reserved(self):
        assert EXPORT_MANIFEST_TABLE == "_anelf_export"

    def test_sqlite_affinity(self):
        assert sqlite_affinity("INTEGER") == "INTEGER"
        assert sqlite_affinity("VARCHAR(50)") == "TEXT"
        assert sqlite_affinity("BLOB") == "BLOB"
        assert sqlite_affinity("FLOAT") == "REAL"
        assert sqlite_affinity("") == "BLOB"
        assert sqlite_affinity("DECIMAL(10,2)") == "NUMERIC"

    def test_reverse_type_mapping(self):
        assert SqlTransferClient.sqlite_type("BIGINT") == "INTEGER"
        assert SqlTransferClient.sqlite_type("BYTEA") == "BLOB"
        assert SqlTransferClient.sqlite_type("DOUBLE PRECISION") == "REAL"
        assert SqlTransferClient.sqlite_type("CHARACTER VARYING") == "TEXT"
        assert SqlTransferClient.sqlite_type("NUMERIC") == "NUMERIC"


class TestRegistryParity:
    def test_database_registry_volume_driven(self):
        """Web 库注册表由卷驱动：原 6 库齐全 + share 补登。

        显式重放各存储模块的卷登记（幂等替换），避免依赖进程内
        模块导入顺序——其他测试可能已把登记落进了被替换的临时注册表。
        """
        import importlib

        from agent.runtime.volumes import VOLUME_MODULES
        from services.database_catalog import database_registry

        for name in VOLUME_MODULES:
            module = importlib.import_module(name)
            register = getattr(module, "_register_volume", None)
            if register is not None:
                register()

        registry = database_registry()
        for expected in ("agent", "memory", "stickers", "audio", "skill_vectors", "share", "cognee", "cognee_graph", "cognee_vector"):
            assert expected in registry, f"缺少库: {expected}"
        assert registry["agent"].path.endswith("agent.sqlite3")
        assert "cognee_db" in registry["cognee"].path


async def test_cancel_inside_large_file_cleans_partial_and_keeps_source(sandbox, monkeypatch):
    root = Path(sandbox.get("testnotes").default_path())
    root.mkdir()
    source = root / "memory.md"
    source.write_bytes(b"memory" * 200000)
    entered, release = threading.Event(), threading.Event()
    read = ops.CancellableReader.read

    def blocked_read(reader, size=-1):
        entered.set()
        assert release.wait(5)
        return read(reader, size)

    monkeypatch.setattr(ops.CancellableReader, "read", blocked_read)
    ops.create_backup("testnotes")
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert ops.list_backups("testnotes") == []
        response = ops.cancel_operation("testnotes")
        assert response["state"] == "running" and response["cancel_requested"]
        with pytest.raises(VolumeOperationError):
            ops.create_backup("testnotes")
    finally:
        release.set()
    assert (await _wait_op("testnotes"))["state"] == "cancelled"
    assert list(ops._volume_backup_dir("testnotes").iterdir()) == []
    assert source.read_bytes() == b"memory" * 200000
    monkeypatch.setattr(ops.CancellableReader, "read", read)
    ops.create_backup("testnotes")
    assert (await _wait_op("testnotes"))["state"] == "done"


async def test_cancel_waiting_for_cognee_does_not_wait_for_batch(sandbox, monkeypatch):
    from agent.memory.cognee.coordinator import CogneeCoordinator
    from services.volume_artifacts import OperationCancelled, check_cancel

    coordinator = object.__new__(CogneeCoordinator)
    coordinator._stopping = False
    coordinator._task = asyncio.create_task(asyncio.sleep(30))
    coordinator._idle_job = None
    coordinator._idle_done = asyncio.Event()
    coordinator._wake = asyncio.Event()
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_coordinator", lambda: coordinator)
    state = {"cancel_requested": False}
    job = asyncio.sleep(0)
    waiter = asyncio.create_task(run_snapshot(lambda: job, lambda: check_cancel(state)))
    await asyncio.sleep(0)
    state["cancel_requested"] = True
    try:
        with pytest.raises(OperationCancelled):
            await asyncio.wait_for(waiter, 1)
        assert coordinator._idle_job is None
        assert job.cr_frame is None
    finally:
        coordinator._task.cancel()
        await asyncio.gather(coordinator._task, return_exceptions=True)


async def test_volume_owner_controls_snapshot_members_and_scan_window(sandbox, tmp_path, monkeypatch):
    from dataclasses import replace

    root = tmp_path / "source"
    root.mkdir()
    (root / "old").write_text("old")
    entered = False

    async def snapshot(job, check=None):
        nonlocal entered
        assert not entered
        entered = True
        # 模拟等待静默窗口期间发生发布；扫描必须在窗口内看见最新文件。
        (root / "old").unlink(missing_ok=True)
        (root / "new").write_text("published")
        try:
            if check:
                check()
            return await job(), "owner_snapshot"
        finally:
            entered = False

    def members(path):
        assert entered
        return [path / "new"]

    descriptor = VolumeDescriptor(
        volume_id="managed_tree", name="", description="", kind=VolumeKind.COGNEE_TREE,
        default_path=lambda: str(root), snapshot=snapshot, backup_members=members,
    )
    sandbox.register(descriptor)
    ops.create_backup("managed_tree")
    assert (await _wait_op("managed_tree"))["state"] == "done"
    backup = ops.get_backup("managed_tree", ops.list_backups("managed_tree")[0]["backup_id"])
    assert backup["consistency"] == "owner_snapshot"
    with tarfile.open(backup["artifact_path"]) as bundle:
        assert bundle.getnames() == ["new"]

    walk = ops.walk_files

    def scan(path):
        assert entered
        return walk(path)

    monkeypatch.setattr(ops, "walk_files", scan)
    # 目标检查使用所有者统计，迁移文件枚举才使用通用遍历。
    async def observed(_path):
        return 9

    sandbox.register(replace(descriptor, observe_size=observed))
    await ops.relocate_volume("managed_tree", str(tmp_path / "moved"))
    assert (await _wait_op("managed_tree"))["state"] == "done"
    assert (tmp_path / "moved/source/new").read_text() == "published"
    assert not (tmp_path / "moved/source/old").exists()


async def test_failed_backup_never_prunes_good_copy(sandbox, monkeypatch):
    _make_sqlite(sandbox.get("testvol").default_path())
    ops.create_backup("testvol")
    assert (await _wait_op("testvol"))["state"] == "done"
    before = ops.list_backups("testvol")

    def broken(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(ops, "digest", broken)
    ops.create_backup("testvol")
    assert (await _wait_op("testvol"))["state"] == "error"
    assert ops.list_backups("testvol") == before
    assert len(list(ops._volume_backup_dir("testvol").iterdir())) == 1


async def test_restore_rejects_corrupted_content_and_wrong_manifest(sandbox):
    db = _make_sqlite(sandbox.get("testvol").default_path())
    ops.create_backup("testvol")
    await _wait_op("testvol")
    bid = ops.list_backups("testvol")[0]["backup_id"]
    info = ops.get_backup("testvol", bid)
    artifact = Path(info["artifact_path"])
    raw = bytearray(artifact.read_bytes())
    raw[-1] ^= 1
    artifact.write_bytes(raw)
    ops.restore_backup("testvol", bid)
    with pytest.raises(VolumeOperationError):
        ops.delete_backup("testvol", bid)
    assert (await _wait_op("testvol"))["state"] == "error"
    assert volume_restore.pending_summary() is None
    assert not list(ops._staging_dir().iterdir())
    assert _read_sqlite(db) == [("original",)]
    info["artifact"] = "../../outside.sqlite3"
    ops._write_manifest(artifact.parent, info)
    with pytest.raises(VolumeOperationError):
        ops.get_backup("testvol", bid)
    assert ops.list_backups("testvol") == []


@pytest.mark.parametrize("name", ["../outside.md", "data/agent.sqlite3", "cognee/system/db"])
def test_notes_restore_rejects_out_of_scope_members(sandbox, tmp_path, name):
    archive = tmp_path / "unsafe.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        entry = tarfile.TarInfo(name)
        entry.size = 4
        tar.addfile(entry, io.BytesIO(b"oops"))
    with pytest.raises(ValueError):
        volume_restore._swap_tree(archive, tmp_path / "target", whole_replace=False)
    assert not (tmp_path / "target").exists()


def test_missing_restore_artifact_never_counts_as_success(sandbox, tmp_path):
    db = _make_sqlite(sandbox.get("testvol").default_path())
    volume_restore.stage_restore_entry("testvol", VolumeKind.SQLITE, str(tmp_path / "missing"))
    result = volume_restore.consume_pending_restores()
    assert result[0]["ok"] is False
    assert volume_restore.pending_summary() == ["testvol"]
    assert _read_sqlite(db) == [("original",)]


async def test_restore_rechecks_staged_checksum_before_touching_source(sandbox):
    db = _make_sqlite(sandbox.get("testvol").default_path())
    ops.create_backup("testvol")
    await _wait_op("testvol")
    ops.restore_backup("testvol", ops.list_backups("testvol")[0]["backup_id"])
    state = await _wait_op("testvol")
    Path(state["result"]["staged"]).write_bytes(b"broken")
    assert volume_restore.consume_pending_restores()[0]["ok"] is False
    assert _read_sqlite(db) == [("original",)]


async def test_export_includes_nonpositive_ids_and_without_rowid(tmp_path):
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.insert_sql = lambda *args: "insert"
    async with aiosqlite.connect(str(tmp_path / "export.sqlite3")) as conn:
        await conn.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)')
        await conn.executemany('INSERT INTO t VALUES (?, ?)', [(-1, "minus"), (0, "zero"), (1, "one")])
        assert await ops._export_table(conn, client, "t", "", True) == 3
        assert client.executemany_rows.call_args.args[1] == [(-1, "minus"), (0, "zero"), (1, "one")]
        await conn.execute('CREATE TABLE w (key TEXT PRIMARY KEY) WITHOUT ROWID')
        await conn.execute("INSERT INTO w VALUES ('key')")
        assert await ops._export_table(conn, client, "w", "", True) == 1


def test_malformed_restore_marker_is_not_silently_ignored(sandbox):
    marker = volume_restore.pending_marker_path()
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"entries": "broken"}))
    with pytest.raises(RuntimeError):
        volume_restore.consume_pending_restores()


async def test_sqlite_page_cancellation_and_shutdown_leave_no_backup(sandbox, monkeypatch):
    db = _make_sqlite(sandbox.get("testvol").default_path())
    real = ops.online_sqlite_backup

    async def cancel_at_page(src, dest, *, progress):
        def cancelling(status, remaining, total):
            ops._quiesce()
            progress(status, remaining, total)
        await real(src, dest, progress=cancelling)

    monkeypatch.setattr(ops, "online_sqlite_backup", cancel_at_page)
    ops.create_backup("testvol")
    assert (await _wait_op("testvol"))["state"] == "cancelled"
    await ops._shutdown()
    assert not ops.has_running_operations()
    assert list(ops._volume_backup_dir("testvol").iterdir()) == []
    assert _read_sqlite(db) == [("original",)]


async def test_cancel_after_commit_boundary_cannot_undo_backup(sandbox, monkeypatch):
    _make_sqlite(sandbox.get("testvol").default_path())
    write = ops._write_manifest

    def publish(path, payload):
        with pytest.raises(VolumeOperationError, match="提交阶段"):
            ops.cancel_operation("testvol")
        write(path, payload)

    monkeypatch.setattr(ops, "_write_manifest", publish)
    ops.create_backup("testvol")
    assert (await _wait_op("testvol"))["state"] == "done"
    assert len(ops.list_backups("testvol")) == 1


def test_sqlite_restore_failure_rolls_back_all_original_files(sandbox, tmp_path, monkeypatch):
    target = Path(sandbox.get("testvol").default_path())
    target.parent.mkdir(parents=True)
    target.write_bytes(b"damaged original")
    wal = Path(f"{target}-wal")
    wal.write_bytes(b"old committed WAL")
    staged = _make_sqlite(tmp_path / "staged.sqlite3")
    replace = volume_restore.os.replace

    def fail_candidate(src, dst):
        if Path(dst) == target:
            raise OSError("replace failure")
        return replace(src, dst)

    monkeypatch.setattr(volume_restore.os, "replace", fail_candidate)
    with pytest.raises(OSError):
        volume_restore._swap_sqlite(staged, target)
    assert target.read_bytes() == b"damaged original"
    assert wal.read_bytes() == b"old committed WAL"
    assert staged.exists()
    monkeypatch.setattr(volume_restore.os, "replace", replace)
    volume_restore._swap_sqlite(staged, target)
    assert _read_sqlite(target) == [("original",)]
    assert not wal.exists()


def test_notes_restore_failure_rolls_back_previously_installed_members(sandbox, tmp_path, monkeypatch):
    target = Path(sandbox.get("testnotes").default_path())
    target.mkdir()
    for name in ("a.md", "b.md"):
        (target / name).write_text("old")
    archive = tmp_path / "notes.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("a.md", "b.md"):
            entry = tarfile.TarInfo(name)
            entry.size = 3
            tar.addfile(entry, io.BytesIO(b"new"))
    rename = Path.rename

    def fail_second(path, destination):
        if ".restore-" in path.parent.name and path.name == "b.md":
            raise OSError("second rename failed")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(OSError):
        volume_restore._swap_tree(archive, target, whole_replace=False)
    assert (target / "a.md").read_text() == "old"
    assert (target / "b.md").read_text() == "old"
    assert archive.exists()


async def test_restore_replays_after_marker_write_failure(sandbox, monkeypatch):
    db = _make_sqlite(sandbox.get("testvol").default_path())
    ops.create_backup("testvol")
    await _wait_op("testvol")
    ops.restore_backup("testvol", ops.list_backups("testvol")[0]["backup_id"])
    state = await _wait_op("testvol")
    staged = Path(state["result"]["staged"])
    write = volume_restore._write_marker_atomic
    failed = False

    def fail_once(path, payload):
        nonlocal failed
        if not failed and not payload["entries"]:
            failed = True
            raise OSError("marker disk failure")
        write(path, payload)

    monkeypatch.setattr(volume_restore, "_write_marker_atomic", fail_once)
    assert volume_restore.consume_pending_restores()[0]["ok"] is False
    assert staged.exists() and volume_restore.pending_summary() == ["testvol"]
    assert volume_restore.consume_pending_restores()[0]["ok"] is True
    assert not staged.exists() and volume_restore.pending_summary() is None
    assert _read_sqlite(db) == [("original",)]
