"""外部数据库连接注册表单元测试 — CRUD / 脱敏 / ${ENV_VAR} 展开 / 校验与失败路径。

不连接真实 PostgreSQL / MySQL 服务器（连接测试指向不可达地址验证失败路径）。
"""

from __future__ import annotations

import pytest

from services.database import DatabaseError
from services.db_connections import ConnectionStore, DbConnection, _validate_readonly_sql


@pytest.fixture()
def store(tmp_path):
    return ConnectionStore(path=tmp_path / "db_connections.json")


@pytest.fixture()
def pg_payload():
    return {
        "name": "测试PG",
        "engine": "postgresql",
        "host": "db.local",
        "database": "anelf",
        "username": "u",
        "password": "plain-secret",
    }


class TestCrud:
    def test_add_and_defaults(self, store, pg_payload):
        conn = store.add(pg_payload)
        assert conn.id and conn.effective_port() == 5432
        assert store.get(conn.id).name == "测试PG"

    def test_mysql_default_port(self, store, pg_payload):
        conn = store.add({**pg_payload, "engine": "mysql"})
        assert conn.effective_port() == 3306

    def test_persistence_reload(self, store, pg_payload, tmp_path):
        conn = store.add(pg_payload)
        reloaded = ConnectionStore(path=tmp_path / "db_connections.json")
        assert reloaded.get(conn.id).engine == "postgresql"

    def test_update_keeps_masked_password(self, store, pg_payload):
        conn = store.add(pg_payload)
        updated = store.update(conn.id, {**pg_payload, "name": "改名", "password": "****"})
        assert updated.name == "改名"
        assert updated.password == "plain-secret"

    def test_update_replaces_password(self, store, pg_payload):
        conn = store.add(pg_payload)
        updated = store.update(conn.id, {**pg_payload, "password": "new-secret"})
        assert updated.password == "new-secret"

    def test_delete(self, store, pg_payload):
        conn = store.add(pg_payload)
        store.delete(conn.id)
        assert store.list() == []
        with pytest.raises(DatabaseError) as e:
            store.get(conn.id)
        assert e.value.status_code == 404


class TestMasking:
    def test_public_dict_masks_password(self, store, pg_payload):
        store.add(pg_payload)
        pub = store.list_public()[0]
        assert pub["password"] == "****"
        assert pub["has_password"] is True

    def test_public_dict_no_password(self, store, pg_payload):
        store.add({**pg_payload, "password": ""})
        pub = store.list_public()[0]
        assert pub["password"] == ""
        assert pub["has_password"] is False


class TestEnvRef:
    def test_password_env_expansion(self, monkeypatch):
        monkeypatch.setenv("PG_PASS_TEST", "real-secret")
        conn = DbConnection(name="t", engine="postgresql", database="d", password="${PG_PASS_TEST}")
        assert conn.resolved_password() == "real-secret"


class TestValidation:
    def test_unknown_engine(self, store, pg_payload):
        with pytest.raises(DatabaseError) as e:
            store.add({**pg_payload, "engine": "oracle"})
        assert e.value.status_code == 400

    def test_empty_name(self, store, pg_payload):
        with pytest.raises(DatabaseError):
            store.add({**pg_payload, "name": " "})

    def test_empty_database(self, store, pg_payload):
        with pytest.raises(DatabaseError):
            store.add({**pg_payload, "database": ""})

    def test_bad_port(self, store, pg_payload):
        with pytest.raises(DatabaseError):
            store.add({**pg_payload, "port": 70000})


class TestReadonlySql:
    def test_auto_limit(self):
        assert "LIMIT 500" in _validate_readonly_sql("SELECT * FROM t")

    def test_pragma_rejected(self):
        with pytest.raises(DatabaseError) as e:
            _validate_readonly_sql("PRAGMA table_info(t)")
        assert e.value.status_code == 403

    def test_write_rejected(self):
        with pytest.raises(DatabaseError):
            _validate_readonly_sql("DELETE FROM t")

    def test_multi_statement_rejected(self):
        with pytest.raises(DatabaseError):
            _validate_readonly_sql("SELECT 1; SELECT 2")


class TestFailurePaths:
    async def test_test_unreachable(self, store):
        result = await store.test({
            "name": "t", "engine": "mysql",
            "host": "192.0.2.1", "port": 1, "database": "x",
        })
        assert result["ok"] is False
        assert result["error"]

    async def test_adapter_unknown_id(self, store):
        with pytest.raises(DatabaseError) as e:
            await store.adapter("nope")
        assert e.value.status_code == 404

    async def test_source_entries_unreachable(self, store, pg_payload):
        store.add({**pg_payload, "host": "192.0.2.1", "port": 1})
        entries = await store.list_source_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["id"].startswith("ext:")
        assert entry["external"] is True
        assert entry["exists"] is False
        assert entry["engine"] == "postgresql"


class TestPasswordUpdate:
    def test_empty_password_keeps_old(self, store, pg_payload):
        conn = store.add(pg_payload)
        updated = store.update(conn.id, {**pg_payload, "password": ""})
        assert updated.password == "plain-secret"
