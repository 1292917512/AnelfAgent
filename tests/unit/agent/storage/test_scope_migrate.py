"""scope 迁移（agent/storage/scope_migrate.py）单元测试：旧格式键回填 adapter 维度。

覆盖：conversation/profile/alias/pending_tasks 主库迁移、记忆库标签迁移、
幂等性（user_version 守卫）、自动备份、标签/source 重写函数。
"""

from __future__ import annotations

import json

import aiosqlite

from agent.storage.scope_migrate import (
    migrate_main_db_scopes,
    migrate_memory_db_tags,
    rewrite_entity_tag,
    rewrite_profile_source,
)


class TestTagRewrite:
    def test_entity_tag_rewrite(self) -> None:
        assert rewrite_entity_tag("user:123", "qq") == "user:qq:123"
        assert rewrite_entity_tag("group:456", "qq") == "group:qq:456"

    def test_already_migrated_untouched(self) -> None:
        assert rewrite_entity_tag("user:qq:123", "qq") == "user:qq:123"

    def test_non_entity_tag_untouched(self) -> None:
        assert rewrite_entity_tag("type:fact", "qq") == "type:fact"
        assert rewrite_entity_tag("topic:天气", "qq") == "topic:天气"
        assert rewrite_entity_tag(123, "qq") == 123

    def test_profile_source_rewrite(self) -> None:
        assert rewrite_profile_source("entity_123", "qq") == "entity_qq:123"
        assert rewrite_profile_source("entity_qq:123", "qq") == "entity_qq:123"
        assert rewrite_profile_source("", "qq") == ""


async def _make_main_db(db_path: str) -> aiosqlite.Connection:
    """构造含当前 schema 的主库（scope 迁移是数据迁移，假定列已随建表就位）。"""
    db = await aiosqlite.connect(db_path)
    await db.execute(
        "CREATE TABLE conversation_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, scope_type TEXT NOT NULL,"
        "scope_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,"
        "ts_ns INTEGER NOT NULL, adapter_key TEXT NOT NULL DEFAULT '')"
    )
    await db.execute(
        "CREATE TABLE entity_profile ("
        "scope_type TEXT NOT NULL, scope_id TEXT NOT NULL, personality TEXT,"
        "updated_ts_ns INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope_type, scope_id))"
    )
    await db.execute(
        "CREATE TABLE entity_alias ("
        "scope_type TEXT NOT NULL, scope_id TEXT NOT NULL,"
        "primary_scope_type TEXT NOT NULL, primary_scope_id TEXT NOT NULL,"
        "created_ts_ns INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope_type, scope_id))"
    )
    await db.execute(
        "CREATE TABLE pending_tasks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, kind TEXT NOT NULL,"
        "payload_json TEXT NOT NULL DEFAULT '{}', ts_ns INTEGER NOT NULL)"
    )
    await db.execute(
        "CREATE TABLE conversation_summary ("
        "scope_type TEXT NOT NULL, scope_id TEXT NOT NULL,"
        "summary TEXT NOT NULL DEFAULT '', watermarks_json TEXT NOT NULL DEFAULT '{}',"
        "watermark_ids_json TEXT NOT NULL DEFAULT '{}',"
        "folded_count INTEGER NOT NULL DEFAULT 0, dropped_count INTEGER NOT NULL DEFAULT 0,"
        "updated_ts_ns INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope_type, scope_id))"
    )
    await db.commit()
    return db


