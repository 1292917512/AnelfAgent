"""文件/chunks 索引与 embedding 缓存：memory.md + memory/*.md 分块索引存储。

覆盖 files / chunks / chunks_fts / embedding_cache 表的 CRUD 与检索
（sqlite-vec KNN 优先，keyset 分页批量评分兜底，LIKE 为 FTS 不可用时的回退）。
连接与 vec 索引维护由 MemoryConnectionManager 提供，本模块不自建连接。

模型可见：文件结果提供命中附近原文、实际行号和原始块范围。
Token：窗口最多 500 字符，与召回注入预算一致；无额外模型调用。
缓存：只改变查询结果，不改变稳定前缀或索引内容。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import aiosqlite

from core.log import log
from core.text_passage import select_passage

from ..memory_utils import pack_embedding, rank_embedding_blobs, unpack_embedding
from ._shared import (
    build_fts_query,
    escape_like,
    extract_like_keywords,
    get_memory_config_value,
)
from .connection import MemoryConnectionManager, fetch_count
from .tokenizer import tokenize_for_index, tokenize_for_query


def _passages(results: list[Dict[str, Any]], query: str) -> list[Dict[str, Any]]:
    """为最终文件候选选择证据窗口，并保留原始块范围供完整读取。"""
    terms = tokenize_for_query(query)
    for result in results:
        text = result["snippet"]
        passage = select_passage(text, terms)
        origin = result["start_line"]
        result["provenance"] = {
            "chunk_start_line": origin,
            "chunk_end_line": result["end_line"],
            "truncated": passage.start > 0 or passage.end < len(text),
        }
        result["start_line"] = origin + text.count("\n", 0, passage.start)
        result["end_line"] = origin + text.count("\n", 0, max(passage.start, passage.end - 1))
        result["snippet"] = passage.text
    return results


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
            await self._delete_chunks(db, path)
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
        """批量写入分块，返回写入数量。"""
        if not chunks:
            return 0
        tokenized = await asyncio.to_thread(lambda: [tokenize_for_index(ch["text"]) for ch in chunks])
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            await self._write_chunks(db, chunks, tokenized)
        return len(chunks)

    async def replace_file_index(
        self, path: str, hash_val: str, mtime_ns: int, size: int, chunks: list[Dict[str, Any]],
    ) -> int:
        """在一个事务内替换文件及其分块，失败或取消时恢复旧索引。"""
        if any(ch["path"] != path for ch in chunks):
            raise ValueError("分块路径必须与文件路径一致")
        tokenized = await asyncio.to_thread(lambda: [tokenize_for_index(ch["text"]) for ch in chunks])
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            await self._delete_chunks(db, path)
            await self._write_chunks(db, chunks, tokenized)
            await db.execute(
                "INSERT OR REPLACE INTO files(path, hash, mtime_ns, size) VALUES(?,?,?,?)",
                (path, hash_val, mtime_ns, size),
            )
        return len(chunks)

    async def _write_chunks(
        self, db: aiosqlite.Connection, chunks: list[Dict[str, Any]], tokenized: list[str],
    ) -> None:
        """在调用方事务内写入分块及启用的检索索引。"""
        for ch, fts_text in zip(chunks, tokenized, strict=True):
            await db.execute(
                "INSERT OR REPLACE INTO chunks(id, path, start_line, end_line, hash, text, embedding, updated_ns) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (ch["id"], ch["path"], ch["start_line"], ch["end_line"],
                 ch["hash"], ch["text"], ch.get("embedding"), ch["updated_ns"]),
            )
            await self._conn.vec_upsert_chunk(db, ch["id"], ch.get("embedding"))
            if self._conn.chunks_fts_available:
                await db.execute("DELETE FROM chunks_fts WHERE id=?", (ch["id"],))
                await db.execute(
                    "INSERT INTO chunks_fts(id, path, start_line, end_line, text) VALUES(?,?,?,?,?)",
                    (ch["id"], ch["path"], ch["start_line"], ch["end_line"], fts_text),
                )

    async def _delete_chunks(self, db: aiosqlite.Connection, path: str) -> int:
        """在调用方事务内移除文件的所有分块与检索条目。"""
        await self._conn.vec_delete_chunks_by_path(db, path)
        if self._conn.chunks_fts_available:
            await db.execute("DELETE FROM chunks_fts WHERE path=?", (path,))
        cursor = await db.execute("DELETE FROM chunks WHERE path=?", (path,))
        return cursor.rowcount or 0

    async def delete_chunks_by_path(self, path: str) -> int:
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            return await self._delete_chunks(db, path)

    async def search_chunks_vector(
        self,
        query_vec: list[float],
        limit: int = 10,
        min_score: float = 0.3,
        *,
        query: str = "",
    ) -> list[Dict[str, Any]]:
        """在 chunks 表中执行向量搜索：优先 sqlite-vec 索引，降级分批全表扫描。"""
        if self._conn.vec_available and self._conn.vec_dims is not None:
            try:
                results = await self._search_chunks_vector_vec(query_vec, limit, min_score)
                return _passages(results, query)
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
                self._score_chunks_vector_rows, rows, query_vec, min_score, limit,
            )
            merged.extend(scored)
            if len(rows) < batch_size:
                break
            if len(merged) > limit * 4:
                merged.sort(key=lambda x: x["score"], reverse=True)
                merged = merged[:limit]

        merged.sort(key=lambda x: x["score"], reverse=True)
        return _passages(merged[:limit], query)

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
        rows = list(await cursor.fetchall())
        new_last = str(rows[-1]["id"]) if rows else last_id
        return rows, new_last

    @staticmethod
    def _score_chunks_vector_rows(
        rows: list[Any],
        query_vec: list[float],
        min_score: float,
        limit: int,
    ) -> list[Dict[str, Any]]:
        """对一批 chunk 行计算余弦相似度（纯 CPU，供 to_thread 调用）。"""
        ranked = rank_embedding_blobs(query_vec, [row["embedding"] for row in rows],
                                      limit=limit, min_score=min_score)
        scored: list[Dict[str, Any]] = []
        for index, score in ranked:
            row = rows[index]
            scored.append({
                "id": row["id"], "path": row["path"],
                "start_line": row["start_line"], "end_line": row["end_line"],
                "snippet": row["text"], "score": score,
                "source": "file",
            })
        return scored

    async def _search_chunks_vector_vec(
        self,
        query_vec: list[float],
        limit: int,
        min_score: float,
    ) -> list[Dict[str, Any]]:
        """基于 sqlite-vec 的 chunks 精确 KNN 检索。"""
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
                "snippet": row["text"], "score": score,
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
            return _passages([
                {
                    "id": r["id"], "path": r["path"], "start_line": r["start_line"],
                    "end_line": r["end_line"], "snippet": r["text"],
                    # FTS5 rank 为负值且越小越好，映射为 |rank|/(1+|rank|)
                    "score": abs(r["rank"]) / (1.0 + abs(r["rank"])),
                    "source": "file",
                }
                for r in rows
            ], query)
        except Exception as exc:
            log(f"文件全文检索失败，降级文本匹配: {exc}", "DEBUG", tag="记忆")
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
        return _passages([
            {
                "id": r["id"], "path": r["path"], "start_line": r["start_line"],
                "end_line": r["end_line"], "snippet": r["text"],
                "score": 0.5, "source": "file",
            }
            for r in rows
        ], query)

    async def get_index_status(self) -> Dict[str, Any]:
        """返回文件索引的统计信息。"""
        db = await self._conn.get_db()
        file_count = await fetch_count(db, "SELECT COUNT(*) FROM files")
        chunk_count = await fetch_count(db, "SELECT COUNT(*) FROM chunks")
        chunk_with_emb = await fetch_count(
            db, "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL")
        mem_count = await fetch_count(db, "SELECT COUNT(*) FROM memories")
        cache_count = await fetch_count(db, "SELECT COUNT(*) FROM embedding_cache")
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
        return (await self.get_cached_embeddings([text_hash])).get(text_hash)

    async def get_cached_embeddings(self, text_hashes: list[str]) -> dict[str, list[float]]:
        """按去重后的内容哈希分批读取缓存，限制 SQLite 绑定参数数量。"""
        hashes = list(dict.fromkeys(text_hashes))
        if not hashes:
            return {}
        db = await self._conn.get_db()
        found: dict[str, list[float]] = {}
        for start in range(0, len(hashes), 500):
            batch = hashes[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            cursor = await db.execute(
                f"SELECT hash, embedding FROM embedding_cache WHERE hash IN ({placeholders})", batch,
            )
            found.update({row["hash"]: unpack_embedding(row["embedding"])
                          for row in await cursor.fetchall() if row["embedding"]})
        return found

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
