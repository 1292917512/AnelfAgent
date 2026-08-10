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
        # 写事务串行锁：单连接上所有未提交写共处同一 SQLite 事务，
        # 任一协程 rollback 会连带回滚其他协程已成功的写入；tx 持锁后写事务互斥
        self.write_lock = asyncio.Lock()
        self.last_health_check = 0.0
        # 记忆标签迁移状态：失败不置完成标志，60s 退避后重试
        self._tags_migrated = False
        self._tags_migrate_retry_after = 0.0
        self.initialized = False
        self.fts_available = False
        self.chunks_fts_available = False
        self.vec_available = False
        self.vec_dims: Optional[int] = None

    @staticmethod
    async def _ensure_column(
        db: aiosqlite.Connection, table: str, column: str, ddl: str,
    ) -> None:
        """幂等加列迁移：列不存在时 ALTER TABLE 添加。"""
        cursor = await db.execute(f"PRAGMA table_info({table})")
        columns = {row["name"] for row in await cursor.fetchall()}
        if column not in columns:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            log(f"记忆库迁移: {table} 新增列 {column}", tag="思维")

    @asynccontextmanager
    async def tx(self, db: aiosqlite.Connection) -> AsyncIterator[None]:
        """事务模板：复合写全部成功才 commit，任一异常 rollback。

        共享连接上未提交的半成品事务会被后续无关调用的 commit 一并落盘，
        因此所有多步写必须经此模板保证原子性；写锁串行化并发事务，
        防止一个协程的 rollback 连带回滚另一协程已成功的写入。
        注意：不可嵌套使用（写锁不可重入）。
        """
        async with self.write_lock:
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
                except Exception:
                    try:
                        await self.db.close()
                    except Exception:
                        pass
                    self.db = None
                else:
                    await self._retry_tag_migration(self.db)
                    return self.db

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
            # 记忆标签/画像 source 的 scope 迁移（user_version 幂等，失败退避重试）
            await self._retry_tag_migration(db)

            self.db = db
            return db

    async def _retry_tag_migration(self, db: aiosqlite.Connection) -> None:
        """记忆标签迁移尝试：失败不置完成标志，60s 退避后在后续周期重试。自身不抛异常。"""
        if self._tags_migrated or time.monotonic() < self._tags_migrate_retry_after:
            return
        try:
            from agent.storage.scope_migrate import get_legacy_adapter, migrate_memory_db_tags
            await migrate_memory_db_tags(db, self.db_path, get_legacy_adapter())
            self._tags_migrated = True
        except Exception as exc:
            log(f"记忆标签迁移失败（60s 后重试，备份可恢复）: {exc}", "ERROR", tag="记忆")
            self._tags_migrate_retry_after = time.monotonic() + 60.0

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
                embedding_blob BLOB,
                tags_json TEXT NOT NULL DEFAULT '[]',
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed_ns INTEGER NOT NULL DEFAULT 0,
                migrated INTEGER NOT NULL DEFAULT 0
            );
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_mem_source ON memories(source);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_mem_ts ON memories(ts_ns);")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_mem_access ON memories(access_count);"
        )
        # version 列迁移：内容修订版本号（update +1），审计与冲突排查用
        await self._ensure_column(db, "memories", "version", "INTEGER NOT NULL DEFAULT 1")

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
                archive_reason TEXT NOT NULL DEFAULT '',
                embedding_blob BLOB,
                last_accessed_ns INTEGER NOT NULL DEFAULT 0
            );
        """)
        await self._ensure_column(db, "memories_archive", "version", "INTEGER NOT NULL DEFAULT 1")

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

        # ---- schema 元信息表（分词器版本等迁移标记） ----
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        # ---- FTS5 虚拟表（unicode61；内容为分词后文本，写路径手动同步） ----
        # 历史版本为 external-content + 触发器同步原文，unicode61 下中文整句
        # 单 token 导致 bigram 查询无法命中；现改为分词后文本手动同步，
        # 分词器版本不一致时全量重建（见 _migrate_fts_indexes）。
        self.chunks_fts_available = False
        try:
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(content, tokenize='unicode61 remove_diacritics 2');
            """)
            self.fts_available = True
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

        await self._migrate_fts_indexes(db)

        # ---- 记忆审计（update/delete/archive/merge 事件流，只追加） ----
        await db.execute("""
            CREATE TABLE IF NOT EXISTS memory_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                ts_ns INTEGER NOT NULL
            );
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_mid ON memory_audit(memory_id);"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_ts ON memory_audit(ts_ns);"
        )

        # ---- 自动捕获游标（每 scope 的提取进度，进程重启后续跑） ----
        await db.execute("""
            CREATE TABLE IF NOT EXISTS capture_cursors (
                scope_key TEXT PRIMARY KEY,
                last_msg_id INTEGER NOT NULL DEFAULT 0,
                counted_msg_id INTEGER NOT NULL DEFAULT 0,
                pending_turns INTEGER NOT NULL DEFAULT 0,
                warmup_threshold INTEGER NOT NULL DEFAULT 1,
                updated_ns INTEGER NOT NULL
            );
        """)
        await self._ensure_column(
            db, "capture_cursors", "counted_msg_id", "INTEGER NOT NULL DEFAULT 0"
        )
        await self._ensure_column(
            db, "capture_cursors", "last_batch_sig", "TEXT NOT NULL DEFAULT ''"
        )

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
        # entry_kind 区分投影条目类型：memory（记忆）/ graph_node（关系节点）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cognee_sync_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_kind TEXT NOT NULL DEFAULT 'memory',
                entry_id INTEGER NOT NULL,
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
            CREATE TABLE IF NOT EXISTS cognee_entry_map (
                entry_kind TEXT NOT NULL DEFAULT 'memory',
                entry_id INTEGER NOT NULL,
                dataset_name TEXT NOT NULL,
                dataset_id TEXT NOT NULL DEFAULT '',
                data_id TEXT NOT NULL DEFAULT '',
                synced_ns INTEGER NOT NULL,
                PRIMARY KEY (entry_kind, entry_id)
            );
        """)
        # 上次进程异常退出时可能遗留 processing，启动后安全重试。
        await db.execute(
            "UPDATE cognee_sync_queue SET status='pending' WHERE status='processing'"
        )

        # ---- 关系图谱（权威存储；cognee 仅为其投影层） ----
        # 节点：实体型（user/group，key 与实体 scope 标签同构）+ 自由型（person/topic/...）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS graph_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_key TEXT NOT NULL UNIQUE,
                node_type TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_ns INTEGER NOT NULL,
                updated_ns INTEGER NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0
            );
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_gn_type ON graph_nodes(node_type);"
        )
        # 边：(subject, predicate, object) 唯一，重复写入即更新强度与证据
        await db.execute("""
            CREATE TABLE IF NOT EXISTS graph_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_id INTEGER NOT NULL,
                predicate TEXT NOT NULL,
                object_id INTEGER NOT NULL,
                symmetric INTEGER NOT NULL DEFAULT 0,
                strength REAL NOT NULL DEFAULT 0.7,
                evidence TEXT NOT NULL DEFAULT '',
                source_memory_id INTEGER,
                origin TEXT NOT NULL DEFAULT 'manual',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_ns INTEGER NOT NULL,
                updated_ns INTEGER NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0,
                UNIQUE (subject_id, predicate, object_id)
            );
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ge_subject ON graph_edges(subject_id);"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ge_object ON graph_edges(object_id);"
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
            # 升级 WARNING：运行期失败会使该记忆永久不可向量检索；
            # 置 NULL embedding 让 backfill_embeddings 后续重试自愈
            log(f"vec 索引写入失败 id={memory_id}: {exc}", "WARNING", tag="记忆")
            if blob is not None:
                try:
                    await db.execute(
                        "UPDATE memories SET embedding_blob=NULL WHERE id=?", (memory_id,),
                    )
                except Exception:
                    pass

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

    # ------------------------------------------------------------------
    # FTS 索引（分词后文本，写路径手动同步；分词器版本变更全量重建）
    # ------------------------------------------------------------------

    async def _migrate_fts_indexes(self, db: aiosqlite.Connection) -> None:
        """FTS 索引迁移：清理旧触发器/旧表，版本不一致时全量重建分词索引。"""
        from .tokenizer import FTS_TOKENIZER_VERSION

        # 旧版（external-content + 触发器同步原文）残留清理
        for trigger in ("memories_ai", "memories_ad", "memories_au"):
            await db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        if self.fts_available:
            # external-content 表不支持手动增删，直接 drop 重建为独立表
            cursor = await db.execute(
                "SELECT sql FROM sqlite_master WHERE name='memories_fts'"
            )
            row = await cursor.fetchone()
            if row and row["sql"] and "content='memories'" in str(row["sql"]):
                await db.execute("DROP TABLE memories_fts")
                await db.execute("""
                    CREATE VIRTUAL TABLE memories_fts
                    USING fts5(content, tokenize='unicode61 remove_diacritics 2');
                """)

        cursor = await db.execute(
            "SELECT value FROM schema_meta WHERE key='fts_tokenizer_version'"
        )
        row = await cursor.fetchone()
        stored_version = row["value"] if row else ""
        if stored_version == FTS_TOKENIZER_VERSION:
            # 计数对账：运行期单条 FTS 写入失败会使该记忆永久不可检索，
            # 启动时计数不一致则全量重建兜底
            if self.fts_available:
                src = await (await db.execute("SELECT COUNT(*) AS c FROM memories")).fetchone()
                idx = await (await db.execute("SELECT COUNT(*) AS c FROM memories_fts")).fetchone()
                if (src["c"] if src else 0) != (idx["c"] if idx else 0):
                    await self._load_fts_vocab(db)
                    await self._rebuild_memories_fts(db)
                    log(
                        f"memories_fts 计数对账不一致（{src['c'] if src else 0} vs "
                        f"{idx['c'] if idx else 0}），已全量重建",
                        "WARNING", tag="思维",
                    )
            return

        await self._load_fts_vocab(db)
        if self.fts_available:
            await self._rebuild_memories_fts(db)
        if self.chunks_fts_available:
            await self._rebuild_chunks_fts(db)
        await db.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('fts_tokenizer_version', ?)",
            (FTS_TOKENIZER_VERSION,),
        )
        log(f"FTS 索引已按分词器 {FTS_TOKENIZER_VERSION} 重建", tag="思维")

    async def _load_fts_vocab(self, db: aiosqlite.Connection) -> None:
        """从图谱节点与既有标签构建自定义词典（昵称/专名不被切碎）。"""
        import json as _json

        from .tokenizer import add_words

        words: set[str] = set()
        try:
            cursor = await db.execute(
                "SELECT label FROM graph_nodes WHERE label != '' AND archived = 0"
            )
            words.update(str(r["label"]).strip() for r in await cursor.fetchall())
        except Exception:
            pass  # graph_nodes 可能尚未建表（首次初始化顺序），忽略即可
        try:
            cursor = await db.execute("SELECT tags_json FROM memories")
            for r in await cursor.fetchall():
                try:
                    tags = _json.loads(r["tags_json"]) if r["tags_json"] else []
                except (ValueError, TypeError):
                    continue
                for tag in tags:
                    if isinstance(tag, str) and ":" in tag:
                        words.add(tag.split(":", 1)[1].strip())
        except Exception as exc:
            log(f"FTS 词典构建（标签部分）失败: {exc}", "DEBUG")
        words.discard("")
        if words:
            add_words(words)
            log(f"FTS 自定义词典: {len(words)} 词", "DEBUG", tag="思维")

    async def _rebuild_memories_fts(self, db: aiosqlite.Connection) -> None:
        """全量重建 memories_fts（分词在 worker 线程执行，避免阻塞事件循环）。"""
        from .tokenizer import tokenize_for_index

        cursor = await db.execute("SELECT id, content FROM memories")
        rows = await cursor.fetchall()
        tokenized = await asyncio.to_thread(
            lambda: [(int(r["id"]), tokenize_for_index(r["content"])) for r in rows]
        )
        await db.execute("DELETE FROM memories_fts")
        await db.executemany(
            "INSERT INTO memories_fts(rowid, content) VALUES(?, ?)", tokenized,
        )
        if tokenized:
            log(f"memories_fts 重建: {len(tokenized)} 条", "DEBUG", tag="思维")

    async def _rebuild_chunks_fts(self, db: aiosqlite.Connection) -> None:
        """全量重建 chunks_fts（分词在 worker 线程执行）。"""
        from .tokenizer import tokenize_for_index

        cursor = await db.execute("SELECT id, path, start_line, end_line, text FROM chunks")
        rows = await cursor.fetchall()
        tokenized = await asyncio.to_thread(
            lambda: [
                (str(r["id"]), r["path"], r["start_line"], r["end_line"],
                 tokenize_for_index(r["text"]))
                for r in rows
            ]
        )
        await db.execute("DELETE FROM chunks_fts")
        await db.executemany(
            "INSERT INTO chunks_fts(id, path, start_line, end_line, text) VALUES(?,?,?,?,?)",
            tokenized,
        )
        if tokenized:
            log(f"chunks_fts 重建: {len(tokenized)} 条", "DEBUG", tag="思维")

    async def fts_upsert_memory(
        self, db: aiosqlite.Connection, memory_id: int, content: str,
    ) -> None:
        """同步单条记忆到 FTS 索引（分词后文本；失败不影响主流程）。"""
        if not self.fts_available or memory_id <= 0:
            return
        from .tokenizer import tokenize_for_index

        try:
            tokenized = await asyncio.to_thread(tokenize_for_index, content)
            await db.execute("DELETE FROM memories_fts WHERE rowid=?", (memory_id,))
            await db.execute(
                "INSERT INTO memories_fts(rowid, content) VALUES(?, ?)",
                (memory_id, tokenized),
            )
        except Exception as exc:
            # 升级 WARNING：运行期失败会使该记忆永久不可全文检索
            # （启动时计数对账会全量重建兜底）
            log(f"FTS 索引写入失败 id={memory_id}: {exc}", "WARNING", tag="记忆")

    async def fts_delete_memories(
        self, db: aiosqlite.Connection, memory_ids: list[int],
    ) -> None:
        """从 FTS 索引批量移除记忆。"""
        if not self.fts_available or not memory_ids:
            return
        try:
            placeholders = ",".join("?" for _ in memory_ids)
            await db.execute(
                f"DELETE FROM memories_fts WHERE rowid IN ({placeholders})",
                memory_ids,
            )
        except Exception as exc:
            log(f"FTS 索引删除失败: {exc}", "DEBUG")
