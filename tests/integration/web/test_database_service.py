"""数据库管理服务单元测试 — 白名单 / 只读 SQL / 序列化 / rowid CRUD / embedding 护栏。"""

from __future__ import annotations

import array
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from services.database import DatabaseError, DatabaseService


@pytest.fixture(autouse=True)
async def isolated_catalog(tmp_path, monkeypatch):
    """管理测试使用真实组件声明和隔离的卷、快照，绝不浏览用户库。"""
    import asyncio

    import core.storage_volume as volumes
    from agent.memory.cognee import databases, storage
    from services.db_connections import ConnectionStore

    registry = volumes.VolumeRegistry(str(tmp_path / "volumes.json"))
    monkeypatch.setattr(volumes, "_registry", registry)
    monkeypatch.setattr("services.database_catalog.ensure_volume_modules", lambda: None)
    monkeypatch.setattr("services.db_connections._store", ConnectionStore(tmp_path / "connections.json"))
    monkeypatch.setenv("ANELF_BOT_SQLITE_PATH", str(tmp_path / "data/agent.sqlite3"))
    for name in ("agent", "memory"):
        registry.register(volumes.VolumeDescriptor(
            volume_id=name, name=name, description="", kind=volumes.VolumeKind.SQLITE,
            default_path=lambda name=name: str(tmp_path / f"data/{name}.sqlite3"),
        ))
    registry.register(volumes.VolumeDescriptor(
        volume_id="cognee", name="Cognee", description="", kind=volumes.VolumeKind.COGNEE_TREE,
        default_path=lambda: str(tmp_path / "cognee"), databases=databases.DATABASE_COMPONENTS,
    ))
    tracker = storage.StorageStatsTracker()
    monkeypatch.setattr(databases, "cognee_storage_stats", tracker)
    yield registry, tracker
    await asyncio.gather(*tracker._tasks)


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    """构造临时主库（agent），含普通表 / 视图 / FTS 虚表 / 各类特殊值。"""
    path = tmp_path / "data" / "agent.sqlite3"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT,
            ts_ns INTEGER,
            metadata_json TEXT,
            embedding_blob BLOB
        );
        CREATE TABLE plain (name TEXT, value REAL);
        CREATE VIEW items_view AS SELECT id, content FROM items;
        CREATE VIRTUAL TABLE items_fts USING fts5(content, content='items', content_rowid='id');
        """
    )
    vec = array.array("f", [0.1, 0.2, 0.3, 0.4]).tobytes()
    rows = [
        ("第一条", 1_700_000_000_000_000_000, '{"a": 1}', vec),
        ("第二条", 1_700_000_100_000_000_000, "[1, 2, 3]", None),
        ("长文本" + "x" * 600, None, "not-json", b"\x00\x01"),
    ]
    conn.executemany(
        "INSERT INTO items (content, ts_ns, metadata_json, embedding_blob) VALUES (?, ?, ?, ?)",
        rows,
    )
    for i in range(10):
        conn.execute("INSERT INTO plain (name, value) VALUES (?, ?)", (f"n{i}", i * 1.5))
    conn.commit()
    conn.close()
    monkeypatch.setenv("ANELF_BOT_SQLITE_PATH", str(path))
    return str(path)


@pytest.fixture()
async def svc():
    service = DatabaseService()
    yield service
    await service.close_all()


@pytest.fixture()
async def managed_files(isolated_catalog):
    from agent.memory.cognee.storage import compute_storage_stats

    registry, tracker = isolated_catalog
    root = Path(registry.resolve_path("cognee"))
    db = root / "system/databases/cognee_db"
    db.parent.mkdir(parents=True)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE documents (content TEXT)")
        conn.execute("INSERT INTO documents VALUES ('preserve me')")
    (db.parent / "graph.lbug").write_bytes(b"graph-data")
    vectors = db.parent / "test.lance.db"
    vectors.mkdir()
    (vectors / "vectors").write_bytes(b"vector-data")
    tracker.adopt(str(root), compute_storage_stats(root))
    return root, db


async def test_managed_catalog_uses_observations_and_keeps_one_volume(managed_files, isolated_catalog, svc, monkeypatch):
    root, db = managed_files
    registry, _ = isolated_catalog
    original = svc._open_conn
    opened = []

    async def open_sqlite(path, *, readonly=False):
        opened.append((path, readonly))
        return await original(path, readonly=readonly)

    monkeypatch.setattr(svc, "_open_conn", open_sqlite)
    rows = {row["id"]: row for row in await svc.list_databases()}
    assert rows["cognee_graph"]["size_bytes"] == len(b"graph-data")
    assert rows["cognee_vector"]["size_bytes"] == len(b"vector-data")
    assert rows["cognee"]["size_bytes"] == db.stat().st_size
    for key, engine in (("cognee", "sqlite"), ("cognee_graph", "ladybug"), ("cognee_vector", "lancedb")):
        assert rows[key]["engine"] == engine
        assert rows[key]["volume_id"] == "cognee"
        assert rows[key]["measured_at"] > 0
        assert rows[key]["managed_by"] == "Cognee"
    assert rows["cognee_graph"]["table_count"] is None
    assert rows["cognee_graph"]["capabilities"] == []
    assert rows["cognee"]["capabilities"] == ["browse", "query"]
    assert opened == [(str(db), True)]
    assert "cognee" not in svc._connections
    assert [v.volume_id for v in registry.list()] == ["agent", "memory", "cognee"]
    # 修改指派不让管理端抢先读取新目录或创建引擎。
    registry.mark_active("cognee", str(root))
    registry.write_location("cognee", str(root.parent / "next"))
    rows = {row["id"]: row for row in await svc.list_databases()}
    assert rows["cognee_graph"]["needs_restart"] is True
    assert rows["cognee_graph"]["path"] == str(root / "system/databases")
    assert not (root.parent / "next").exists()


async def test_managed_unknown_measurement_is_not_zero(isolated_catalog, svc):
    rows = {row["id"]: row for row in await svc.list_databases()}
    assert rows["cognee_graph"]["size_bytes"] is None
    assert rows["cognee_graph"]["exists"] is None
    assert rows["cognee_vector"]["measured_at"] is None


async def test_component_observation_failure_does_not_hide_native_databases(db_path, svc, monkeypatch):
    from agent.memory.cognee import databases

    async def fail(_root):
        raise OSError("snapshot unavailable")

    monkeypatch.setattr(databases.cognee_storage_stats, "get", fail)
    rows = {row["id"]: row for row in await svc.list_databases()}
    assert rows["agent"]["exists"] is True
    assert rows["cognee_vector"]["size_bytes"] is None
    assert rows["cognee_vector"]["error"] == "snapshot unavailable"


async def test_managed_metadata_is_readonly_and_follows_replaced_database(managed_files, svc):
    _, db = managed_files
    assert (await svc.list_tables("cognee"))[0]["readonly"] is True
    assert (await svc.table_schema("cognee", "documents"))["readonly"] is True
    assert (await svc.browse_rows("cognee", "documents"))["readonly"] is True
    assert (await svc.get_row("cognee", "documents", 1))["values"]["content"] == "preserve me"
    # 模拟发布/恢复替换；短连接不锁住旧文件，下次查询读取替换后的库。
    replacement = db.with_suffix(".next")
    with closing(sqlite3.connect(replacement)) as conn, conn:
        conn.execute("CREATE TABLE documents (content TEXT)")
        conn.execute("INSERT INTO documents VALUES ('restored')")
    replacement.replace(db)
    result = await svc.run_query("cognee", "SELECT content FROM documents")
    assert result["rows"] == [{"content": "restored"}]
    assert "cognee" not in svc._connections


@pytest.mark.parametrize("db_id", ["cognee", "cognee_graph", "cognee_vector"])
async def test_managed_components_reject_direct_mutation_before_open(managed_files, svc, db_id, monkeypatch):
    root, db = managed_files
    original = db.read_bytes()

    async def unexpected_open(*args, **kwargs):
        pytest.fail("管理写操作不得打开受管数据库")

    monkeypatch.setattr(svc, "_open_conn", unexpected_open)
    for method, args in (
        (svc.insert_row, ("documents", {"content": "bad"})),
        (svc.update_row, ("documents", 1, {"content": "bad"})),
        (svc.delete_row, ("documents", 1)),
        (svc.database_health, ()),
        (svc.backup_database, ()),
        (svc.optimize_database, (["vacuum"],)),
    ):
        with pytest.raises(DatabaseError) as error:
            await method(db_id, *args)
        assert error.value.status_code == 403
    if db_id != "cognee":
        for method, args in ((svc.list_tables, ()), (svc.run_query, ("SELECT 1",)),
                             (svc.table_schema, ("documents",)), (svc.browse_rows, ("documents",)),
                             (svc.get_row, ("documents", 1))):
            with pytest.raises(DatabaseError) as error:
                await method(db_id, *args)
            assert error.value.status_code == 403
    assert db.read_bytes() == original
    assert (root / "system/databases/graph.lbug").read_bytes() == b"graph-data"


async def test_management_lifecycle_closes_real_connections_and_retains_failed_handles(db_path, svc, monkeypatch):
    from unittest.mock import AsyncMock

    from core.lifecycle import Lifecycle, ShutdownBlockedError
    from services import database

    monkeypatch.setattr(database, "_service", svc)
    conn = await svc._get_conn("agent")
    close = conn.close
    Lifecycle.reset()
    try:
        database.register_lifecycle()
        # Web 最后登记，先停止请求再关闭其持有的管理连接。
        async def stop_web():
            assert (await (await conn.execute("SELECT COUNT(*) FROM plain")).fetchone())[0] == 10

        Lifecycle.register("web_server", object(), cleanup=stop_web)
        monkeypatch.setattr(conn, "close", AsyncMock(side_effect=RuntimeError("busy")))
        with pytest.raises(ShutdownBlockedError):
            await Lifecycle.shutdown_all()
        assert svc._connections["agent"] is conn
        monkeypatch.setattr(conn, "close", close)
        await Lifecycle.shutdown_all()
        assert svc._connections == {}
        with pytest.raises(ValueError):
            await conn.execute("SELECT 1")
    finally:
        monkeypatch.setattr(conn, "close", close)
        await svc.close_all()
        Lifecycle.reset()


@pytest.mark.parametrize("sql", [
    "PRAGMA user_version(123)",
    "WITH selected AS (SELECT name FROM plain LIMIT 1) DELETE FROM plain WHERE name IN (SELECT name FROM selected) RETURNING name",
])
async def test_query_endpoint_cannot_write_through_readonly_syntax(db_path, svc, sql):
    with pytest.raises(DatabaseError):
        await svc.run_query("agent", sql)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM plain").fetchone()[0] == 10


async def test_query_timeout_interrupts_without_breaking_crud_connection(db_path, svc, monkeypatch):
    import services.database as module

    cached = await svc._get_conn("agent")
    monkeypatch.setattr(module, "_QUERY_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(DatabaseError, match="查询超时"):
        await svc.run_query("agent", "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n) SELECT sum(x) FROM n")
    assert await svc._get_conn("agent") is cached
    assert (await (await cached.execute("SELECT COUNT(*) FROM plain")).fetchone())[0] == 10


# ======================================================================
# 库 / 表清单
# ======================================================================

class TestList:
    async def test_list_databases(self, db_path, svc):
        dbs = {d["id"]: d for d in await svc.list_databases()}
        assert dbs["agent"]["exists"] is True
        assert dbs["agent"]["size_bytes"] > 0
        assert dbs["agent"]["table_count"] >= 3
        # 派生库不存在 → exists=False 而非报错
        assert dbs["memory"]["exists"] is False

    async def test_unknown_db(self, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.list_tables("nope")
        assert e.value.status_code == 404

    async def test_list_tables_marks_readonly(self, db_path, svc):
        tables = {t["name"]: t for t in await svc.list_tables("agent")}
        assert tables["items"]["readonly"] is False
        assert tables["items_view"]["readonly"] is True  # 视图
        assert tables["items_fts"]["readonly"] is True   # 虚表
        # 影子表默认过滤
        assert "items_fts_data" not in tables

    async def test_list_tables_include_shadow(self, db_path, svc):
        tables = {t["name"]: t for t in await svc.list_tables("agent", include_shadow=True)}
        assert "items_fts_data" in tables
        assert tables["items_fts_data"]["shadow"] is True

    async def test_table_schema(self, db_path, svc):
        schema = await svc.table_schema("agent", "items")
        col_names = [c["name"] for c in schema["columns"]]
        assert col_names == ["id", "content", "ts_ns", "metadata_json", "embedding_blob"]
        assert schema["columns"][0]["pk"] is True
        assert "CREATE TABLE items" in schema["ddl"]


# ======================================================================
# 行浏览（分页 / 排序 / 筛选 / 序列化）
# ======================================================================

class TestBrowseRows:
    async def test_pagination(self, db_path, svc):
        page1 = await svc.browse_rows("agent", "plain", page=1, page_size=4)
        assert page1["total"] == 10 and page1["pages"] == 3
        assert len(page1["items"]) == 4
        page3 = await svc.browse_rows("agent", "plain", page=3, page_size=4)
        assert len(page3["items"]) == 2

    async def test_sort(self, db_path, svc):
        result = await svc.browse_rows("agent", "plain", sort="value", order="desc")
        values = [r["values"]["value"] for r in result["items"]]
        assert values == sorted(values, reverse=True)

    async def test_sort_invalid_column(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.browse_rows("agent", "plain", sort="value; DROP TABLE plain")
        assert e.value.status_code == 400

    async def test_filter(self, db_path, svc):
        result = await svc.browse_rows(
            "agent", "items", filter_col="content", filter_text="第二",
        )
        assert result["total"] == 1
        assert result["items"][0]["values"]["content"] == "第二条"

    async def test_unknown_table(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.browse_rows("agent", "no_such_table")
        assert e.value.status_code == 404

    async def test_serialization(self, db_path, svc):
        result = await svc.browse_rows("agent", "items", sort="id")
        r1 = result["items"][0]["values"]
        # vec：float32 解码
        assert r1["embedding_blob"]["__type__"] == "vec"
        assert r1["embedding_blob"]["dims"] == 4
        assert r1["embedding_blob"]["preview"][0] == pytest.approx(0.1, abs=1e-4)
        # ts_ns：附可读时间
        assert r1["ts_ns"]["__type__"] == "ts"
        assert "2023" in r1["ts_ns"]["text"]
        # JSON：解析为结构化
        assert r1["metadata_json"]["__type__"] == "json"
        assert r1["metadata_json"]["value"] == {"a": 1}
        # 长文本：截断标记
        r3 = result["items"][2]["values"]
        assert r3["content"]["__type__"] == "text"
        assert r3["content"]["truncated"] is True
        # 非向量 blob
        assert r3["embedding_blob"]["__type__"] == "blob"
        assert r3["embedding_blob"]["bytes"] == 2

    async def test_get_row_full_text(self, db_path, svc):
        row = await svc.get_row("agent", "items", 3)
        content = row["values"]["content"]
        assert content["__type__"] == "text"
        assert len(content["text"]) == 603  # 全文不截断


# ======================================================================
# 行编辑（CRUD / 只读保护 / embedding 护栏）
# ======================================================================

class TestRowCrud:
    async def test_insert_update_delete_roundtrip(self, db_path, svc):
        ins = await svc.insert_row("agent", "plain", {"name": "新增", "value": 9.9})
        assert ins["rowid"] > 0

        await svc.update_row("agent", "plain", ins["rowid"], {"name": "改名"})
        row = await svc.get_row("agent", "plain", ins["rowid"])
        assert row["values"]["name"] == "改名"

        await svc.delete_row("agent", "plain", ins["rowid"])
        with pytest.raises(DatabaseError) as e:
            await svc.get_row("agent", "plain", ins["rowid"])
        assert e.value.status_code == 404

    async def test_update_invalid_column(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.update_row("agent", "plain", 1, {"evil_col": 1})
        assert e.value.status_code == 400

    async def test_view_is_readonly(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.insert_row("agent", "items_view", {"content": "x"})
        assert e.value.status_code == 403

    async def test_virtual_table_is_readonly(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.delete_row("agent", "items_fts", 1)
        assert e.value.status_code == 403

    async def test_update_missing_row(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.update_row("agent", "plain", 99999, {"name": "x"})
        assert e.value.status_code == 404

    async def test_embedding_cleared_on_content_update(self, db_path, svc):
        """内容列变更 → embedding 自动置 NULL（等后台重建）。"""
        await svc.update_row("agent", "items", 1, {"content": "改过的内容"})
        row = await svc.get_row("agent", "items", 1)
        assert row["values"]["content"] == "改过的内容"
        assert row["values"]["embedding_blob"] is None

    async def test_embedding_kept_on_other_update(self, db_path, svc):
        """改非内容列 → embedding 保留。"""
        await svc.update_row("agent", "items", 1, {"ts_ns": 1_800_000_000_000_000_000})
        row = await svc.get_row("agent", "items", 1)
        assert row["values"]["embedding_blob"]["__type__"] == "vec"


# ======================================================================
# 只读 SQL 控制台
# ======================================================================

class TestRunQuery:
    async def test_select_ok(self, db_path, svc):
        result = await svc.run_query("agent", "SELECT name, value FROM plain WHERE value > 3")
        assert result["columns"] == ["name", "value"]
        assert result["row_count"] == 7
        assert result["elapsed_ms"] >= 0

    async def test_auto_limit(self, db_path, svc):
        safe = DatabaseService._validate_readonly_sql("SELECT * FROM plain")
        assert "LIMIT 500" in safe
        # 已有 LIMIT 不重复补
        safe2 = DatabaseService._validate_readonly_sql("select * from plain limit 5")
        assert safe2.lower().count("limit") == 1

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP TABLE plain",
            "DELETE FROM plain",
            "UPDATE plain SET name = 'x'",
            "INSERT INTO plain VALUES ('x', 1)",
            "SELECT 1; DROP TABLE plain",
            "ATTACH DATABASE 'x' AS y",
            "PRAGMA journal_mode=DELETE",
            "",
        ],
    )
    async def test_write_statements_rejected(self, db_path, svc, sql):
        with pytest.raises(DatabaseError):
            await svc.run_query("agent", sql)

    async def test_pragma_readonly_ok(self, db_path, svc):
        result = await svc.run_query("agent", "PRAGMA table_info(items)")
        assert result["row_count"] == 5

    async def test_bad_sql_error(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.run_query("agent", "SELECT * FROM no_such_table")
        assert e.value.status_code == 400


# ======================================================================
# 健康概览 / 备份 / 优化
# ======================================================================


class TestHealth:
    async def test_health_basic(self, db_path, svc):
        h = await svc.database_health("agent")
        assert h["id"] == "agent"
        assert h["size_bytes"] > 0
        assert h["page_count"] > 0
        assert 0.0 <= h["fragmentation"] <= 1.0
        # 表规模 Top：items(3) 与 plain(10) 应出现
        names = [t["name"] for t in h["top_tables"]]
        assert "items" in names and "plain" in names
        assert h["top_tables"][0]["name"] == "plain"  # 行数最多
        # analyze 建议恒在
        assert any(s["action"] == "analyze" for s in h["suggestions"])

    async def test_health_external_forbidden(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.database_health("ext:whatever")
        assert e.value.status_code == 403


class TestOptimize:
    async def test_checkpoint_and_analyze(self, db_path, svc):
        r = await svc.optimize_database("agent", ["checkpoint", "analyze"])
        assert [a["action"] for a in r["actions"]] == ["checkpoint", "analyze"]
        assert all(a["elapsed_ms"] >= 0 for a in r["actions"])

    async def test_vacuum(self, db_path, svc):
        r = await svc.optimize_database("agent", ["vacuum"])
        assert r["actions"][0]["action"] == "vacuum"

    async def test_unknown_action_rejected(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.optimize_database("agent", ["drop"])
        assert e.value.status_code == 400

    async def test_empty_actions_rejected(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.optimize_database("agent", [])
        assert e.value.status_code == 400

    async def test_external_forbidden(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.optimize_database("ext:x", ["vacuum"])
        assert e.value.status_code == 403


class TestBackup:
    async def test_backup_roundtrip(self, db_path, svc, tmp_path, monkeypatch):
        monkeypatch.setattr("core.path.ConfigPaths.MEMORY_DIR", str(tmp_path / "mem"))
        b = await svc.backup_database("agent")
        dest = sqlite3.connect(b["path"])
        try:
            assert dest.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3
            assert dest.execute("SELECT COUNT(*) FROM plain").fetchone()[0] == 10
        finally:
            dest.close()
        assert b["filename"].startswith("agent-") and b["filename"].endswith(".sqlite3")
        assert b["size_bytes"] > 0

    async def test_backup_external_forbidden(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.backup_database("ext:x")
        assert e.value.status_code == 403


class TestExternalGuards:
    async def test_row_writes_forbidden(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.insert_row("ext:x", "t", {"a": 1})
        assert e.value.status_code == 403
        with pytest.raises(DatabaseError) as e:
            await svc.update_row("ext:x", "t", 1, {"a": 1})
        assert e.value.status_code == 403
        with pytest.raises(DatabaseError) as e:
            await svc.delete_row("ext:x", "t", 1)
        assert e.value.status_code == 403

    async def test_get_row_forbidden(self, db_path, svc):
        with pytest.raises(DatabaseError) as e:
            await svc.get_row("ext:x", "t", 1)
        assert e.value.status_code == 403
