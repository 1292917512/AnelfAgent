"""StickerStore：表情包 + 全量图片索引的持久化与混合检索。

存储：独立 SQLite 库（派生自主库路径，``{stem}_stickers.sqlite3``），WAL 模式。
索引：embedding BLOB 为权威数据，sqlite-vec vec0 表为派生索引（与 MemoryStore 同一范式）；
      无 sqlite-vec / 无 embedding 模型时降级为 Python 余弦 / 模糊关键词打分。

两张表：
- stickers：表情包（agent 收藏或 WebUI 上传），description+tags 文本向量
- images：全量图片感知索引（入站图片后台沉淀），phash + description 向量
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from array import array
from typing import Any, Dict, List, Optional

import aiosqlite

from core.log import log

from .fuzzy import fuzzy_rank
from .phash import hamming_distance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stickers (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    emotion TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    phash TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    use_count INTEGER NOT NULL DEFAULT 0,
    created_ns INTEGER NOT NULL,
    updated_ns INTEGER NOT NULL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_stickers_hash ON stickers(content_hash);
CREATE INDEX IF NOT EXISTS idx_stickers_use ON stickers(use_count);

CREATE TABLE IF NOT EXISTS images (
    path TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    phash TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    ts_ns INTEGER NOT NULL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_images_hash ON images(content_hash);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# 图搜图：dHash 汉明距离粗筛阈值（64 bit 中差异 ≤10 视为候选）
PHASH_CANDIDATE_THRESHOLD = 10


def _default_db_path() -> str:
    """派生贴纸库路径：与 MemoryStore 同一目录，stem + '_stickers'。"""
    try:
        from agent.storage.sqlite_backend import default_sqlite_path
        main = default_sqlite_path()
    except Exception:
        main = os.path.join("workspace", "data.sqlite3")
    stem, ext = os.path.splitext(main)
    return f"{stem}_stickers{ext or '.sqlite3'}"


def _embed_to_blob(vec: List[float]) -> bytes:
    return array("f", vec).tobytes()


def _blob_to_embed(blob: bytes) -> List[float]:
    a = array("f")
    a.frombytes(blob)
    return list(a)


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _row_to_sticker(row: aiosqlite.Row) -> Dict[str, Any]:
    try:
        tags = json.loads(row["tags_json"])
    except Exception:
        tags = []
    return {
        "id": row["id"],
        "description": row["description"],
        "tags": tags,
        "emotion": row["emotion"],
        "file_path": row["file_path"],
        "content_hash": row["content_hash"],
        "phash": row["phash"],
        "source": row["source"],
        "use_count": row["use_count"],
        "created_ns": row["created_ns"],
        "updated_ns": row["updated_ns"],
        "has_embedding": row["embedding"] is not None,
    }


def _row_to_image(row: aiosqlite.Row) -> Dict[str, Any]:
    return {
        "path": row["path"],
        "description": row["description"],
        "content_hash": row["content_hash"],
        "phash": row["phash"],
        "source": row["source"],
        "ts_ns": row["ts_ns"],
        "has_embedding": row["embedding"] is not None,
    }


class StickerStore:
    """表情包与图片索引的统一存储（懒初始化单例）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _default_db_path()
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._vec_available = False

    # ------------------------------------------------------------------
    # 连接与 schema
    # ------------------------------------------------------------------

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is not None:
            return self._db
        async with self._lock:
            if self._db is not None:
                return self._db
            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
            db = await aiosqlite.connect(self._db_path)
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA synchronous=NORMAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            self._vec_available = await self._load_vec_extension(db)
            await db.executescript(_SCHEMA)
            await db.commit()
            self._db = db
            log(f"StickerStore 就绪: {self._db_path} (vec={self._vec_available})", tag="贴纸")
            return db

    async def _load_vec_extension(self, db: aiosqlite.Connection) -> bool:
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
            log(f"sqlite-vec 不可用，贴纸向量检索降级为全表扫描: {exc}", "WARNING", tag="贴纸")
            return False

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # vec0 派生索引（维度首次写入时确定，变更时重建）
    # ------------------------------------------------------------------

    async def _ensure_vec_table(self, db: aiosqlite.Connection, kind: str, dims: int) -> bool:
        """确保 vec0 索引表存在且维度匹配；维度变更时从 BLOB 重建。返回可用性。"""
        if not self._vec_available:
            return False
        table = f"{kind}_vec"
        meta_key = f"{kind}_vec_dims"
        cursor = await db.execute("SELECT value FROM meta WHERE key=?", (meta_key,))
        row = await cursor.fetchone()
        existing_dims = int(row["value"]) if row else 0

        if existing_dims == dims:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if await cursor.fetchone():
                return True

        if existing_dims and existing_dims != dims:
            log(f"{kind} embedding 维度变更 {existing_dims}→{dims}，重建 vec 索引", "WARNING", tag="贴纸")
            await db.execute(f"DROP TABLE IF EXISTS {table}")

        try:
            await db.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} "
                f"USING vec0(id TEXT PRIMARY KEY, embedding float[{dims}] distance_metric=cosine)"
            )
            await db.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (meta_key, str(dims)),
            )
        except Exception as exc:
            log(f"vec 索引表创建失败（降级全表扫描）: {exc}", "WARNING", tag="贴纸")
            self._vec_available = False
            return False

        # 从权威 BLOB 回填派生索引
        main_table = "stickers" if kind == "stickers" else "images"
        id_col = "id" if kind == "stickers" else "path"
        try:
            import sqlite_vec
            cursor = await db.execute(
                f"SELECT {id_col}, embedding FROM {main_table} WHERE embedding IS NOT NULL")
            for row in await cursor.fetchall():
                vec = _blob_to_embed(row["embedding"])
                if len(vec) != dims:
                    continue
                await db.execute(
                    f"INSERT OR REPLACE INTO {table}(id, embedding) VALUES(?, ?)",
                    (row[id_col], sqlite_vec.serialize_float32(vec)),
                )
            await db.commit()
        except Exception as exc:
            log(f"vec 索引回填失败: {exc}", "WARNING", tag="贴纸")
        return True

    async def _vec_upsert(self, kind: str, item_id: str, vec: List[float]) -> None:
        db = await self._get_db()
        if not await self._ensure_vec_table(db, kind, len(vec)):
            return
        try:
            import sqlite_vec
            # vec0 虚表的 INSERT OR REPLACE 不一定生效，先删后插保证幂等
            await db.execute(f"DELETE FROM {kind}_vec WHERE id=?", (item_id,))
            await db.execute(
                f"INSERT INTO {kind}_vec(id, embedding) VALUES(?, ?)",
                (item_id, sqlite_vec.serialize_float32(vec)),
            )
            await db.commit()
        except Exception as exc:
            log(f"vec 写入失败: {exc}", "DEBUG", tag="贴纸")

    async def _vec_delete(self, kind: str, item_id: str) -> None:
        if not self._vec_available:
            return
        try:
            db = await self._get_db()
            await db.execute(f"DELETE FROM {kind}_vec WHERE id=?", (item_id,))
            await db.commit()
        except Exception:
            pass

    async def _vec_search(
        self, kind: str, query_vec: List[float], limit: int,
    ) -> Optional[List[Dict[str, Any]]]:
        """vec0 KNN 检索，返回 [{id, score}]；不可用返回 None。"""
        if not self._vec_available:
            return None
        db = await self._get_db()
        if not await self._ensure_vec_table(db, kind, len(query_vec)):
            return None
        try:
            import sqlite_vec
            cursor = await db.execute(
                f"SELECT id, distance FROM {kind}_vec "
                f"WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(query_vec), limit),
            )
            rows = await cursor.fetchall()
            return [{"id": r["id"], "score": round(1.0 - r["distance"], 4)} for r in rows]
        except Exception as exc:
            log(f"vec 检索失败（降级全表扫描）: {exc}", "WARNING", tag="贴纸")
            return None

    # ------------------------------------------------------------------
    # 表情包 CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _new_id(content_hash: str) -> str:
        seed = f"{content_hash}:{time.time_ns()}"
        return hashlib.md5(seed.encode()).hexdigest()[:8]

    async def add_sticker(
        self,
        *,
        file_path: str,
        description: str,
        tags: List[str],
        emotion: str = "",
        content_hash: str = "",
        phash: str = "",
        source: str = "",
        embedding: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        """新增表情包；content_hash 重复时原地更新元数据（去重语义同 nekro）。"""
        db = await self._get_db()
        now = time.time_ns()

        if content_hash:
            cursor = await db.execute(
                "SELECT * FROM stickers WHERE content_hash=?", (content_hash,))
            existing = await cursor.fetchone()
            if existing:
                sticker_id = existing["id"]
                await db.execute(
                    "UPDATE stickers SET description=?, tags_json=?, emotion=?, "
                    "file_path=?, phash=?, updated_ns=? WHERE id=?",
                    (description, json.dumps(tags, ensure_ascii=False), emotion,
                     file_path, phash or existing["phash"], now, sticker_id),
                )
                await db.commit()
                if embedding:
                    await db.execute(
                        "UPDATE stickers SET embedding=? WHERE id=?",
                        (_embed_to_blob(embedding), sticker_id))
                    await db.commit()
                    await self._vec_upsert("stickers", sticker_id, embedding)
                cursor = await db.execute("SELECT * FROM stickers WHERE id=?", (sticker_id,))
                result = _row_to_sticker(await cursor.fetchone())
                result["deduplicated"] = True
                return result

        sticker_id = self._new_id(content_hash or file_path)
        await db.execute(
            "INSERT INTO stickers(id, description, tags_json, emotion, file_path, "
            "content_hash, phash, source, use_count, created_ns, updated_ns, embedding) "
            "VALUES(?,?,?,?,?,?,?,?,0,?,?,?)",
            (sticker_id, description, json.dumps(tags, ensure_ascii=False), emotion,
             file_path, content_hash, phash, source, now, now,
             _embed_to_blob(embedding) if embedding else None),
        )
        await db.commit()
        if embedding:
            await self._vec_upsert("stickers", sticker_id, embedding)
        cursor = await db.execute("SELECT * FROM stickers WHERE id=?", (sticker_id,))
        return _row_to_sticker(await cursor.fetchone())

    async def get_sticker(self, sticker_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute("SELECT * FROM stickers WHERE id=?", (sticker_id,))
        row = await cursor.fetchone()
        return _row_to_sticker(row) if row else None

    async def update_sticker(
        self,
        sticker_id: str,
        *,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        emotion: Optional[str] = None,
        embedding: Optional[List[float]] = None,
    ) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        current = await self.get_sticker(sticker_id)
        if not current:
            return None
        await db.execute(
            "UPDATE stickers SET description=?, tags_json=?, emotion=?, updated_ns=? WHERE id=?",
            (
                description if description is not None else current["description"],
                json.dumps(tags if tags is not None else current["tags"], ensure_ascii=False),
                emotion if emotion is not None else current["emotion"],
                time.time_ns(), sticker_id,
            ),
        )
        await db.commit()
        if embedding:
            await db.execute(
                "UPDATE stickers SET embedding=? WHERE id=?",
                (_embed_to_blob(embedding), sticker_id))
            await db.commit()
            await self._vec_upsert("stickers", sticker_id, embedding)
        return await self.get_sticker(sticker_id)

    async def delete_sticker(self, sticker_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        current = await self.get_sticker(sticker_id)
        if not current:
            return None
        await db.execute("DELETE FROM stickers WHERE id=?", (sticker_id,))
        await db.commit()
        await self._vec_delete("stickers", sticker_id)
        return current

    async def touch_use(self, sticker_id: str) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE stickers SET use_count=use_count+1 WHERE id=?", (sticker_id,))
        await db.commit()

    async def list_stickers(
        self, *, page: int = 1, page_size: int = 20, query: str = "",
    ) -> Dict[str, Any]:
        db = await self._get_db()
        cursor = await db.execute("SELECT COUNT(*) AS c FROM stickers")
        total = (await cursor.fetchone())["c"]
        cursor = await db.execute(
            "SELECT * FROM stickers ORDER BY updated_ns DESC LIMIT ? OFFSET ?",
            (page_size, (max(1, page) - 1) * page_size),
        )
        items = [_row_to_sticker(r) for r in await cursor.fetchall()]
        if query:
            items = fuzzy_rank(query, [
                {**s, "name": s["id"]} for s in items
            ], limit=len(items), min_score=0.0)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def recent_stickers(self, limit: int = 5) -> List[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM stickers ORDER BY use_count DESC, updated_ns DESC LIMIT ?",
            (limit,))
        return [_row_to_sticker(r) for r in await cursor.fetchall()]

    async def stats(self) -> Dict[str, Any]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(use_count),0) AS u FROM stickers")
        row = await cursor.fetchone()
        cursor = await db.execute("SELECT COUNT(*) AS c FROM images")
        images = (await cursor.fetchone())["c"]
        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM images WHERE description != ''")
        described = (await cursor.fetchone())["c"]
        return {
            "stickers": row["c"],
            "total_uses": row["u"],
            "indexed_images": images,
            "described_images": described,
            "vec_available": self._vec_available,
        }

    # ------------------------------------------------------------------
    # 检索（向量 → Python 余弦 → 模糊打分 三级降级）
    # ------------------------------------------------------------------

    async def search_stickers(
        self,
        query: str,
        *,
        query_vec: Optional[List[float]] = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """文搜表情包：语义向量优先，无向量时降级模糊关键词打分。"""
        db = await self._get_db()

        if query_vec:
            hits = await self._vec_search("stickers", query_vec, limit)
            if hits is None:
                # vec 索引不可用：Python 全表余弦
                hits = []
                cursor = await db.execute(
                    "SELECT id, embedding FROM stickers WHERE embedding IS NOT NULL")
                for row in await cursor.fetchall():
                    score = _cosine(query_vec, _blob_to_embed(row["embedding"]))
                    if score > 0.05:
                        hits.append({"id": row["id"], "score": round(score, 4)})
                hits.sort(key=lambda x: x["score"], reverse=True)
                hits = hits[:limit]
            results = []
            for hit in hits:
                sticker = await self.get_sticker(hit["id"])
                if sticker:
                    sticker["score"] = hit["score"]
                    results.append(sticker)
            if results:
                return results

        # 降级：模糊关键词打分（无 embedding 模型时仍可用）
        cursor = await db.execute("SELECT * FROM stickers")
        candidates = [
            {**_row_to_sticker(r), "name": r["id"]} for r in await cursor.fetchall()
        ]
        return fuzzy_rank(query, candidates, limit=limit)

    async def search_images(
        self,
        query: str,
        *,
        query_vec: Optional[List[float]] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """文搜图：全量图片索引的语义检索，无向量时降级描述模糊匹配。"""
        db = await self._get_db()

        if query_vec:
            hits = await self._vec_search("images", query_vec, limit)
            if hits is None:
                hits = []
                cursor = await db.execute(
                    "SELECT path, embedding FROM images WHERE embedding IS NOT NULL")
                for row in await cursor.fetchall():
                    score = _cosine(query_vec, _blob_to_embed(row["embedding"]))
                    if score > 0.05:
                        hits.append({"id": row["path"], "score": round(score, 4)})
                hits.sort(key=lambda x: x["score"], reverse=True)
                hits = hits[:limit]
            results = []
            for hit in hits:
                img = await self.get_image(hit["id"])
                if img:
                    img["score"] = hit["score"]
                    results.append(img)
            if results:
                return results

        cursor = await db.execute("SELECT * FROM images WHERE description != ''")
        candidates = [
            {
                **_row_to_image(r),
                "name": os.path.basename(r["path"]),
                "tags": [],
            }
            for r in await cursor.fetchall()
        ]
        return fuzzy_rank(query, candidates, limit=limit, min_score=15.0)

    async def find_similar_by_phash(
        self, phash: str, *, limit: int = 5,
        threshold: int = PHASH_CANDIDATE_THRESHOLD,
    ) -> List[Dict[str, Any]]:
        """图搜图：dHash 汉明距离粗筛（stickers + images 两表合并）。"""
        if not phash:
            return []
        db = await self._get_db()
        results: List[Dict[str, Any]] = []

        cursor = await db.execute("SELECT * FROM stickers WHERE phash != ''")
        for row in await cursor.fetchall():
            dist = hamming_distance(phash, row["phash"])
            if dist is not None and dist <= threshold:
                item = _row_to_sticker(row)
                item["kind"] = "sticker"
                item["distance"] = dist
                results.append(item)

        cursor = await db.execute("SELECT * FROM images WHERE phash != ''")
        for row in await cursor.fetchall():
            dist = hamming_distance(phash, row["phash"])
            if dist is not None and dist <= threshold:
                item = _row_to_image(row)
                item["kind"] = "image"
                item["file_path"] = item["path"]
                item["distance"] = dist
                results.append(item)

        results.sort(key=lambda x: (x["distance"], -x.get("use_count", 0)))
        return results[:limit]

    # ------------------------------------------------------------------
    # 全量图片索引
    # ------------------------------------------------------------------

    async def upsert_image(
        self,
        *,
        path: str,
        description: str = "",
        content_hash: str = "",
        phash: str = "",
        source: str = "",
        embedding: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        db = await self._get_db()
        now = time.time_ns()
        await db.execute(
            "INSERT INTO images(path, description, content_hash, phash, source, ts_ns, embedding) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET "
            "description=excluded.description, content_hash=excluded.content_hash, "
            "phash=excluded.phash, source=excluded.source, embedding=excluded.embedding",
            (path, description, content_hash, phash, source, now,
             _embed_to_blob(embedding) if embedding else None),
        )
        await db.commit()
        if embedding:
            await self._vec_upsert("images", path, embedding)
        return (await self.get_image(path)) or {}

    async def get_image(self, path: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute("SELECT * FROM images WHERE path=?", (path,))
        row = await cursor.fetchone()
        return _row_to_image(row) if row else None

    async def get_image_by_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        if not content_hash:
            return None
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM images WHERE content_hash=? LIMIT 1", (content_hash,))
        row = await cursor.fetchone()
        return _row_to_image(row) if row else None

    async def delete_image(self, path: str) -> bool:
        db = await self._get_db()
        cursor = await db.execute("DELETE FROM images WHERE path=?", (path,))
        await db.commit()
        if cursor.rowcount:
            await self._vec_delete("images", path)
            return True
        return False

    async def list_images(
        self, *, page: int = 1, page_size: int = 24,
    ) -> Dict[str, Any]:
        db = await self._get_db()
        cursor = await db.execute("SELECT COUNT(*) AS c FROM images")
        total = (await cursor.fetchone())["c"]
        cursor = await db.execute(
            "SELECT * FROM images ORDER BY ts_ns DESC LIMIT ? OFFSET ?",
            (page_size, (max(1, page) - 1) * page_size),
        )
        items = [_row_to_image(r) for r in await cursor.fetchall()]
        return {"items": items, "total": total, "page": page, "page_size": page_size}


# ------------------------------------------------------------------
# 单例
# ------------------------------------------------------------------

_store: Optional[StickerStore] = None


def get_sticker_store() -> StickerStore:
    """获取 StickerStore 单例（首次调用时创建，DB 连接懒初始化）。"""
    global _store
    if _store is None:
        _store = StickerStore()
    return _store