class TestMainDbMigration:
    async def test_migrates_all_tables(self, tmp_path) -> None:
        db_path = str(tmp_path / "main.sqlite3")
        db = await _make_main_db(db_path)
        await db.execute(
            "INSERT INTO conversation_messages(scope_type, scope_id, role, content, ts_ns, adapter_key)"
            " VALUES('user','123','user','hi',1,'qq')"
        )
        await db.execute(
            "INSERT INTO conversation_messages(scope_type, scope_id, role, content, ts_ns, adapter_key)"
            " VALUES('group','456','user','yo',2,'')"
        )
        await db.execute("INSERT INTO entity_profile VALUES('user','123','nice',0)")
        await db.execute("INSERT INTO entity_alias VALUES('user','789','user','123',0)")
        await db.execute(
            "INSERT INTO pending_tasks(scope, kind, payload_json, ts_ns) VALUES('user_123','general',?,3)",
            (json.dumps({"scope": "user_123", "adapter_key": "qq"}),),
        )
        await db.execute(
            "INSERT INTO conversation_summary(scope_type, scope_id, summary, watermarks_json)"
            " VALUES('user','123','摘要',?)",
            (json.dumps({"user:123": 5, "user:qq:9": 7}),),
        )
        await db.commit()

        done = await migrate_main_db_scopes(db, db_path, "qq")
        assert done

        rows = [
            r[0] for r in await (await db.execute("SELECT scope_id FROM conversation_messages ORDER BY id")).fetchall()
        ]
        # 有 adapter_key 的行按行内频道归属；空值回落 legacy
        assert rows == ["qq:123", "qq:456"]

        row = await (await db.execute("SELECT scope_id FROM entity_profile")).fetchone()
        assert row[0] == "qq:123"

        row = await (await db.execute("SELECT scope_id, primary_scope_id FROM entity_alias")).fetchone()
        assert tuple(row) == ("qq:789", "qq:123")

        row = await (await db.execute("SELECT scope, payload_json FROM pending_tasks")).fetchone()
        assert row[0] == "user_qq:123"
        assert "user_qq:123" in row[1]

        # 摘要表：scope_id 迁移 + 水位线键同步改写（已迁移键保持不变）
        row = await (await db.execute(
            "SELECT scope_id, watermarks_json FROM conversation_summary"
        )).fetchone()
        assert row[0] == "qq:123"
        assert json.loads(row[1]) == {"user:qq:123": 5, "user:qq:9": 7}

        await db.close()

    async def test_webui_history_not_torn(self, tmp_path) -> None:
        """WebUI 对话完整性：assistant 回复行（无 adapter_key）跟随同 scope 的用户消息频道。

        旧代码 _record_sent_reply 不落 adapter_key 列，若空值行统一回落 legacy 默认，
        WebUI 历史会被撕裂成 qq:web_user（回复）与 webui:web_user（用户消息）两个 scope。
        """
        db_path = str(tmp_path / "main.sqlite3")
        db = await _make_main_db(db_path)
        # 用户消息带 adapter，assistant 回复不带（旧代码行为）
        await db.execute(
            "INSERT INTO conversation_messages(scope_type, scope_id, role, content, ts_ns, adapter_key)"
            " VALUES('user','web_user','user','你好',1,'webui')"
        )
        await db.execute(
            "INSERT INTO conversation_messages(scope_type, scope_id, role, content, ts_ns, adapter_key)"
            " VALUES('user','web_user','assistant','你好呀',2,'')"
        )
        # 无 adapter 列时插入的多会话行
        await db.execute(
            "INSERT INTO conversation_messages(scope_type, scope_id, role, content, ts_ns, adapter_key)"
            " VALUES('user','web_user#chat2','user','另一个会话',3,'')"
        )
        await db.commit()

        assert await migrate_main_db_scopes(db, db_path, "qq")

        rows = [
            tuple(r)
            for r in await (await db.execute("SELECT scope_id, role FROM conversation_messages ORDER BY id")).fetchall()
        ]
        assert rows == [
            ("webui:web_user", "user"),
            ("webui:web_user", "assistant"),  # 跟随同 scope 频道，不撕裂
            # chat2 无用户消息锚点，但 web_user 基 id 命中知名内置 scope 启发式
            ("webui:web_user#chat2", "user"),
        ]
        await db.close()

    async def test_idempotent_and_backup(self, tmp_path) -> None:
        import os

        db_path = str(tmp_path / "main.sqlite3")
        db = await _make_main_db(db_path)
        assert await migrate_main_db_scopes(db, db_path, "qq")
        assert not await migrate_main_db_scopes(db, db_path, "qq")
        assert os.path.exists(db_path + ".pre-scope-migration.bak")
        await db.close()


class TestMemoryDbMigration:
    async def test_tags_and_source_migrated(self, tmp_path) -> None:
        db_path = str(tmp_path / "mem.sqlite3")
        db = await aiosqlite.connect(db_path)
        await db.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY, type TEXT, content TEXT,"
            " source TEXT DEFAULT '', tags_json TEXT DEFAULT '[]')"
        )
        await db.execute(
            "CREATE TABLE memories_archive (id INTEGER PRIMARY KEY, type TEXT, content TEXT,"
            " source TEXT DEFAULT '', tags_json TEXT DEFAULT '[]')"
        )
        await db.execute(
            "INSERT INTO memories VALUES(1,'entity','x','entity_123',?)",
            (json.dumps(["user:123", "type:profile"]),),
        )
        await db.execute(
            "INSERT INTO memories VALUES(2,'fact','y','',?)",
            (json.dumps(["topic:a"]),),
        )
        await db.commit()

        assert await migrate_memory_db_tags(db, db_path, "qq")

        row = await (await db.execute("SELECT source, tags_json FROM memories WHERE id=1")).fetchone()
        assert row[0] == "entity_qq:123"
        assert json.loads(row[1]) == ["user:qq:123", "type:profile"]

        row = await (await db.execute("SELECT source, tags_json FROM memories WHERE id=2")).fetchone()
        assert row[0] == ""
        assert json.loads(row[1]) == ["topic:a"]

        # 幂等
        assert not await migrate_memory_db_tags(db, db_path, "qq")
        await db.close()
