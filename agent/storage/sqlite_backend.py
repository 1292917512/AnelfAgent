from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import aiosqlite

from core.log import log


def default_sqlite_path() -> str:
    env_path = os.getenv("ANELF_BOT_SQLITE_PATH")
    if env_path and env_path.strip():
        return env_path.strip()
    from core.path import project_root
    return str(Path(project_root()) / "config" / "memory" / "data" / "agent.sqlite3")


class SqliteBackend:
    """SQLite 后端（异步，持久连接）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or default_sqlite_path()
        self._db: Optional[aiosqlite.Connection] = None
        self._initialized = False
        self._conv_embed_ready = False
        self._entity_counter_ready = False

    async def _get_db(self) -> aiosqlite.Connection:
        """获取持久连接，首次调用时创建并初始化表结构。"""
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

        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        db = await aiosqlite.connect(self.db_path)
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("PRAGMA busy_timeout=5000;")

        if not self._initialized:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  scope_type TEXT NOT NULL,
                  scope_id TEXT NOT NULL,
                  role TEXT NOT NULL,
                  content TEXT NOT NULL,
                  ts_ns INTEGER NOT NULL
                );
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_scope_ts "
                "ON conversation_messages(scope_type, scope_id, ts_ns);"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_profile (
                  scope_type TEXT NOT NULL,
                  scope_id TEXT NOT NULL,
                  personality TEXT,
                  updated_ts_ns INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(scope_type, scope_id)
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_alias (
                  scope_type TEXT NOT NULL,
                  scope_id TEXT NOT NULL,
                  primary_scope_type TEXT NOT NULL,
                  primary_scope_id TEXT NOT NULL,
                  created_ts_ns INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(scope_type, scope_id)
                );
                """
            )
            await db.commit()
            self._initialized = True

        self._db = db
        return db

    async def close(self) -> None:
        """关闭持久连接。"""
        if self._db:
            try:
                await self._db.close()
            except Exception:
                pass
            self._db = None

    # 兼容旧代码的 _ensure_init
    async def _ensure_init(self) -> None:
        await self._get_db()

    # ------------------------------------------------------------------
    # 会话记录
    # ------------------------------------------------------------------

    async def append_conversation(
        self,
        *,
        scope_type: str,
        scope_id: str,
        role: str,
        content: str,
        ts_ns: Optional[int] = None,
    ) -> None:
        db = await self._get_db()
        ts_ns = ts_ns or time.time_ns()
        await db.execute(
            "INSERT INTO conversation_messages(scope_type, scope_id, role, content, ts_ns) VALUES(?,?,?,?,?)",
            (scope_type, scope_id, role, content, int(ts_ns)),
        )
        await db.commit()

    async def fetch_conversation(
        self, *, scope_type: str, scope_id: str, limit: int
    ) -> list[dict]:
        db = await self._get_db()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT role, content, ts_ns
            FROM conversation_messages
            WHERE scope_type=? AND scope_id=?
            ORDER BY ts_ns DESC
            LIMIT ?
            """,
            (scope_type, scope_id, int(limit)),
        )
        rows = await cursor.fetchall()
        db.row_factory = None
        rows = list(reversed(rows))
        # 角色按存储原样返回（主流 OpenAI 格式：system/user/assistant/tool），
        # 不做 system→assistant 等特殊映射；ts_ns 由调用方用于时序水位，入库时间即消息到达时间
        return [{"role": r["role"], "content": r["content"], "ts_ns": r["ts_ns"]} for r in rows]

    # ------------------------------------------------------------------
    # 实体画像
    # ------------------------------------------------------------------

    async def _ensure_entity_counter_columns(self) -> None:
        """懒迁移：确保 entity_profile 拥有 conv_num / conv_update_num 列。"""
        if self._entity_counter_ready:
            return
        db = await self._get_db()
        cursor = await db.execute("PRAGMA table_info(entity_profile)")
        cols = {row[1] for row in await cursor.fetchall()}
        altered = False
        if "conv_num" not in cols:
            await db.execute("ALTER TABLE entity_profile ADD COLUMN conv_num INTEGER NOT NULL DEFAULT 0")
            altered = True
        if "conv_update_num" not in cols:
            await db.execute("ALTER TABLE entity_profile ADD COLUMN conv_update_num INTEGER NOT NULL DEFAULT 0")
            altered = True
        if altered:
            await db.commit()
        self._entity_counter_ready = True

    async def get_entity_personality(self, *, scope_type: str, scope_id: str) -> Optional[dict]:
        """返回 {personality, conv_num, conv_update_num} 或 None。"""
        await self._ensure_entity_counter_columns()
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT personality, conv_num, conv_update_num FROM entity_profile "
            "WHERE scope_type=? AND scope_id=?",
            (scope_type, scope_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "personality": row[0],
            "conv_num": row[1] or 0,
            "conv_update_num": row[2] or 0,
        }

    async def set_entity_personality(
        self,
        *,
        scope_type: str,
        scope_id: str,
        personality: str,
        conv_num: int = 0,
        conv_update_num: int = 0,
    ) -> None:
        await self._ensure_entity_counter_columns()
        db = await self._get_db()
        now = time.time_ns()
        await db.execute(
            """
            INSERT INTO entity_profile(scope_type, scope_id, personality, updated_ts_ns, conv_num, conv_update_num)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(scope_type, scope_id) DO UPDATE SET
              personality=excluded.personality,
              updated_ts_ns=excluded.updated_ts_ns,
              conv_num=excluded.conv_num,
              conv_update_num=excluded.conv_update_num
            """,
            (scope_type, scope_id, personality, int(now), conv_num, conv_update_num),
        )
        await db.commit()

    async def save_entity_counters(
        self,
        *,
        scope_type: str,
        scope_id: str,
        conv_num: int,
        conv_update_num: int,
    ) -> None:
        """仅更新对话计数（不覆盖 personality），若记录不存在则跳过。"""
        await self._ensure_entity_counter_columns()
        db = await self._get_db()
        await db.execute(
            "UPDATE entity_profile SET conv_num=?, conv_update_num=? "
            "WHERE scope_type=? AND scope_id=?",
            (conv_num, conv_update_num, scope_type, scope_id),
        )
        await db.commit()

    # ------------------------------------------------------------------
    # 记忆管理扩展
    # ------------------------------------------------------------------

    async def list_conversation_scopes(self) -> list[dict]:
        """列出所有会话 scope（去重），返回 [{scope_type, scope_id, count}]。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT scope_type, scope_id, COUNT(*) as cnt "
            "FROM conversation_messages GROUP BY scope_type, scope_id ORDER BY cnt DESC"
        )
        rows = await cursor.fetchall()
        return [{"scope_type": r[0], "scope_id": r[1], "count": r[2]} for r in rows]

    async def fetch_conversation_with_id(
        self, *, scope_type: str, scope_id: str, limit: int = 100
    ) -> list[dict]:
        """获取会话记录（含 row id，用于定向删除）。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, role, content, ts_ns FROM conversation_messages "
            "WHERE scope_type=? AND scope_id=? ORDER BY ts_ns DESC LIMIT ?",
            (scope_type, scope_id, int(limit)),
        )
        rows = await cursor.fetchall()
        rows = list(reversed(rows))
        return [{"id": r[0], "role": r[1], "content": r[2], "ts_ns": r[3]} for r in rows]

    async def _ensure_conv_embedding_column(self) -> None:
        """懒迁移：确保 conversation_messages 拥有 embedding_blob 列。"""
        if self._conv_embed_ready:
            return
        db = await self._get_db()
        cursor = await db.execute("PRAGMA table_info(conversation_messages)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "embedding_blob" not in cols:
            await db.execute(
                "ALTER TABLE conversation_messages ADD COLUMN embedding_blob BLOB"
            )
            await db.commit()
        self._conv_embed_ready = True

    async def backfill_conversation_embeddings(
        self,
        embedder: object,
        *,
        scope_type: str = "",
        scope_id: str = "",
        batch_size: int = 20,
    ) -> int:
        """为对话记录中缺少 embedding 的条目批量补充向量。

        仅处理 user/assistant 角色的消息（系统消息不需要检索），
        支持限定 scope 加速局部回填。
        """
        await self._ensure_conv_embedding_column()
        db = await self._get_db()

        if scope_type and scope_id:
            cursor = await db.execute(
                "SELECT id, content FROM conversation_messages "
                "WHERE scope_type=? AND scope_id=? AND embedding_blob IS NULL "
                "AND role IN ('user','assistant') LIMIT ?",
                (scope_type, scope_id, batch_size),
            )
        else:
            cursor = await db.execute(
                "SELECT id, content FROM conversation_messages "
                "WHERE embedding_blob IS NULL AND role IN ('user','assistant') LIMIT ?",
                (batch_size,),
            )
        rows = await cursor.fetchall()
        if not rows:
            return 0

        from agent.memory.memory_utils import pack_embedding

        count = 0
        for row in rows:
            try:
                vec = await embedder.embed_one(row[1])  # type: ignore[attr-defined]
                if vec:
                    await db.execute(
                        "UPDATE conversation_messages SET embedding_blob=? WHERE id=?",
                        (pack_embedding(vec), row[0]),
                    )
                    count += 1
            except Exception as e:
                log(f"对话 embedding 生成失败 (id={row[0]}): {e}", "DEBUG")
                continue
        if count:
            await db.commit()
            log(f"对话 embedding 回填: {count} 条", "DEBUG", tag="存储")
        return count

    async def search_conversation_vector(
        self,
        scope_type: str,
        scope_id: str,
        query_vec: list[float],
        *,
        limit: int = 5,
        skip_recent: int = 30,
        min_score: float = 0.25,
        scan_limit: int = 500,
    ) -> list[dict]:
        """向量语义搜索历史对话（跳过最近 skip_recent 条避免与上下文窗口重叠）。

        返回列表按相关度降序排列，每项包含 id / role / content / ts_ns / score。
        """
        await self._ensure_conv_embedding_column()
        db = await self._get_db()

        # 计算时间截断点：第 skip_recent 条的 ts_ns
        cutoff_cursor = await db.execute(
            "SELECT ts_ns FROM conversation_messages "
            "WHERE scope_type=? AND scope_id=? ORDER BY ts_ns DESC LIMIT 1 OFFSET ?",
            (scope_type, scope_id, skip_recent),
        )
        cutoff_row = await cutoff_cursor.fetchone()
        if not cutoff_row:
            return []  # 历史消息不足 skip_recent 条，无需深度检索
        cutoff_ts: int = cutoff_row[0]

        # 加载截断点之前有 embedding 的消息（scan_limit 防止内存压力）
        rows_cursor = await db.execute(
            "SELECT id, role, content, ts_ns, embedding_blob FROM conversation_messages "
            "WHERE scope_type=? AND scope_id=? AND ts_ns<=? AND embedding_blob IS NOT NULL "
            "AND role IN ('user','assistant') ORDER BY ts_ns DESC LIMIT ?",
            (scope_type, scope_id, cutoff_ts, scan_limit),
        )
        rows = await rows_cursor.fetchall()
        if not rows:
            return []

        from agent.memory.memory_utils import cosine_similarity, unpack_embedding

        scored: list[dict] = []
        for row in rows:
            try:
                vec = unpack_embedding(row[4])
                score = cosine_similarity(query_vec, vec)
                if score >= min_score:
                    scored.append({
                        "id": row[0],
                        "role": row[1],
                        "content": row[2],
                        "ts_ns": row[3],
                        "score": score,
                    })
            except Exception as e:
                log(f"对话向量解析失败 (id={row[0]}): {e}", "DEBUG")
                continue

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    async def search_conversation_keyword(
        self,
        scope_type: str,
        scope_id: str,
        keywords: list[str],
        *,
        limit: int = 5,
        skip_recent: int = 30,
    ) -> list[dict]:
        """关键词搜索历史对话（无 embedding 时的 fallback）。"""
        if not keywords:
            return []
        db = await self._get_db()

        cutoff_cursor = await db.execute(
            "SELECT ts_ns FROM conversation_messages "
            "WHERE scope_type=? AND scope_id=? ORDER BY ts_ns DESC LIMIT 1 OFFSET ?",
            (scope_type, scope_id, skip_recent),
        )
        cutoff_row = await cutoff_cursor.fetchone()
        if not cutoff_row:
            return []
        cutoff_ts: int = cutoff_row[0]

        like_clauses = " AND ".join("content LIKE ?" for _ in keywords)
        like_params = [f"%{kw}%" for kw in keywords]

        rows_cursor = await db.execute(
            f"SELECT id, role, content, ts_ns FROM conversation_messages "
            f"WHERE scope_type=? AND scope_id=? AND ts_ns<=? "
            f"AND role IN ('user','assistant') AND {like_clauses} "
            f"ORDER BY ts_ns DESC LIMIT ?",
            (scope_type, scope_id, cutoff_ts, *like_params, limit),
        )
        rows = await rows_cursor.fetchall()
        return [
            {"id": r[0], "role": r[1], "content": r[2], "ts_ns": r[3], "score": 0.0}
            for r in rows
        ]

    async def delete_conversation_by_id(self, row_id: int) -> None:
        """按 id 删除单条会话记录。"""
        db = await self._get_db()
        await db.execute("DELETE FROM conversation_messages WHERE id=?", (row_id,))
        await db.commit()

    async def clear_conversation(self, *, scope_type: str, scope_id: str) -> int:
        """清空指定 scope 的全部会话记录，返回删除数量。"""
        db = await self._get_db()
        cursor = await db.execute(
            "DELETE FROM conversation_messages WHERE scope_type=? AND scope_id=?",
            (scope_type, scope_id),
        )
        await db.commit()
        return cursor.rowcount

    async def list_entity_profiles(self) -> list[dict]:
        """列出所有实体画像（含对话计数）。"""
        await self._ensure_entity_counter_columns()
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT scope_type, scope_id, personality, updated_ts_ns, conv_num, conv_update_num "
            "FROM entity_profile ORDER BY updated_ts_ns DESC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "scope_type": r[0], "scope_id": r[1], "personality": r[2],
                "updated_ts_ns": r[3], "conv_num": r[4] or 0, "conv_update_num": r[5] or 0,
            }
            for r in rows
        ]

    async def delete_entity_profile(self, *, scope_type: str, scope_id: str) -> None:
        """删除指定实体画像。"""
        db = await self._get_db()
        await db.execute(
            "DELETE FROM entity_profile WHERE scope_type=? AND scope_id=?",
            (scope_type, scope_id),
        )
        await db.commit()

    # ------------------------------------------------------------------
    # 实体别名（跨平台身份关联）
    # ------------------------------------------------------------------

    async def resolve_alias(
        self, scope_type: str, scope_id: str,
    ) -> Optional[tuple[str, str]]:
        """解析别名，返回 (primary_type, primary_id)；无别名时返回 None。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT primary_scope_type, primary_scope_id FROM entity_alias "
            "WHERE scope_type=? AND scope_id=?",
            (scope_type, scope_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return (row[0], row[1])

    async def set_alias(
        self,
        *,
        scope_type: str,
        scope_id: str,
        primary_scope_type: str,
        primary_scope_id: str,
    ) -> None:
        """设置别名映射（source → primary）。"""
        db = await self._get_db()
        now = time.time_ns()
        await db.execute(
            """
            INSERT INTO entity_alias(scope_type, scope_id, primary_scope_type, primary_scope_id, created_ts_ns)
            VALUES(?,?,?,?,?)
            ON CONFLICT(scope_type, scope_id) DO UPDATE SET
              primary_scope_type=excluded.primary_scope_type,
              primary_scope_id=excluded.primary_scope_id,
              created_ts_ns=excluded.created_ts_ns
            """,
            (scope_type, scope_id, primary_scope_type, primary_scope_id, int(now)),
        )
        await db.commit()

    async def remove_alias(self, *, scope_type: str, scope_id: str) -> bool:
        """移除别名，返回是否有记录被删除。"""
        db = await self._get_db()
        cursor = await db.execute(
            "DELETE FROM entity_alias WHERE scope_type=? AND scope_id=?",
            (scope_type, scope_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def list_aliases(self) -> list[dict]:
        """列出所有别名关系。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT scope_type, scope_id, primary_scope_type, primary_scope_id, created_ts_ns "
            "FROM entity_alias ORDER BY created_ts_ns DESC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "scope_type": r[0], "scope_id": r[1],
                "primary_scope_type": r[2], "primary_scope_id": r[3],
                "created_ts_ns": r[4],
            }
            for r in rows
        ]

    async def get_aliases_for_primary(
        self, scope_type: str, scope_id: str,
    ) -> list[dict]:
        """获取某个 primary 下的所有别名。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT scope_type, scope_id FROM entity_alias "
            "WHERE primary_scope_type=? AND primary_scope_id=?",
            (scope_type, scope_id),
        )
        rows = await cursor.fetchall()
        return [{"scope_type": r[0], "scope_id": r[1]} for r in rows]
