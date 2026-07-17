"""MemoryStore：基于 SQLite FTS5 + Embedding 的统一记忆存储。

支持混合评分管线：语义评分 (Vector + FTS + TagMatch) × 衰减评分 (Recency + Frequency + Importance)。
新增文件索引体系：memory.md + memory/*.md 分块索引，双轨统一搜索。
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from typing import Any, Dict, List, Optional

import aiosqlite

from core.entity import BaseEntity, EntityType
from core.log import log
from .memory_types import MemoryEntry, MemorySearchResult, MemoryType
from .memory_utils import cosine_similarity, pack_embedding, unpack_embedding

_HALF_LIFE_HOURS = 24 * 30  # 30 天半衰期

# 混合评分权重
_W_SEMANTIC = 0.7
_W_DECAY = 0.3
_W_VEC = 0.6
_W_FTS = 0.25
_W_TAG = 0.15
_W_RECENCY = 0.5
_W_FREQUENCY = 0.3
_W_IMPORTANCE = 0.2

# memories 表显式列名（避免 SELECT * 对顺序的依赖）
_MEM_COLUMNS = (
    "id, type, content, source, importance, ts_ns, "
    "metadata_json, embedding_blob, tags_json, access_count, last_accessed_ns, migrated"
)


def _time_decay(ts: float, half_life_hours: Optional[float] = None) -> float:
    """基于时间的衰减因子，越新越接近 1。"""
    if half_life_hours is None:
        days = float(_get_memory_config_value("memory_time_decay_days", 30))
        half_life_hours = max(1.0, days * 24)
    age_hours = (time.time() - ts) / 3600.0
    return 0.5 ** (age_hours / half_life_hours)


def _tag_match_score(query_tags: list[str], memory_tags: list[str]) -> float:
    """标签匹配得分：查询标签在记忆标签中的命中比例。"""
    if not query_tags or not memory_tags:
        return 0.0
    hits = sum(1 for t in query_tags if t in memory_tags)
    return hits / len(query_tags)


def _frequency_boost(access_count: int, max_access: int) -> float:
    """访问频率归一化得分。"""
    if max_access <= 0:
        return 0.0
    return math.log(1 + access_count) / math.log(1 + max_access)


_DATED_PATH_RE = re.compile(r"(?:^|/)memory/(\d{4})-(\d{2})-(\d{2})\.md$")
_HALF_LIFE_DAYS = 30


def _file_temporal_decay(path: str) -> float:
    """文件级时间衰减：memory.md 等常青文件不衰减，memory/YYYY-MM-DD.md 按日期衰减。"""
    normalized = path.replace("\\", "/").lstrip("./")
    if normalized in ("MEMORY.md", "memory.md"):
        return 1.0
    if normalized.startswith("memory/") and not _DATED_PATH_RE.search(normalized):
        return 1.0
    m = _DATED_PATH_RE.search(normalized)
    if m:
        try:
            from datetime import datetime, timezone
            file_date = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            age_days = (datetime.now(tz=timezone.utc) - file_date).total_seconds() / 86400
            return 0.5 ** (age_days / _HALF_LIFE_DAYS)
        except (ValueError, OverflowError):
            pass
    return 1.0


def _get_memory_config_value(field: str, default: Any = None) -> Any:
    """从 MindConfig 安全读取配置值。"""
    try:
        from agent.config import get_config_provider
        return getattr(get_config_provider().mind, field, default)
    except Exception:
        return default


class MemoryStore(BaseEntity):
    """SQLite 记忆存储，支持 FTS5 全文检索、向量相似度搜索和标签索引。"""

    _entity_type = EntityType.DATABASE
    _entity_description = "记忆存储 — 基于 SQLite FTS5 + Embedding 的统一记忆系统"

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._initialized = False
        self._fts_available = False
        self._chunks_fts_available = False
        self._cognee_projection_enabled = False
        super().__init__()

    def set_cognee_projection_enabled(self, enabled: bool) -> None:
        """启用或禁用 Cognee 持久化投影队列。"""
        self._cognee_projection_enabled = enabled

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is not None:
            try:
                await self._db.execute("SELECT 1")
                return self._db
            except Exception:
                try:
                    await self._db.close()
                except Exception:
                    pass
                self._db = None

        from pathlib import Path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        db = await aiosqlite.connect(self._db_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("PRAGMA busy_timeout=5000;")

        if not self._initialized:
            await self._init_schema(db)
            self._initialized = True

        self._db = db
        return db

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
        self._chunks_fts_available = False
        try:
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(content, content='memories', content_rowid='id',
                           tokenize='unicode61 remove_diacritics 2');
            """)
            self._fts_available = True
            # 创建触发器保持 FTS 索引自动同步
            await self._create_fts_triggers(db)
            await self._sync_fts_index(db)
        except Exception as exc:
            log(f"FTS5 不可用，降级为纯 SQL LIKE 搜索: {exc}", "WARNING")
            self._fts_available = False

        try:
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(text, id UNINDEXED, path UNINDEXED,
                           start_line UNINDEXED, end_line UNINDEXED,
                           tokenize='unicode61 remove_diacritics 2');
            """)
            self._chunks_fts_available = True
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

        await db.commit()

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

    async def backfill_embeddings(self, embedder: Any, batch_size: int = 2) -> int:
        """渐进式为缺少 embedding 的记忆补充向量。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, content FROM memories WHERE embedding_blob IS NULL LIMIT ?",
            (batch_size,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0

        count = 0
        for row in rows:
            try:
                vec = await embedder.embed_one(row["content"])
                if vec:
                    blob = pack_embedding(vec)
                    await db.execute(
                        "UPDATE memories SET embedding_blob=? WHERE id=?",
                        (blob, row["id"]),
                    )
                    count += 1
            except Exception:
                continue
        if count:
            await db.commit()
            log(f"Embedding 渐进回填: {count} 条", "DEBUG", tag="思维")
        return count

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _entry_projection_payload(entry: MemoryEntry, memory_id: int) -> Dict[str, Any]:
        return {
            "memory_id": memory_id,
            "type": entry.memory_type.value,
            "content": entry.content,
            "source": entry.source,
            "importance": entry.importance,
            "timestamp": entry.timestamp,
            "metadata": entry.metadata,
            "tags": entry.tags,
        }

    async def _enqueue_cognee_sync(
        self,
        db: aiosqlite.Connection,
        memory_id: int,
        operation: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """在当前事务中追加 Cognee 投影操作，并压缩尚未执行的旧操作。"""
        if not self._cognee_projection_enabled or memory_id <= 0:
            return
        now_ns = time.time_ns()
        await db.execute(
            "DELETE FROM cognee_sync_queue "
            "WHERE memory_id=? AND status IN ('pending', 'failed')",
            (memory_id,),
        )
        await db.execute(
            "INSERT INTO cognee_sync_queue"
            "(memory_id, operation, payload_json, status, attempts, next_retry_ns, "
            "last_error, created_ns, updated_ns) VALUES(?,?,?,'pending',0,0,'',?,?)",
            (
                memory_id,
                operation,
                json.dumps(payload or {}, ensure_ascii=False),
                now_ns,
                now_ns,
            ),
        )

    async def add(self, entry: MemoryEntry) -> int:
        """添加一条记忆，返回 id。"""
        db = await self._get_db()
        ts_ns = int(entry.timestamp * 1e9) if entry.timestamp else int(time.time() * 1e9)
        blob = pack_embedding(entry.embedding) if entry.embedding else None
        tags_json = json.dumps(entry.tags, ensure_ascii=False)
        cursor = await db.execute(
            "INSERT INTO memories"
            "(type, content, source, importance, ts_ns, metadata_json, embedding_blob, tags_json, access_count, last_accessed_ns) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                entry.memory_type.value,
                entry.content,
                entry.source,
                entry.importance,
                ts_ns,
                json.dumps(entry.metadata, ensure_ascii=False),
                blob,
                tags_json,
                entry.access_count,
                int(entry.last_accessed * 1e9),
            ),
        )
        row_id = cursor.lastrowid or 0
        await self._enqueue_cognee_sync(
            db,
            row_id,
            "upsert",
            self._entry_projection_payload(entry, row_id),
        )
        # FTS 触发器会自动同步，无需手动插入 memories_fts
        await db.commit()
        tag_hint = f" tags={entry.tags}" if entry.tags else ""
        log(f"📝 记忆写入 [{entry.memory_type.value}] id={row_id}{tag_hint}: {entry.content[:50]}", tag="思维")
        return row_id

    async def get(self, memory_id: int) -> Optional[MemoryEntry]:
        db = await self._get_db()
        cursor = await db.execute(f"SELECT {_MEM_COLUMNS} FROM memories WHERE id=?", (memory_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_entry(row)

    async def update_importance(self, memory_id: int, importance: float) -> None:
        db = await self._get_db()
        await db.execute("UPDATE memories SET importance=? WHERE id=?", (importance, memory_id))
        entry = await self.get(memory_id)
        if entry:
            await self._enqueue_cognee_sync(
                db, memory_id, "upsert", self._entry_projection_payload(entry, memory_id),
            )
        await db.commit()

    async def update(self, entry: MemoryEntry) -> bool:
        """原地更新一条记忆的内容、标签、embedding 等字段（保留原 id 和时间戳）。"""
        if not entry.id:
            return False
        db = await self._get_db()
        blob = pack_embedding(entry.embedding) if entry.embedding else None
        tags_json = json.dumps(entry.tags, ensure_ascii=False)
        cursor = await db.execute(
            "UPDATE memories SET content=?, importance=?, metadata_json=?, "
            "embedding_blob=?, tags_json=? WHERE id=?",
            (
                entry.content,
                entry.importance,
                json.dumps(entry.metadata, ensure_ascii=False),
                blob,
                tags_json,
                entry.id,
            ),
        )
        if (cursor.rowcount or 0) > 0:
            await self._enqueue_cognee_sync(
                db,
                entry.id,
                "upsert",
                self._entry_projection_payload(entry, entry.id),
            )
        # FTS 触发器会自动处理 UPDATE OF content
        await db.commit()
        updated = (cursor.rowcount or 0) > 0
        if updated:
            log(f"📝 记忆更新 [{entry.memory_type.value}] id={entry.id}: {entry.content[:50]}", tag="思维")
        return updated

    async def delete(self, memory_id: int) -> bool:
        db = await self._get_db()
        cursor = await db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        if (cursor.rowcount or 0) > 0:
            await self._enqueue_cognee_sync(db, memory_id, "delete")
        # FTS 触发器会自动处理 DELETE
        await db.commit()
        return (cursor.rowcount or 0) > 0

    async def clear(
        self,
        memory_type: Optional[MemoryType] = None,
        include_permanent: bool = False,
    ) -> int:
        """清除记忆。默认跳过 permanent 类型。"""
        db = await self._get_db()
        select_sql = "SELECT id FROM memories"
        select_params: tuple[Any, ...] = ()
        if memory_type:
            select_sql += " WHERE type=?"
            select_params = (memory_type.value,)
            delete_sql = "DELETE FROM memories WHERE type=?"
            delete_params = select_params
        elif include_permanent:
            delete_sql = "DELETE FROM memories"
            delete_params = ()
        else:
            select_sql += " WHERE type != ?"
            select_params = (MemoryType.PERMANENT.value,)
            delete_sql = "DELETE FROM memories WHERE type != ?"
            delete_params = select_params
        ids_cursor = await db.execute(select_sql, select_params)
        memory_ids = [int(row["id"]) for row in await ids_cursor.fetchall()]
        cursor = await db.execute(delete_sql, delete_params)
        for memory_id in memory_ids:
            await self._enqueue_cognee_sync(db, memory_id, "delete")
        # FTS 触发器会自动同步删除
        await db.commit()
        return cursor.rowcount or 0

    # ------------------------------------------------------------------
    # Cognee 投影队列
    # ------------------------------------------------------------------

    async def claim_cognee_sync_batch(self, limit: int) -> list[Dict[str, Any]]:
        """领取一批可执行投影任务，避免同进程重复消费。"""
        db = await self._get_db()
        now_ns = time.time_ns()
        cursor = await db.execute(
            "SELECT id, memory_id, operation, payload_json, attempts "
            "FROM cognee_sync_queue "
            "WHERE status='pending' AND next_retry_ns<=? ORDER BY id LIMIT ?",
            (now_ns, max(1, limit)),
        )
        rows = await cursor.fetchall()
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in ids)
        await db.execute(
            f"UPDATE cognee_sync_queue SET status='processing', updated_ns=? "
            f"WHERE id IN ({placeholders})",
            (now_ns, *ids),
        )
        await db.commit()
        result: list[Dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            result.append({
                "queue_id": int(row["id"]),
                "memory_id": int(row["memory_id"]),
                "operation": str(row["operation"]),
                "payload": payload,
                "attempts": int(row["attempts"]),
            })
        return result

    async def complete_cognee_sync(
        self,
        queue_id: int,
        memory_id: int,
        *,
        dataset_name: str = "",
        dataset_id: str = "",
        data_id: str = "",
        delete_mapping: bool = False,
    ) -> None:
        db = await self._get_db()
        if delete_mapping:
            await db.execute(
                "DELETE FROM cognee_memory_map WHERE memory_id=?", (memory_id,),
            )
        elif dataset_name:
            await db.execute(
                "INSERT OR REPLACE INTO cognee_memory_map"
                "(memory_id, dataset_name, dataset_id, data_id, synced_ns) "
                "VALUES(?,?,?,?,?)",
                (memory_id, dataset_name, dataset_id, data_id, time.time_ns()),
            )
        await db.execute("DELETE FROM cognee_sync_queue WHERE id=?", (queue_id,))
        await db.commit()

    async def fail_cognee_sync(
        self,
        queue_id: int,
        error: str,
        *,
        max_retries: int,
        retry_delay_seconds: float,
    ) -> None:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT attempts FROM cognee_sync_queue WHERE id=?", (queue_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return
        attempts = int(row["attempts"]) + 1
        status = "failed" if attempts >= max_retries else "pending"
        next_retry_ns = time.time_ns() + int(max(0.5, retry_delay_seconds) * 1e9)
        await db.execute(
            "UPDATE cognee_sync_queue SET status=?, attempts=?, next_retry_ns=?, "
            "last_error=?, updated_ns=? WHERE id=?",
            (status, attempts, next_retry_ns, error[:1000], time.time_ns(), queue_id),
        )
        await db.commit()

    async def get_cognee_mapping(self, memory_id: int) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT memory_id, dataset_name, dataset_id, data_id, synced_ns "
            "FROM cognee_memory_map WHERE memory_id=?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_cognee_sync_status(self) -> Dict[str, int]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT status, COUNT(*) AS cnt FROM cognee_sync_queue GROUP BY status"
        )
        counts = {str(row["status"]): int(row["cnt"]) for row in await cursor.fetchall()}
        mapped = await db.execute("SELECT COUNT(*) AS cnt FROM cognee_memory_map")
        mapped_row = await mapped.fetchone()
        return {
            "pending": counts.get("pending", 0) + counts.get("processing", 0),
            "failed": counts.get("failed", 0),
            "synced": int(mapped_row["cnt"]) if mapped_row else 0,
        }

    async def retry_failed_cognee_sync(self) -> int:
        db = await self._get_db()
        cursor = await db.execute(
            "UPDATE cognee_sync_queue SET status='pending', attempts=0, "
            "next_retry_ns=0, last_error='', updated_ns=? WHERE status='failed'",
            (time.time_ns(),),
        )
        await db.commit()
        return cursor.rowcount or 0

    async def enqueue_cognee_backfill(self, *, limit: int = 0) -> int:
        """显式将历史记忆加入投影队列；不会在启动时自动调用。"""
        if not self._cognee_projection_enabled:
            return 0
        db = await self._get_db()
        sql = f"SELECT {_MEM_COLUMNS} FROM memories ORDER BY id"
        params: tuple[Any, ...] = ()
        if limit > 0:
            sql += " LIMIT ?"
            params = (limit,)
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.id:
                await self._enqueue_cognee_sync(
                    db,
                    entry.id,
                    "upsert",
                    self._entry_projection_payload(entry, entry.id),
                )
        await db.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # 访问跟踪（隐式反馈回路）
    # ------------------------------------------------------------------

    async def record_access(self, memory_ids: list[int]) -> None:
        """批量记录记忆被访问（递增 access_count，更新 last_accessed）。"""
        if not memory_ids:
            return
        db = await self._get_db()
        now_ns = int(time.time() * 1e9)
        for mid in memory_ids:
            await db.execute(
                "UPDATE memories SET access_count = access_count + 1, last_accessed_ns = ? WHERE id = ?",
                (now_ns, mid),
            )
        await db.commit()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def list_recent(
        self,
        limit: int = 20,
        memory_type: Optional[MemoryType] = None,
        source: Optional[str] = None,
    ) -> list[MemoryEntry]:
        db = await self._get_db()
        sql = f"SELECT {_MEM_COLUMNS} FROM memories WHERE 1=1"
        params: list[Any] = []
        if memory_type:
            sql += " AND type=?"
            params.append(memory_type.value)
        if source is not None:
            sql += " AND source=?"
            params.append(source)
        sql += " ORDER BY ts_ns DESC LIMIT ?"
        params.append(limit)
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [self._row_to_entry(r) for r in reversed(rows)]

    async def count(self, memory_type: Optional[MemoryType] = None) -> int:
        db = await self._get_db()
        if memory_type:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM memories WHERE type=?", (memory_type.value,))
        else:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM memories")
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # 标签索引
    # ------------------------------------------------------------------

    async def list_tags(self) -> Dict[str, int]:
        """聚合所有标签及其出现次数。"""
        db = await self._get_db()
        cursor = await db.execute("SELECT tags_json FROM memories")
        rows = await cursor.fetchall()
        tag_counts: Dict[str, int] = {}
        for row in rows:
            try:
                tags = json.loads(row["tags_json"]) if row["tags_json"] else []
            except json.JSONDecodeError:
                continue
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        return tag_counts

    async def search_by_tags(
        self,
        tags: list[str],
        limit: int = 20,
    ) -> list[MemoryEntry]:
        """按标签交集筛选记忆（返回包含所有指定标签的记忆）。"""
        db = await self._get_db()
        conditions = " AND ".join("tags_json LIKE ?" for _ in tags)
        params: list[Any] = [f'%"{t}"%' for t in tags]
        params.append(limit)
        cursor = await db.execute(
            f"SELECT {_MEM_COLUMNS} FROM memories WHERE {conditions} ORDER BY ts_ns DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        return [self._row_to_entry(r) for r in rows]

    # ------------------------------------------------------------------
    # FTS5 全文检索
    # ------------------------------------------------------------------

    async def search_fts(self, query: str, limit: int = 10) -> list[tuple[MemoryEntry, float]]:
        """FTS5 关键词搜索，返回 (entry, bm25_score) 列表。"""
        db = await self._get_db()
        if not self._fts_available:
            return await self._search_like(query, limit)

        fts_query = self._build_fts_query(query)
        if not fts_query:
            return []

        try:
            fts_select = ", ".join(f"m.{c.strip()}" for c in _MEM_COLUMNS.split(","))
            cursor = await db.execute(
                f"""
                SELECT {fts_select}, rank
                FROM memories_fts f
                JOIN memories m ON m.id = f.rowid
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit),
            )
            rows = await cursor.fetchall()
            results: list[tuple[MemoryEntry, float]] = []
            for row in rows:
                entry = self._row_to_entry(row)
                bm25_rank = row["rank"]
                score = 1.0 / (1.0 + abs(bm25_rank))
                results.append((entry, score))
            return results
        except Exception as exc:
            log(f"FTS 搜索异常，回退 LIKE: {exc}", "DEBUG")
            return await self._search_like(query, limit)

    async def _search_like(self, query: str, limit: int) -> list[tuple[MemoryEntry, float]]:
        """LIKE 回退搜索：将查询拆成关键词段，用 OR 匹配。"""
        db = await self._get_db()
        keywords = self._extract_like_keywords(query)
        if not keywords:
            return []
        conditions = " OR ".join("content LIKE ?" for _ in keywords)
        params: list[Any] = [f"%{kw}%" for kw in keywords]
        params.append(limit)
        cursor = await db.execute(
            f"SELECT {_MEM_COLUMNS} FROM memories WHERE ({conditions}) ORDER BY ts_ns DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        return [(self._row_to_entry(r), 0.5) for r in rows]

    @staticmethod
    def _extract_like_keywords(query: str) -> list[str]:
        """从查询中提取 LIKE 搜索关键词（中文按 2-4 字滑窗，英文按空格分词）。"""
        keywords: list[str] = []
        for word in query.split():
            cjk_chars = [ch for ch in word if '\u4e00' <= ch <= '\u9fff']
            if len(cjk_chars) >= 2:
                # 2-4 字滑窗，优先长片段
                step = 2 if len(cjk_chars) <= 4 else 3
                for i in range(0, len(cjk_chars) - step + 1, step):
                    kw = "".join(cjk_chars[i:i + min(step + 1, len(cjk_chars) - i)])
                    keywords.append(kw)
            elif len(word) >= 2:
                keywords.append(word)
        return keywords[:10]

    # ------------------------------------------------------------------
    # 向量相似度搜索（支持分批并行）
    # ------------------------------------------------------------------

    async def search_vector(
        self,
        query_vec: list[float],
        limit: int = 10,
        min_score: float = 0.3,
    ) -> list[tuple[MemoryEntry, float]]:
        """向量搜索，超过阈值时分批加载并行计算。"""
        db = await self._get_db()
        batch_size: int = _get_memory_config_value("vector_search_batch_size", 500)

        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM memories WHERE embedding_blob IS NOT NULL"
        )
        total_row = await cursor.fetchone()
        total = total_row["cnt"] if total_row else 0

        if total == 0:
            return []

        if total <= batch_size:
            return await self._search_vector_batch(query_vec, 0, total, min_score, limit)

        # 分批并行计算
        tasks = [
            self._search_vector_batch(query_vec, offset, batch_size, min_score, limit)
            for offset in range(0, total, batch_size)
        ]
        batch_results = await asyncio.gather(*tasks)

        merged: list[tuple[MemoryEntry, float]] = []
        for batch in batch_results:
            merged.extend(batch)
        merged.sort(key=lambda x: x[1], reverse=True)
        return merged[:limit]

    async def _search_vector_batch(
        self,
        query_vec: list[float],
        offset: int,
        batch_size: int,
        min_score: float,
        limit: int,
    ) -> list[tuple[MemoryEntry, float]]:
        """加载一个批次的记忆并计算向量相似度。"""
        db = await self._get_db()
        cursor = await db.execute(
            f"SELECT {_MEM_COLUMNS} FROM memories WHERE embedding_blob IS NOT NULL "
            "ORDER BY ts_ns DESC LIMIT ? OFFSET ?",
            (batch_size, offset),
        )
        rows = await cursor.fetchall()

        scored: list[tuple[MemoryEntry, float]] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.embedding:
                score = cosine_similarity(query_vec, entry.embedding)
                if score >= min_score:
                    scored.append((entry, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    # ------------------------------------------------------------------
    # 混合搜索（三路并行评分管线）
    # ------------------------------------------------------------------

    async def search_hybrid(
        self,
        query: str,
        query_vec: Optional[list[float]] = None,
        query_tags: Optional[list[str]] = None,
        limit: int = 10,
        min_score: float = 0.1,
    ) -> list[tuple[MemoryEntry, float]]:
        """混合搜索：向量 + FTS + LIKE 三路并行，取并集后统一评分。"""
        pool_size = limit * 5

        async def _empty_vec() -> list[tuple[MemoryEntry, float]]:
            return []

        # 三路并行搜索
        vec_coro = self.search_vector(query_vec, limit=pool_size, min_score=0.05) if query_vec else _empty_vec()
        fts_coro = self.search_fts(query, limit=pool_size)
        like_coro = self._search_like(query, pool_size)

        vec_results, fts_results, like_results = await asyncio.gather(
            vec_coro, fts_coro, like_coro
        )

        all_results = fts_results + like_results + vec_results
        max_access = max((e.access_count for e, _ in all_results), default=0)

        # 合并候选（三路去重）
        candidates: Dict[int, tuple[MemoryEntry, float, float, float]] = {}

        for entry, fts_score in fts_results:
            eid = entry.id or 0
            candidates[eid] = (entry, 0.0, fts_score, 0.0)

        for entry, like_score in like_results:
            eid = entry.id or 0
            if eid in candidates:
                e, vs, fs, _ = candidates[eid]
                candidates[eid] = (e, vs, fs, like_score)
            else:
                candidates[eid] = (entry, 0.0, 0.0, like_score)

        for entry, vec_score in vec_results:
            eid = entry.id or 0
            if eid in candidates:
                e, _, fs, ls = candidates[eid]
                candidates[eid] = (e, vec_score, fs, ls)
            else:
                candidates[eid] = (entry, vec_score, 0.0, 0.0)

        q_tags = query_tags or []
        results: list[tuple[MemoryEntry, float]] = []

        for eid, (entry, vec_score, fts_score, like_score) in candidates.items():
            tag_score = _tag_match_score(q_tags, entry.tags) if q_tags else 0.0
            text_score = max(fts_score, like_score)
            semantic = vec_score * _W_VEC + text_score * _W_FTS + tag_score * _W_TAG

            recency = _time_decay(entry.timestamp)
            freq = _frequency_boost(entry.access_count, max_access)
            decay = recency * _W_RECENCY + freq * _W_FREQUENCY + entry.importance * _W_IMPORTANCE

            final = semantic * _W_SEMANTIC + decay * _W_DECAY
            if final >= min_score:
                results.append((entry, final))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    # ------------------------------------------------------------------
    # 统一搜索（memories + chunks 双轨并行）
    # ------------------------------------------------------------------

    async def search_unified(
        self,
        query: str,
        query_vec: Optional[list[float]] = None,
        query_tags: Optional[list[str]] = None,
        limit: int = 10,
        min_score: float = 0.1,
    ) -> list[MemorySearchResult]:
        """统一搜索：同时检索 memories 表和 chunks 表，合并排序返回。"""
        pool_size = limit * 3

        # memories 搜索 + chunks 三路搜索并行执行
        async def _empty_chunk_vec() -> list[Dict[str, Any]]:
            return []

        mem_coro = self.search_hybrid(
            query=query, query_vec=query_vec, query_tags=query_tags,
            limit=pool_size, min_score=min_score,
        )
        chunk_vec_coro = self.search_chunks_vector(query_vec, limit=pool_size, min_score=0.05) if query_vec else _empty_chunk_vec()
        chunk_fts_coro = self.search_chunks_fts(query, limit=pool_size)
        chunk_like_coro = self._search_chunks_like(query, pool_size)

        mem_results, chunk_vec_results, chunk_fts_results, chunk_like_results = await asyncio.gather(
            mem_coro, chunk_vec_coro, chunk_fts_coro, chunk_like_coro
        )

        # 合并 chunks 候选
        chunk_candidates: Dict[str, Dict[str, Any]] = {}
        for r in chunk_fts_results + chunk_like_results:
            cid = r["id"]
            if cid not in chunk_candidates or r["score"] > chunk_candidates[cid].get("text_score", 0):
                chunk_candidates[cid] = {**r, "text_score": r["score"], "vec_score": 0.0}
        for r in chunk_vec_results:
            cid = r["id"]
            if cid in chunk_candidates:
                chunk_candidates[cid]["vec_score"] = r["score"]
            else:
                chunk_candidates[cid] = {**r, "text_score": 0.0, "vec_score": r["score"]}

        chunk_results: list[MemorySearchResult] = []
        for cid, ch in chunk_candidates.items():
            semantic = ch["vec_score"] * _W_VEC + ch["text_score"] * (_W_FTS + _W_TAG)
            decay_mult = _file_temporal_decay(ch.get("path", ""))
            final = semantic * decay_mult
            if final >= min_score:
                chunk_results.append(MemorySearchResult(
                    id=cid,
                    path=ch.get("path", ""),
                    start_line=ch.get("start_line", 0),
                    end_line=ch.get("end_line", 0),
                    snippet=ch.get("snippet", "")[:700],
                    score=final,
                    source="file",
                ))

        unified: list[MemorySearchResult] = []
        for entry, score in mem_results:
            unified.append(MemorySearchResult(
                id=f"mem:{entry.id}",
                snippet=entry.content[:700],
                score=score,
                source="memory",
                memory_type=entry.memory_type.value,
                tags=entry.tags,
            ))

        unified.extend(chunk_results)
        unified.sort(key=lambda r: r.score, reverse=True)
        return unified[:limit]

    # ------------------------------------------------------------------
    # 去重
    # ------------------------------------------------------------------

    async def has_similar_content(self, content: str, min_overlap: float = 0.6) -> bool:
        """检查是否已存在语义相近的记忆（基于 FTS 候选 + bigram 相似度）。"""
        results = await self.search_fts(content, limit=5)
        content_clean = content.replace(" ", "").replace("\n", "")
        for entry, _ in results:
            existing_clean = entry.content.replace(" ", "").replace("\n", "")
            if content_clean in existing_clean or existing_clean in content_clean:
                return True
            if self._bigram_similarity(content_clean, existing_clean) >= min_overlap:
                return True
        return False

    @staticmethod
    def _bigram_similarity(a: str, b: str) -> float:
        if len(a) < 2 or len(b) < 2:
            return 0.0
        bigrams_a = {a[i:i + 2] for i in range(len(a) - 1)}
        bigrams_b = {b[i:i + 2] for i in range(len(b) - 1)}
        intersection = bigrams_a & bigrams_b
        union = bigrams_a | bigrams_b
        return len(intersection) / len(union) if union else 0.0

    # ------------------------------------------------------------------
    # 管理接口
    # ------------------------------------------------------------------

    async def list_all_with_id(
        self,
        memory_type: Optional[MemoryType] = None,
        limit: int = 200,
    ) -> list[Dict[str, Any]]:
        db = await self._get_db()
        sql = "SELECT id, type, content, source, importance, ts_ns, metadata_json, tags_json, access_count, last_accessed_ns FROM memories WHERE 1=1"
        params: list[Any] = []
        if memory_type:
            sql += " AND type=?"
            params.append(memory_type.value)
        sql += " ORDER BY ts_ns DESC LIMIT ?"
        params.append(limit)
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"], "type": r["type"], "content": r["content"],
                "source": r["source"], "importance": r["importance"], "ts_ns": r["ts_ns"],
                "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else {},
                "tags": json.loads(r["tags_json"]) if r["tags_json"] else [],
                "access_count": r["access_count"],
                "last_accessed_ns": r["last_accessed_ns"],
            }
            for r in rows
        ]

    async def list_paginated(
        self,
        page: int = 1,
        page_size: int = 50,
        memory_type: Optional[MemoryType] = None,
    ) -> Dict[str, Any]:
        """分页查询记忆，返回 {items, total, page, page_size, pages}。"""
        db = await self._get_db()
        total = await self.count(memory_type)
        offset = (max(1, page) - 1) * page_size

        sql = f"SELECT {_MEM_COLUMNS} FROM memories WHERE 1=1"
        params: list[Any] = []
        if memory_type:
            sql += " AND type=?"
            params.append(memory_type.value)
        sql += " ORDER BY ts_ns DESC LIMIT ? OFFSET ?"
        params.extend([page_size, offset])

        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()

        items = []
        for r in rows:
            entry = self._row_to_entry(r)
            items.append({
                "id": entry.id, "type": entry.memory_type.value,
                "content": entry.content, "source": entry.source,
                "importance": entry.importance, "timestamp": entry.timestamp,
                "tags": entry.tags, "access_count": entry.access_count,
            })

        pages = max(1, math.ceil(total / page_size))
        return {"items": items, "total": total, "page": page, "page_size": page_size, "pages": pages}

    async def get_type_counts(self) -> Dict[str, int]:
        """按类型统计记忆条目数量。"""
        db = await self._get_db()
        cursor = await db.execute("SELECT type, COUNT(*) as cnt FROM memories GROUP BY type")
        rows = await cursor.fetchall()
        return {r["type"]: r["cnt"] for r in rows}

    async def merge_memories(self, ids: list[int], merged_content: str, merged_type: Optional[MemoryType] = None) -> int:
        """将多条记忆合并为一条新记忆，旧记忆标记 importance=0（不删除）。返回新记忆 id。"""
        if not ids or not merged_content:
            return 0

        db = await self._get_db()

        # 获取原记忆信息用于继承标签和类型
        placeholders = ",".join("?" for _ in ids)
        cursor = await db.execute(
            f"SELECT {_MEM_COLUMNS} FROM memories WHERE id IN ({placeholders})",
            ids,
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0

        # 合并标签（取并集）
        all_tags: set[str] = set()
        best_type = merged_type or MemoryType(rows[0]["type"])
        max_importance = 0.0
        for r in rows:
            try:
                tags = json.loads(r["tags_json"]) if r["tags_json"] else []
                all_tags.update(tags)
            except (json.JSONDecodeError, TypeError):
                pass
            max_importance = max(max_importance, r["importance"])

        # 将旧记忆标记为已合并（importance=0）
        await db.execute(
            f"UPDATE memories SET importance = 0 WHERE id IN ({placeholders})",
            ids,
        )

        # 创建合并后的新记忆
        merged_tags = sorted(all_tags)
        merged_tags.append("merged")
        entry = MemoryEntry(
            memory_type=best_type,
            content=merged_content,
            source="merge",
            tags=merged_tags,
            importance=max_importance,
            metadata={"merged_from": ids},
        )
        new_id = await self.add(entry)
        log(f"🔗 记忆合并: {ids} → id={new_id}", tag="思维")
        return new_id

    async def cleanup_low_importance(self, threshold: float = 0.05, max_age_hours: float = 24 * 90) -> int:
        db = await self._get_db()
        cutoff_ts = int((time.time() - max_age_hours * 3600) * 1e9)
        ids_cursor = await db.execute(
            "SELECT id FROM memories WHERE importance < ? AND ts_ns < ? AND type != ?",
            (threshold, cutoff_ts, MemoryType.PERMANENT.value),
        )
        memory_ids = [int(row["id"]) for row in await ids_cursor.fetchall()]
        cursor = await db.execute(
            "DELETE FROM memories WHERE importance < ? AND ts_ns < ? AND type != ?",
            (threshold, cutoff_ts, MemoryType.PERMANENT.value),
        )
        for memory_id in memory_ids:
            await self._enqueue_cognee_sync(db, memory_id, "delete")
        await db.commit()
        return cursor.rowcount or 0

    async def clean_embedding_cache(self) -> int:
        """清理不再被 chunks 引用的过期 embedding 缓存。"""
        db = await self._get_db()
        cursor = await db.execute(
            "DELETE FROM embedding_cache WHERE hash NOT IN "
            "(SELECT DISTINCT hash FROM chunks WHERE hash IS NOT NULL)"
        )
        await db.commit()
        cleaned = cursor.rowcount or 0
        if cleaned:
            log(f"🗑️ 清理 embedding 缓存: {cleaned} 条", tag="思维")
        return cleaned

    async def close(self) -> None:
        if self._db:
            try:
                await self._db.close()
            except Exception:
                pass
            self._db = None

    # ------------------------------------------------------------------
    # 健康状态
    # ------------------------------------------------------------------

    async def get_health_status(self) -> Dict[str, Any]:
        """返回记忆系统综合健康状态。"""
        type_counts = await self.get_type_counts()
        index_status = await self.get_index_status()
        total = sum(type_counts.values())

        warn_threshold: int = _get_memory_config_value("memory_warn_threshold", 200)
        max_per_type: int = _get_memory_config_value("memory_max_per_type", 500)

        warnings: list[str] = []
        for mem_type, cnt in type_counts.items():
            if cnt >= max_per_type:
                warnings.append(f"{mem_type} 已达上限 ({cnt}/{max_per_type})，建议合并")
            elif cnt >= warn_threshold:
                warnings.append(f"{mem_type} 数量较多 ({cnt}/{warn_threshold})，建议整理")

        return {
            "total_memories": total,
            "type_counts": type_counts,
            "warn_threshold": warn_threshold,
            "max_per_type": max_per_type,
            "warnings": warnings,
            "has_warnings": len(warnings) > 0,
            **index_status,
        }

    # ------------------------------------------------------------------
    # 文件索引 CRUD
    # ------------------------------------------------------------------

    async def get_file(self, path: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute("SELECT path, hash, mtime_ns, size FROM files WHERE path=?", (path,))
        row = await cursor.fetchone()
        if not row:
            return None
        return {"path": row["path"], "hash": row["hash"], "mtime_ns": row["mtime_ns"], "size": row["size"]}

    async def upsert_file(self, path: str, hash_val: str, mtime_ns: int, size: int) -> None:
        db = await self._get_db()
        await db.execute(
            "INSERT OR REPLACE INTO files(path, hash, mtime_ns, size) VALUES(?,?,?,?)",
            (path, hash_val, mtime_ns, size),
        )
        await db.commit()

    async def delete_file(self, path: str) -> None:
        """删除文件记录及其所有 chunks。"""
        db = await self._get_db()
        if self._chunks_fts_available:
            try:
                await db.execute("DELETE FROM chunks_fts WHERE path=?", (path,))
            except Exception as e:
                log(f"chunks_fts 删除失败 [{path}]: {e}", "DEBUG")
        await db.execute("DELETE FROM chunks WHERE path=?", (path,))
        await db.execute("DELETE FROM files WHERE path=?", (path,))
        await db.commit()

    async def list_files(self) -> list[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute("SELECT path, hash, mtime_ns, size FROM files ORDER BY path")
        rows = await cursor.fetchall()
        return [{"path": r["path"], "hash": r["hash"], "mtime_ns": r["mtime_ns"], "size": r["size"]} for r in rows]

    # ------------------------------------------------------------------
    # Chunks CRUD
    # ------------------------------------------------------------------

    async def upsert_chunks(self, chunks: list[Dict[str, Any]]) -> int:
        """批量写入 chunks（id 冲突时替换）。返回写入数量。"""
        if not chunks:
            return 0
        db = await self._get_db()
        count = 0
        for ch in chunks:
            await db.execute(
                "INSERT OR REPLACE INTO chunks(id, path, start_line, end_line, hash, text, embedding, updated_ns) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (ch["id"], ch["path"], ch["start_line"], ch["end_line"],
                 ch["hash"], ch["text"], ch.get("embedding"), ch["updated_ns"]),
            )
            if self._chunks_fts_available:
                try:
                    await db.execute("DELETE FROM chunks_fts WHERE id=?", (ch["id"],))
                    await db.execute(
                        "INSERT INTO chunks_fts(id, path, start_line, end_line, text) VALUES(?,?,?,?,?)",
                        (ch["id"], ch["path"], ch["start_line"], ch["end_line"], ch["text"]),
                    )
                except Exception as e:
                    log(f"chunks_fts 同步失败 [{ch['id']}]: {e}", "DEBUG")
            count += 1
        await db.commit()
        return count

    async def delete_chunks_by_path(self, path: str) -> int:
        db = await self._get_db()
        if self._chunks_fts_available:
            try:
                await db.execute("DELETE FROM chunks_fts WHERE path=?", (path,))
            except Exception as e:
                log(f"chunks_fts 路径删除失败 [{path}]: {e}", "DEBUG")
        cursor = await db.execute("DELETE FROM chunks WHERE path=?", (path,))
        await db.commit()
        return cursor.rowcount or 0

    async def search_chunks_vector(
        self,
        query_vec: list[float],
        limit: int = 10,
        min_score: float = 0.3,
    ) -> list[Dict[str, Any]]:
        """在 chunks 表中执行向量搜索（支持分批并行）。"""
        db = await self._get_db()
        batch_size: int = _get_memory_config_value("vector_search_batch_size", 500)

        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM chunks WHERE embedding IS NOT NULL"
        )
        total_row = await cursor.fetchone()
        total = total_row["cnt"] if total_row else 0

        if total == 0:
            return []

        if total <= batch_size:
            return await self._search_chunks_vector_batch(query_vec, 0, total, min_score, limit)

        tasks = [
            self._search_chunks_vector_batch(query_vec, offset, batch_size, min_score, limit)
            for offset in range(0, total, batch_size)
        ]
        batch_results = await asyncio.gather(*tasks)
        merged: list[Dict[str, Any]] = []
        for batch in batch_results:
            merged.extend(batch)
        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:limit]

    async def _search_chunks_vector_batch(
        self,
        query_vec: list[float],
        offset: int,
        batch_size: int,
        min_score: float,
        limit: int,
    ) -> list[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, path, start_line, end_line, text, embedding FROM chunks "
            "WHERE embedding IS NOT NULL LIMIT ? OFFSET ?",
            (batch_size, offset),
        )
        rows = await cursor.fetchall()
        scored: list[Dict[str, Any]] = []
        for row in rows:
            if not row["embedding"]:
                continue
            vec = unpack_embedding(row["embedding"])
            score = cosine_similarity(query_vec, vec)
            if score >= min_score:
                scored.append({
                    "id": row["id"], "path": row["path"],
                    "start_line": row["start_line"], "end_line": row["end_line"],
                    "snippet": row["text"][:700], "score": score,
                    "source": "file",
                })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    async def search_chunks_fts(self, query: str, limit: int = 10) -> list[Dict[str, Any]]:
        """在 chunks_fts 中执行全文搜索。"""
        if not self._chunks_fts_available:
            return await self._search_chunks_like(query, limit)
        fts_query = self._build_fts_query(query)
        if not fts_query:
            return []
        db = await self._get_db()
        try:
            cursor = await db.execute(
                "SELECT c.id, c.path, c.start_line, c.end_line, c.text, rank "
                "FROM chunks_fts f JOIN chunks c ON c.id = f.id "
                "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, limit),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": r["id"], "path": r["path"], "start_line": r["start_line"],
                    "end_line": r["end_line"], "snippet": r["text"][:700],
                    "score": 1.0 / (1.0 + abs(r["rank"])),
                    "source": "file",
                }
                for r in rows
            ]
        except Exception:
            return await self._search_chunks_like(query, limit)

    async def _search_chunks_like(self, query: str, limit: int) -> list[Dict[str, Any]]:
        keywords = self._extract_like_keywords(query)
        if not keywords:
            return []
        db = await self._get_db()
        conditions = " OR ".join("text LIKE ?" for _ in keywords)
        params: list[Any] = [f"%{kw}%" for kw in keywords]
        params.append(limit)
        cursor = await db.execute(
            f"SELECT id, path, start_line, end_line, text FROM chunks WHERE ({conditions}) LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"], "path": r["path"], "start_line": r["start_line"],
                "end_line": r["end_line"], "snippet": r["text"][:700],
                "score": 0.5, "source": "file",
            }
            for r in rows
        ]

    async def get_index_status(self) -> Dict[str, Any]:
        """返回文件索引的统计信息。"""
        db = await self._get_db()
        file_count = (await (await db.execute("SELECT COUNT(*) as cnt FROM files")).fetchone())["cnt"]
        chunk_count = (await (await db.execute("SELECT COUNT(*) as cnt FROM chunks")).fetchone())["cnt"]
        chunk_with_emb = (await (await db.execute(
            "SELECT COUNT(*) as cnt FROM chunks WHERE embedding IS NOT NULL"
        )).fetchone())["cnt"]
        mem_count = (await (await db.execute("SELECT COUNT(*) as cnt FROM memories")).fetchone())["cnt"]
        cache_count = (await (await db.execute("SELECT COUNT(*) as cnt FROM embedding_cache")).fetchone())["cnt"]
        return {
            "files": file_count,
            "chunks": chunk_count,
            "chunks_with_embedding": chunk_with_emb,
            "memories": mem_count,
            "embedding_cache": cache_count,
            "fts_available": self._fts_available,
            "chunks_fts_available": self._chunks_fts_available,
        }

    # ------------------------------------------------------------------
    # Embedding 缓存
    # ------------------------------------------------------------------

    async def get_cached_embedding(self, text_hash: str) -> Optional[list[float]]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT embedding FROM embedding_cache WHERE hash=?", (text_hash,),
        )
        row = await cursor.fetchone()
        if not row or not row["embedding"]:
            return None
        return unpack_embedding(row["embedding"])

    async def put_cached_embedding(self, text_hash: str, vec: list[float]) -> None:
        db = await self._get_db()
        blob = pack_embedding(vec)
        now_ns = int(time.time() * 1e9)
        await db.execute(
            "INSERT OR REPLACE INTO embedding_cache(hash, embedding, dims, updated_ns) VALUES(?,?,?,?)",
            (text_hash, blob, len(vec), now_ns),
        )
        await db.commit()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: Any) -> MemoryEntry:
        """将数据库行转换为 MemoryEntry（按列名访问，不依赖列顺序）。"""
        embedding = unpack_embedding(row["embedding_blob"]) if row["embedding_blob"] else None
        tags: list[str] = []
        access_count = 0
        last_accessed = 0.0
        try:
            tags = json.loads(row["tags_json"]) if row["tags_json"] else []
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
        try:
            access_count = row["access_count"] or 0
        except (TypeError, KeyError):
            pass
        try:
            last_accessed = (row["last_accessed_ns"] or 0) / 1e9
        except (TypeError, KeyError):
            pass

        return MemoryEntry(
            id=row["id"],
            memory_type=MemoryType(row["type"]),
            content=row["content"],
            source=row["source"],
            importance=row["importance"],
            timestamp=row["ts_ns"] / 1e9,
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            embedding=embedding,
            tags=tags,
            access_count=access_count,
            last_accessed=last_accessed,
        )

    @staticmethod
    def _build_fts_query(raw: str) -> Optional[str]:
        """构建 FTS5 查询：中文 bigram 切分 + 英文原词，使用 OR 组合。"""
        raw = raw.strip()
        if not raw:
            return None

        tokens: list[str] = []
        for word in raw.split():
            word = word.replace('"', '""')
            cjk_chars = [ch for ch in word if '\u4e00' <= ch <= '\u9fff']
            if len(cjk_chars) >= 2:
                # bigram 切分中文（跳步=2 减少噪声 token）
                for i in range(0, len(cjk_chars) - 1, 2):
                    end = min(i + 2, len(cjk_chars))
                    if end - i >= 2:
                        tokens.append("".join(cjk_chars[i:end]))
                # 确保尾部不丢失
                if len(cjk_chars) > 2 and len(cjk_chars) % 2 == 1:
                    tokens.append("".join(cjk_chars[-2:]))
            elif len(word) >= 2:
                tokens.append(word)

        if not tokens:
            return None
        seen: set[str] = set()
        unique: list[str] = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return " OR ".join(f'"{t}"' for t in unique)

    # ==================================================================
    # 工具错误追踪
    # ==================================================================

    async def record_tool_error(
        self,
        tool_name: str,
        error_type: str,
        error_msg: str,
        args_json: str = "{}",
        context: str = "",
    ) -> Optional[int]:
        """记录工具执行错误，返回记录 ID。"""
        try:
            db = await self._get_db()
            cursor = await db.execute(
                "INSERT INTO tool_errors (tool_name, error_type, error_msg, args_json, context, ts_ns) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (tool_name, error_type, error_msg[:500], args_json[:500], context[:200],
                 int(time.time() * 1e9)),
            )
            await db.commit()
            return cursor.lastrowid
        except Exception as e:
            log(f"记录工具错误失败: {e}", "DEBUG")
            return None

    async def get_tool_errors(
        self,
        tool_name: str = "",
        limit: int = 20,
        unresolved_only: bool = False,
    ) -> list[Dict[str, Any]]:
        """查询工具错误历史。"""
        db = await self._get_db()
        conditions: list[str] = []
        params: list[Any] = []
        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        if unresolved_only:
            conditions.append("resolved = 0")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        cursor = await db.execute(
            f"SELECT id, tool_name, error_type, error_msg, args_json, context, resolved, ts_ns "
            f"FROM tool_errors{where} ORDER BY ts_ns DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "tool_name": r["tool_name"],
                "error_type": r["error_type"],
                "error_msg": r["error_msg"],
                "args_preview": r["args_json"][:100],
                "context": r["context"],
                "resolved": bool(r["resolved"]),
                "time": r["ts_ns"] / 1e9,
            }
            for r in rows
        ]

    async def get_tool_error_stats(self) -> list[Dict[str, Any]]:
        """按工具名统计错误次数。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT tool_name, COUNT(*) as count, "
            "SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) as unresolved "
            "FROM tool_errors GROUP BY tool_name ORDER BY count DESC"
        )
        rows = await cursor.fetchall()
        return [
            {"tool_name": r["tool_name"], "total": r["count"], "unresolved": r["unresolved"]}
            for r in rows
        ]

    async def resolve_tool_error(self, error_id: int) -> bool:
        """标记工具错误为已解决。"""
        db = await self._get_db()
        cursor = await db.execute(
            "UPDATE tool_errors SET resolved = 1 WHERE id = ?", (error_id,)
        )
        await db.commit()
        return (cursor.rowcount or 0) > 0
