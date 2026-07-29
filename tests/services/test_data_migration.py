"""数据目录迁移服务单元测试 — 目标校验 / 在线迁移全流程 / 单飞行与 env 拦截。"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from core.config import ConfigManager
from services import data_migration as dm
from services.data_migration import MigrationError


@pytest.fixture()
def src_dir(tmp_path):
    """构造源数据目录：SQLite（WAL）+ 普通文件 + 应跳过的 wal/shm 侧文件。"""
    src = tmp_path / "src"
    (src / "data").mkdir(parents=True)
    db = src / "data" / "agent.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"v{i}",) for i in range(50)])
    conn.commit()
    conn.close()
    (src / "notes.md").write_text("# 便签\nhello", encoding="utf-8")
    (src / "data" / "agent.sqlite3-wal").write_bytes(b"")
    (src / "data" / "agent.sqlite3-shm").write_bytes(b"")
    return src


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch, src_dir):
    """隔离数据目录与 ConfigManager（防止写入真实 app_config.json）。"""
    monkeypatch.delenv("ANELF_DATA_DIR", raising=False)
    monkeypatch.setattr(dm, "resolved_data_dir", lambda: src_dir.resolve())
    monkeypatch.setattr(ConfigManager, "_config_file", str(tmp_path / "app_config.json"))
    monkeypatch.setattr(ConfigManager, "_initialized", False)
    ConfigManager.clear()
    dm._reset_state()
    yield
    dm._reset_state()


class TestCheckTarget:
    def test_empty(self, tmp_path):
        assert "empty" in dm.check_target("")["problems"]

    def test_not_absolute(self):
        assert "not_absolute" in dm.check_target("relative/path")["problems"]

    def test_same_as_current(self, src_dir):
        assert "same_as_current" in dm.check_target(str(src_dir))["problems"]

    def test_inside_current(self, src_dir):
        assert "inside_current" in dm.check_target(str(src_dir / "sub"))["problems"]

    def test_contains_current(self, src_dir):
        assert "contains_current" in dm.check_target(str(src_dir.parent))["problems"]

    def test_ok_with_size(self, tmp_path, src_dir):
        result = dm.check_target(str(tmp_path / "dst"))
        assert result["ok"], result
        assert result["required_bytes"] > 0

    def test_not_empty_warning(self, tmp_path, src_dir):
        dst = tmp_path / "dst"
        dst.mkdir()
        (dst / "existing.txt").write_text("x")
        result = dm.check_target(str(dst))
        assert result["ok"]
        assert "not_empty" in result["warnings"]


class TestGetLocation:
    def test_location_overview(self, src_dir):
        loc = dm.get_location()
        assert loc["exists"] is True
        assert loc["total_bytes"] > 0
        names = {e["name"] for e in loc["entries"]}
        assert "data" in names and "notes.md" in names
        # wal/shm 侧文件不计入
        assert not any("wal" in e["name"] or "shm" in e["name"] for e in loc["entries"])
        assert loc["source"] == "default"


class TestMigration:
    async def test_full_migration(self, tmp_path, src_dir):
        target = tmp_path / "dst"
        status = dm.start_migration(str(target))
        assert status["state"] == "running"

        for _ in range(200):
            await asyncio.sleep(0.05)
            if dm.migration_status()["state"] in ("done", "error"):
                break
        status = dm.migration_status()
        assert status["state"] == "done", status

        # 普通文件完整拷贝
        assert (target / "notes.md").read_text(encoding="utf-8") == "# 便签\nhello"
        # wal/shm 侧文件跳过
        assert not (target / "data" / "agent.sqlite3-wal").exists()
        assert not (target / "data" / "agent.sqlite3-shm").exists()
        # SQLite 热备份内容一致
        conn = sqlite3.connect(str(target / "data" / "agent.sqlite3"))
        try:
            assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 50
        finally:
            conn.close()
        # data_root 已写入配置（隔离的临时配置文件）
        assert ConfigManager.get("data_root") == str(target.resolve())

    async def test_single_flight(self, tmp_path):
        dm._running = True
        try:
            with pytest.raises(MigrationError) as e:
                dm.start_migration(str(tmp_path / "dst"))
            assert e.value.status_code == 409
        finally:
            dm._running = False

    def test_env_override_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANELF_DATA_DIR", "/somewhere/else")
        with pytest.raises(MigrationError) as e:
            dm.start_migration(str(tmp_path / "dst"))
        assert e.value.status_code == 409

    def test_invalid_target_rejected(self, src_dir):
        with pytest.raises(MigrationError) as e:
            dm.start_migration(str(src_dir))
        assert e.value.status_code == 400
