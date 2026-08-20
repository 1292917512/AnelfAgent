"""存储卷管理操作单元测试 — 备份/恢复往返 / 便签树覆盖 / 迁移指派 / 目标校验 / 表过滤。"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import aiosqlite
import pytest

import core.storage_volume as sv
from agent.storage import volume_restore
from core.storage_volume import VolumeDescriptor, VolumeKind, VolumeRegistry
from services import volume_ops as ops
from services.db_connections import EXPORT_MANIFEST_TABLE, SqlTransferClient, sqlite_affinity
from services.volume_ops import VolumeOperationError


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
        if state["state"] in ("done", "error", "idle"):
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

        result = ops.restore_backup("testvol", backups[0]["backup_id"])
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
        volume_restore.consume_pending_restores()

        assert (root / "memory.md").read_text(encoding="utf-8") == "v1"
        assert (root / "events" / "2026-08-20.md").read_text(encoding="utf-8") == "evt1"
        # 覆盖仅限备份涵盖条目：无关目录原样保留，备份未涵盖的新文件保留
        assert (root / "unrelated_dir" / "keep.txt").read_text(encoding="utf-8") == "keep"
        assert (root / "extra.md").exists()

    async def test_cognee_tree_whole_replace(self, sandbox):
        root = Path(sandbox.get("testnotes").default_path())
        sandbox.register(VolumeDescriptor(
            volume_id="testcognee", name="", description="",
            kind=VolumeKind.COGNEE_TREE, default_path=lambda: str(root),
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

        from services.database import VOLUME_MODULES, _database_registry

        for name in VOLUME_MODULES:
            module = importlib.import_module(name)
            register = getattr(module, "_register_volume", None)
            if register is not None:
                register()

        registry = _database_registry()
        for expected in ("agent", "memory", "stickers", "voiceprints", "skill_vectors", "share", "cognee"):
            assert expected in registry, f"缺少库: {expected}"
        assert registry["agent"]["path"].endswith("agent.sqlite3")
        assert "cognee_db" in registry["cognee"]["path"]
