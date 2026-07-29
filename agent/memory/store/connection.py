"""记忆库连接管理器：单连接生命周期、事务模板、schema 初始化、vec 索引与 FTS 触发器。

全库仅一份 aiosqlite 连接（WAL），所有子模块（cognee_queue / file_index /
tool_errors / search）与 MemoryStore 门面共享本管理器，不各自创建连接。
写路径统一经 tx() 事务模板保证复合写原子性。
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import aiosqlite

from core.log import log

# 连接健康检查节流间隔（秒）：避免每次 get_db 都执行 SELECT 1
_HEALTH_CHECK_INTERVAL = 30.0


class MemoryConnectionManager:
    """SQLite 单连接管理器：连接/健康检查/建表/vec 索引/FTS 触发器。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None
        self.connect_lock = asyncio.Lock()
        self.last_health_check = 0.0
        self.initialized = False
        self.fts_available = False
        self.chunks_fts_available = False
        self.vec_available = False
        self.vec_dims: Optional[int] = None

    @asynccontextmanager
    async def tx(self, db: aiosqlite.Connection) -> AsyncIterator[None]:
        """事务模板：复合写全部成功才 commit，任一异常 rollback。

        共享连接上未提交的半成品事务会被后续无关调用的 commit 一并落盘，
        因此所有多步写必须经此模板保证原子性。
        """
        try:
            yield
        except Exception:
            try:
                await db.rollback()
            except Exception as rb_exc:
                log(f"记忆库事务回滚失败: {rb_exc}", "DEBUG", tag="记忆")
            raise
        else:
            await db.commit()

    async def get_db(self) -> aiosqlite.Connection:
        # 健康检查失败后的关闭/重建全程走 connect_lock：
        # 并发调用（如后台 EmbeddingWorker 与请求路径）若各自持有独立连接写同一文件
        # 会触发 database is locked，重连交错还可能在关闭途中复用旧连接。
        # 健康检查（SELECT 1）按 30s 节流，避免每次调用都探测（与 SqliteBackend 同约定）
        async with self.connect_lock:
            if self.db is not None:
                now = time.monotonic()
                if now - self.last_health_check < _HEALTH_CHECK_INTERVAL:
                    return self.db
                try:
                    await self.db.execute("SELECT 1")
                    self.last_health_check = now
                    return self.db
                except Exception:
                    try:
                        await self.db.close()
                    except Exception:
                        pass
                    self.db = None

            from pathlib import Path
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

            db = await aiosqlite.connect(self.db_path)
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA synchronous=NORMAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            self.vec_available = await self._load_vec_extension(db)

            if not self.initialized:
                await self._init_schema(db)
                self.initialized = True

            self.db = db
            return db

    async def close(self) -> None:
        # 与 get_db 的健康检查/重建共用同一把锁，避免关闭途中被其他协程复用
        async with self.connect_lock:
            if self.db:
                try:
                    await self.db.close()
                except Exception as exc:
                    log(f"记忆库连接关闭异常: {exc}", "DEBUG", tag="记忆")
                self.db = None

    async def _load_vec_extension(self, db: aiosqlite.Connection) -> bool:
        """加载 sqlite-vec 扩展（连接级），失败时向量检索降级为全表扫描。"""
        try:
            import sqlite_vec

            await db.enable_load_extension(True)
            try:
                await db.load_extension(sqlite_vec.loadable_path())
            finally:
                await db.enable_load_extension(False)
            cursor = await db.execute("SELECT vec_version()")
            await cursor.fetchone()
            return True
        except Exception as exc:
            log(f"sqlite-vec 不可用，向量检索降级为全表扫描: {exc}", "WARNING")
            return False

    async def _init_schema(self, db: aiosqlite.Connection) -> None:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                importance REAL NOT NULL DEFAULT 0.5,
                ts_ns INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                embedding_blob BLOB
            );
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_mem_source ON memories(source);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_mem_ts ON memories(ts_ns);")

        for stmt in (
            "ALTER TABLE memories ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE memories ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE memories ADD COLUMN last_accessed_ns INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE memories ADD COLUMN migrated INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                await db.execute(stmt)
            except Exception as e:
                log(f"Schema 迁移: {e}", "DEBUG")

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_mem_access ON memories(access_count);"
        )

        # ---- 遗忘归档表（归档记忆不参与召回，但可恢复） ----
        await db.execute("""
            CREATE TABLE IF NOT EXISTS memories_archive (
                id INTEGER PRIMARY KEY,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                importance REAL NOT NULL DEFAULT 0.5,
                ts_ns INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                tags_json TEXT NOT NULL DEFAULT '[]',
                access_count INTEGER NOT NULL DEFAULT 0,
                archived_at_ns INTEGER NOT NULL,
                archive_reason TEXT NOT NULL DEFAULT ''
            );
        """)
        # 懒迁移：归档保留向量与最近访问时间，恢复时可原样回填
        cursor = await db.execute("PRAGMA table_info(memories_archive)")
        archive_cols = {row["name"] for row in await cursor.fetchall()}
        if "embedding_blob" not in archive_cols:
            await db.execute("ALTER TABLE memories_archive ADD COLUMN embedding_blob BLOB")
        if "last_accessed_ns" not in archive_cols:
            await db.execute(
                "ALTER TABLE memories_archive ADD COLUMN last_accessed_ns INTEGER NOT NULL DEFAULT 0"
            )

        # ---- 文件索引表 ----
        await db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                hash TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL
            );
        """)

        # ---- 分块表 ----
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                hash TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB,
                updated_ns INTEGER NOT NULL
            );
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);")

        # ---- Embedding 缓存表 ----
        await db.execute("""
            CREATE TABLE IF NOT EXISTS embedding_cache (
                hash TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                dims INTEGER,
                updated_ns INTEGER NOT NULL
            );
        """)

        # ---- FTS5 虚拟表（使用 unicode61 tokenizer + 触发器自动同步） ----
        self.chunks_fts_available = False
        try:
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(content, content='memories', content_rowid='id',
                           tokenize='unicode61 remove_diacritics 2');
            """)
            self.fts_available = True
            # 创建触发器保持 FTS 索引自动同步
            await self._create_fts_triggers(db)
            await self._sync_fts_index(db)
        except Exception as exc:
            log(f"FTS5 不可用，降级为纯 SQL LIKE 搜索: {exc}", "WARNING")
            self.fts_available = False

        try:
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(text, id UNINDEXED, path UNINDEXED,
                           start_line UNINDEXED, end_line UNINDEXED,
                           tokenize='unicode61 remove_diacritics 2');
            """)
            self.chunks_fts_available = True
        except Exception as exc:
            log(f"chunks_fts 创建失败: {exc}", "WARNING")

        # ---- 工具错误追踪表 ----
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tool_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL,
                error_type TEXT NOT NULL DEFAULT '',
                error_msg TEXT NOT NULL,
                args_json TEXT NOT NULL DEFAULT '{}',
                context TEXT NOT NULL DEFAULT '',
                resolved INTEGER NOT NULL DEFAULT 0,
                ts_ns INTEGER NOT NULL
            );
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_te_tool ON tool_errors(tool_name);"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_te_ts ON tool_errors(ts_ns);"
        )

        # ---- Cognee 异步投影队列与 ID 映射 ----
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cognee_sync_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                operation TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_retry_ns INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                created_ns INTEGER NOT NULL,
                updated_ns INTEGER NOT NULL
            );
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_cognee_queue_ready "
            "ON cognee_sync_queue(status, next_retry_ns, id);"
        )
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cognee_memory_map (
                memory_id INTEGER PRIMARY KEY,
                dataset_name TEXT NOT NULL,
                dataset_id TEXT NOT NULL DEFAULT '',
                data_id TEXT NOT NULL DEFAULT '',
                synced_ns INTEGER NOT NULL
            );
        """)
        # 上次进程异常退出时可能遗留 processing，启动后安全重试。
        await db.execute(
            "UPDATE cognee_sync_queue SET status='pending' WHERE status='processing'"
        )

        await self._init_vec_index(db)

        await db.commit()

    # ------------------------------------------------------------------
    # sqlite-vec 向量索引（embedding_blob 为权威数据，vec0 表为派生索引）
    # ------------------------------------------------------------------

    async def _init_vec_index(self, db: aiosqlite.Connection) -> None:
        """初始化 vec0 索引表并对齐既有数据，失败时整体降级为全表扫描。"""
        if not self.vec_available:
            return
        try:
            dims = await self._infer_vec_dims(db)
            if dims is None:
                return  # 尚无 embedding，首次写入时惰性建表
            await self._ensure_vec_tables(db, dims)
            await self._rebuild_vec_index_if_stale(db)
        except Exception as exc:
            log(f"vec 索引初始化失败，降级为全表扫描: {exc}", "WARNING")
            self.vec_available = False
            self.vec_dims = None

    async def _infer_vec_dims(self, db: aiosqlite.Connection) -> Optional[int]:
        """从既有数据推断 embedding 维度（blob 字节数 / 4）。"""
        for sql, is_bytes in (
            ("SELECT length(embedding_blob) AS n FROM memories WHERE embedding_blob IS NOT NULL LIMIT 1", True),
            ("SELECT dims AS n FROM embedding_cache WHERE dims IS NOT NULL AND dims > 0 LIMIT 1", False),
            ("SELECT length(embedding) AS n FROM chunks WHERE embedding IS NOT NULL LIMIT 1", True),
        ):
            cursor = await db.execute(sql)
            row = await cursor.fetchone()
            if row and row["n"]:
                n = int(row["n"])
                return n // 4 if is_bytes else n
        return None

    async def _existing_vec_dims(self, db: aiosqlite.Connection) -> Optional[int]:
        """读取已存在的 memories_vec 声明维度，不存在返回 None。"""
        cursor = await db.execute(
            "SELECT sql FROM sqlite_master WHERE name='memories_vec'"
        )
        row = await cursor.fetchone()
        if not row or not row["sql"]:
            return None
        m = re.search(r"float\[(\d+)\]", str(row["sql"]))
        return int(m.group(1)) if m else None

    async def _ensure_vec_tables(self, db: aiosqlite.Connection, dims: int) -> None:
        """确保 vec0 索引表存在且维度匹配；维度变更时重建。"""
        # vec_dims 已缓存时跳过 sqlite_master 探测（写入路径热调用）
        existing = (
            self.vec_dims if self.vec_dims is not None
            else await self._existing_vec_dims(db)
        )
        if existing is not None and existing != dims:
            log(f"vec 索引维度变更 {existing}→{dims}，重建索引", "WARNING")
            await db.execute("DROP TABLE IF EXISTS memories_vec")
            await db.execute("DROP TABLE IF EXISTS chunks_vec")
            existing = None
        await db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec "
            f"USING vec0(embedding float[{dims}] distance_metric=cosine)"
        )
        await db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec "
            f"USING vec0(chunk_id TEXT PRIMARY KEY, embedding float[{dims}] distance_metric=cosine)"
        )
        self.vec_dims = dims
        if existing is None:
            await self._rebuild_vec_index_if_stale(db)

    async def _rebuild_vec_index_if_stale(self, db: aiosqlite.Connection) -> None:
        """计数不一致时从权威 BLOB 列全量回填（覆盖迁移/修复/重建场景）。"""
        pairs = (
            ("memories_vec", "memories", "embedding_blob", "id", "rowid"),
            ("chunks_vec", "chunks", "embedding", "id", "chunk_id"),
        )
        for vec_table, src_table, blob_col, src_key, vec_key in pairs:
            src_cnt = await (await db.execute(
                f"SELECT COUNT(*) AS c FROM {src_table} WHERE {blob_col} IS NOT NULL"
            )).fetchone()
            idx_cnt = await (await db.execute(
                f"SELECT COUNT(*) AS c FROM {vec_table}"
            )).fetchone()
            if (src_cnt["c"] if src_cnt else 0) != (idx_cnt["c"] if idx_cnt else 0):
                await db.execute(f"DELETE FROM {vec_table}")
                await db.execute(
                    f"INSERT INTO {vec_table}({vec_key}, embedding) "
                    f"SELECT {src_key}, {blob_col} FROM {src_table} WHERE {blob_col} IS NOT NULL"
                )
                log(f"vec 索引回填: {vec_table} ← {src_cnt['c'] if src_cnt else 0} 条", "DEBUG", tag="思维")

    async def vec_upsert_memory(self, db: aiosqlite.Connection, memory_id: int, blob: Optional[bytes]) -> None:
        """同步单条记忆到 vec 索引（失败不影响主流程）。"""
        if not self.vec_available or memory_id <= 0:
            return
        try:
            if blob is None:
                if self.vec_dims is not None:
                    await db.execute("DELETE FROM memories_vec WHERE rowid=?", (memory_id,))
                return
            await self._ensure_vec_tables(db, len(blob) // 4)
            await db.execute("DELETE FROM memories_vec WHERE rowid=?", (memory_id,))
            await db.execute(
                "INSERT INTO memories_vec(rowid, embedding) VALUES(?,?)",
                (memory_id, blob),
            )
        except Exception as exc:
            log(f"vec 索引写入失败 id={memory_id}: {exc}", "DEBUG")

    async def vec_delete_memories(self, db: aiosqlite.Connection, memory_ids: list[int]) -> None:
        """从 vec 索引批量移除记忆。"""
        if not self.vec_available or self.vec_dims is None or not memory_ids:
            return
        try:
            placeholders = ",".join("?" for _ in memory_ids)
            await db.execute(
                f"DELETE FROM memories_vec WHERE rowid IN ({placeholders})",
                memory_ids,
            )
        except Exception as exc:
            log(f"vec 索引删除失败: {exc}", "DEBUG")

    async def vec_upsert_chunk(self, db: aiosqlite.Connection, chunk_id: str, blob: Optional[bytes]) -> None:
        """同步单个 chunk 到 vec 索引（失败不影响主流程）。"""
        if not self.vec_available:
            return
        try:
            if blob is None:
                if self.vec_dims is not None:
                    await db.execute("DELETE FROM chunks_vec WHERE chunk_id=?", (chunk_id,))
                return
            await self._ensure_vec_tables(db, len(blob) // 4)
            await db.execute("DELETE FROM chunks_vec WHERE chunk_id=?", (chunk_id,))
            await db.execute(
                "INSERT INTO chunks_vec(chunk_id, embedding) VALUES(?,?)",
                (chunk_id, blob),
            )
        except Exception as exc:
            log(f"vec 索引写入失败 chunk={chunk_id}: {exc}", "DEBUG")

    async def vec_delete_chunks_by_path(self, db: aiosqlite.Connection, path: str) -> None:
        """按文件路径移除 chunks vec 索引（需在 chunks 行删除前调用）。"""
        if not self.vec_available or self.vec_dims is None:
            return
        try:
            cursor = await db.execute("SELECT id FROM chunks WHERE path=?", (path,))
            ids = [row["id"] for row in await cursor.fetchall()]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                await db.execute(
                    f"DELETE FROM chunks_vec WHERE chunk_id IN ({placeholders})", ids,
                )
        except Exception as exc:
            log(f"vec 索引按路径删除失败 [{path}]: {exc}", "DEBUG")

    async def _create_fts_triggers(self, db: aiosqlite.Connection) -> None:
        """为 memories_fts 创建自动同步触发器（INSERT/DELETE/UPDATE）。"""
        triggers = [
            """CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
            END;""",
            """CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.id, old.content);
            END;""",
            """CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF content ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.id, old.content);
                INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
            END;""",
        ]
        for sql in triggers:
            try:
                await db.execute(sql)
            except Exception as e:
                log(f"FTS 触发器创建: {e}", "DEBUG")

    async def _sync_fts_index(self, db: aiosqlite.Connection) -> None:
        """确保所有记忆都在 FTS 索引中（修复旧记忆未被索引的问题）。"""
        try:
            cursor = await db.execute(
                "SELECT id, content FROM memories WHERE id NOT IN "
                "(SELECT rowid FROM memories_fts)"
            )
            missing = await cursor.fetchall()
            if missing:
                for row in missing:
                    await db.execute(
                        "INSERT INTO memories_fts(rowid, content) VALUES(?,?)",
                        (row["id"], row["content"]),
                    )
                log(f"FTS 索引同步: 补充 {len(missing)} 条未索引的记忆", tag="思维")
        except Exception as exc:
            log(f"FTS 索引同步失败: {exc}", "WARNING", tag="思维")
