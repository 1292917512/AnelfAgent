"""文件/chunks 索引与 embedding 缓存：memory.md + memory/*.md 分块索引存储。

覆盖 files / chunks / chunks_fts / embedding_cache 表的 CRUD 与检索
（sqlite-vec ANN 优先，keyset 分页全表扫描兜底，LIKE 为 FTS 不可用时的回退）。
连接与 vec 索引维护由 MemoryConnectionManager 提供，本模块不自建连接。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from core.log import log

from ..memory_utils import cosine_similarity, pack_embedding, unpack_embedding
from ._shared import (
    build_fts_query,
    escape_like,
    extract_like_keywords,
    get_memory_config_value,
)
from .connection import MemoryConnectionManager


class FileIndexStore:
    """文件索引、chunks 双轨检索与 embedding 缓存。"""

    def __init__(self, conn: MemoryConnectionManager) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # 文件索引 CRUD
    # ------------------------------------------------------------------

    async def get_file(self, path: str) -> Optional[Dict[str, Any]]:
        db = await self._conn.get_db()
        cursor = await db.execute("SELECT path, hash, mtime_ns, size FROM files WHERE path=?", (path,))
        row = await cursor.fetchone()
        if not row:
            return None
        return {"path": row["path"], "hash": row["hash"], "mtime_ns": row["mtime_ns"], "size": row["size"]}

    async def upsert_file(self, path: str, hash_val: str, mtime_ns: int, size: int) -> None:
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            await db.execute(
                "INSERT OR REPLACE INTO files(path, hash, mtime_ns, size) VALUES(?,?,?,?)",
                (path, hash_val, mtime_ns, size),
            )

    async def delete_file(self, path: str) -> None:
        """删除文件记录及其所有 chunks。"""
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            await self._conn.vec_delete_chunks_by_path(db, path)
            if self._conn.chunks_fts_available:
                try:
                    await db.execute("DELETE FROM chunks_fts WHERE path=?", (path,))
                except Exception as e:
                    log(f"chunks_fts 删除失败 [{path}]: {e}", "DEBUG")
            await db.execute("DELETE FROM chunks WHERE path=?", (path,))
            await db.execute("DELETE FROM files WHERE path=?", (path,))

    async def list_files(self) -> list[Dict[str, Any]]:
        db = await self._conn.get_db()
        cursor = await db.execute("SELECT path, hash, mtime_ns, size FROM files ORDER BY path")
        rows = await cursor.fetchall()
        return [{"path": r["path"], "hash": r["hash"], "mtime_ns": r["mtime_ns"], "size": r["size"]} for r in rows]

    async def list_chunk_counts(self) -> Dict[str, int]:
        """返回每个已索引文件的 chunk 数量（path → count）。"""
        db = await self._conn.get_db()
        cursor = await db.execute("SELECT path, COUNT(*) AS c FROM chunks GROUP BY path")
        return {str(row["path"]): int(row["c"]) for row in await cursor.fetchall()}

    # ------------------------------------------------------------------
    # Chunks CRUD
    # ------------------------------------------------------------------

    async def upsert_chunks(self, chunks: list[Dict[str, Any]]) -> int:
        """批量写入 chunks（id 冲突时替换）。返回写入数量。"""
        if not chunks:
            return 0
        from .tokenizer import tokenize_for_index

        db = await self._conn.get_db()
        # 分词为 CPU 密集操作，批量放 worker 线程一次性完成
        tokenized_texts = await asyncio.to_thread(
            lambda: [tokenize_for_index(ch["text"]) for ch in chunks]
        )
        count = 0
        async with self._conn.tx(db):
            for ch, fts_text in zip(chunks, tokenized_texts, strict=True):
                await db.execute(
                    "INSERT OR REPLACE INTO chunks(id, path, start_line, end_line, hash, text, embedding, updated_ns) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (ch["id"], ch["path"], ch["start_line"], ch["end_line"],
                     ch["hash"], ch["text"], ch.get("embedding"), ch["updated_ns"]),
                )
                await self._conn.vec_upsert_chunk(db, ch["id"], ch.get("embedding"))
                if self._conn.chunks_fts_available:
                    try:
                        await db.execute("DELETE FROM chunks_fts WHERE id=?", (ch["id"],))
                        await db.execute(
                            "INSERT INTO chunks_fts(id, path, start_line, end_line, text) VALUES(?,?,?,?,?)",
                            (ch["id"], ch["path"], ch["start_line"], ch["end_line"], fts_text),
                        )
                    except Exception as e:
                        log(f"chunks_fts 同步失败 [{ch['id']}]: {e}", "DEBUG")
                count += 1
        return count

    async def delete_chunks_by_path(self, path: str) -> int:
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            await self._conn.vec_delete_chunks_by_path(db, path)
            if self._conn.chunks_fts_available:
                try:
                    await db.execute("DELETE FROM chunks_fts WHERE path=?", (path,))
                except Exception as e:
                    log(f"chunks_fts 路径删除失败 [{path}]: {e}", "DEBUG")
            cursor = await db.execute("DELETE FROM chunks WHERE path=?", (path,))
        return cursor.rowcount or 0

    async def search_chunks_vector(
        self,
        query_vec: list[float],
        limit: int = 10,
        min_score: float = 0.3,
    ) -> list[Dict[str, Any]]:
        """在 chunks 表中执行向量搜索：优先 sqlite-vec 索引，降级分批全表扫描。"""
        if self._conn.vec_available and self._conn.vec_dims is not None:
            try:
                return await self._search_chunks_vector_vec(query_vec, limit, min_score)
            except Exception as exc:
                log(f"chunks vec 检索失败，降级全表扫描: {exc}", "WARNING")

        batch_size: int = get_memory_config_value("vector_search_batch_size", 500)

        # keyset 分页顺序扫描（与记忆向量搜索同约定：避免深页 OFFSET O(n²) IO）
        merged: list[Dict[str, Any]] = []
        last_id = ""
        while True:
            rows, last_id = await self._fetch_chunks_vector_batch(last_id, batch_size)
            if not rows:
                break
            scored = await asyncio.to_thread(
                self._score_chunks_vector_rows, rows, query_vec, min_score,
            )
            merged.extend(scored)
            if len(rows) < batch_size:
                break
            if len(merged) > limit * 4:
                merged.sort(key=lambda x: x["score"], reverse=True)
                merged = merged[:limit]

        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:limit]

    async def _fetch_chunks_vector_batch(
        self, last_id: str, batch_size: int,
    ) -> tuple[list[Any], str]:
        """按 id 游标取一批带向量的 chunks（keyset 分页），返回 (行, 新游标)。"""
        db = await self._conn.get_db()
        cursor = await db.execute(
            "SELECT id, path, start_line, end_line, text, embedding FROM chunks "
            "WHERE embedding IS NOT NULL AND id > ? ORDER BY id LIMIT ?",
            (last_id, batch_size),
        )
        rows = await cursor.fetchall()
        new_last = str(rows[-1]["id"]) if rows else last_id
        return rows, new_last

    @staticmethod
    def _score_chunks_vector_rows(
        rows: list[Any],
        query_vec: list[float],
        min_score: float,
    ) -> list[Dict[str, Any]]:
        """对一批 chunk 行计算余弦相似度（纯 CPU，供 to_thread 调用）。"""
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
        return scored

    async def _search_chunks_vector_vec(
        self,
        query_vec: list[float],
        limit: int,
        min_score: float,
    ) -> list[Dict[str, Any]]:
        """基于 sqlite-vec 的 chunks ANN 检索。"""
        db = await self._conn.get_db()
        cursor = await db.execute(
            "SELECT chunk_id, distance FROM chunks_vec "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (pack_embedding(query_vec), max(1, limit)),
        )
        vec_rows = await cursor.fetchall()
        if not vec_rows:
            return []

        ids = [str(r["chunk_id"]) for r in vec_rows]
        placeholders = ",".join("?" for _ in ids)
        cursor = await db.execute(
            f"SELECT id, path, start_line, end_line, text FROM chunks WHERE id IN ({placeholders})",
            ids,
        )
        chunk_rows = {str(row["id"]): row for row in await cursor.fetchall()}

        scored: list[Dict[str, Any]] = []
        for r in vec_rows:
            score = 1.0 - float(r["distance"])
            if score < min_score:
                continue
            row = chunk_rows.get(str(r["chunk_id"]))
            if row is None:
                continue
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
        if not self._conn.chunks_fts_available:
            return await self._search_chunks_like(query, limit)
        fts_query = build_fts_query(query)
        if not fts_query:
            return []
        db = await self._conn.get_db()
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
                    # FTS5 rank 为负值且越小越好，映射为 |rank|/(1+|rank|)
                    "score": abs(r["rank"]) / (1.0 + abs(r["rank"])),
                    "source": "file",
                }
                for r in rows
            ]
        except Exception:
            return await self._search_chunks_like(query, limit)

    async def _search_chunks_like(self, query: str, limit: int) -> list[Dict[str, Any]]:
        keywords = extract_like_keywords(query)
        if not keywords:
            return []
        db = await self._conn.get_db()
        conditions = " OR ".join(r"text LIKE ? ESCAPE '\'" for _ in keywords)
        params: list[Any] = [f"%{escape_like(kw)}%" for kw in keywords]
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
        db = await self._conn.get_db()
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
            "fts_available": self._conn.fts_available,
            "chunks_fts_available": self._conn.chunks_fts_available,
        }

    # ------------------------------------------------------------------
    # Embedding 缓存
    # ------------------------------------------------------------------

    async def get_cached_embedding(self, text_hash: str) -> Optional[list[float]]:
        db = await self._conn.get_db()
        cursor = await db.execute(
            "SELECT embedding FROM embedding_cache WHERE hash=?", (text_hash,),
        )
        row = await cursor.fetchone()
        if not row or not row["embedding"]:
            return None
        return unpack_embedding(row["embedding"])

    async def put_cached_embedding(self, text_hash: str, vec: list[float]) -> None:
        db = await self._conn.get_db()
        blob = pack_embedding(vec)
        now_ns = int(time.time() * 1e9)
        async with self._conn.tx(db):
            await db.execute(
                "INSERT OR REPLACE INTO embedding_cache(hash, embedding, dims, updated_ns) VALUES(?,?,?,?)",
                (text_hash, blob, len(vec), now_ns),
            )

    async def clean_embedding_cache(self) -> int:
        """清理不再被 chunks 引用的过期 embedding 缓存。"""
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            cursor = await db.execute(
                "DELETE FROM embedding_cache WHERE hash NOT IN "
                "(SELECT DISTINCT hash FROM chunks WHERE hash IS NOT NULL)"
            )
        cleaned = cursor.rowcount or 0
        if cleaned:
            log(f"🗑️ 清理 embedding 缓存: {cleaned} 条", tag="思维")
        return cleaned
