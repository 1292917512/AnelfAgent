"""数据库管理服务单元测试 — 白名单 / 只读 SQL / 序列化 / rowid CRUD / embedding 护栏。"""

from __future__ import annotations

import array
import sqlite3

import pytest

from services.database import DatabaseError, DatabaseService


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
