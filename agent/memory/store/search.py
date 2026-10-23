"""记忆搜索管线：FTS5 / 向量 / 混合评分 / 标签关联 / 双轨统一搜索。

语义评分 (Vector + FTS + TagMatch) × 衰减评分 (Recency + Frequency + Importance)。
向量检索优先 sqlite-vec 精确 KNN，标签在限量前过滤；不可用时 keyset 分页批量评分。
FTS 不可用或异常时回退 LIKE（ESCAPE 转义）。chunks 侧检索委托 FileIndexStore。
连接由 MemoryConnectionManager 提供，本模块不自建连接。

模型可见：保留语义与关键词两路证据、精确标签范围和原有评分。
Token 影响：不增加调用或结果预算。
缓存影响：只影响动态召回，不改稳定前缀。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional

from core.log import log

from ..memory_types import MemoryEntry, MemorySearchResult
from ..memory_utils import pack_embedding, rank_embedding_blobs
from ..tag_query import TagQuery
from ._shared import (
    MEM_COLUMNS,
    bigram_similarity,
    build_fts_query,
    compute_effective_score,
    escape_like,
    extract_like_keywords,
    file_temporal_decay,
    frequency_boost,
    get_memory_config_value,
    idf_tag_score,
    row_to_entry,
    temporal_weight,
    time_decay,
)
from .connection import MemoryConnectionManager
from .file_index import FileIndexStore
from .tag_intel import TagIntelligence


# 混合评分权重：动态配置（core.config 注册表），默认值见文件底部注册
def _w(name: str, default: float) -> float:
    try:
        from core.config import get_config_float

        return get_config_float(name, default)
    except Exception:
        return default


def _normalize(score: float, channel_max: float) -> float:
    """通道内归一化：各检索通道分数尺度不同（cosine 0~1 vs BM25 映射分），
    固定权重直接加权会系统性偏袒尺度大的一路；按本批通道最高分归一。"""
    if channel_max <= 0:
        return 0.0
    return min(1.0, score / channel_max)


def _archived_row_dict(row: Any) -> Dict[str, Any]:
    """归档表行 → 统一字典（按列名访问；embedding_blob 列忽略）。"""
    tags: list[str] = []
    try:
        parsed = json.loads(row["tags_json"]) if row["tags_json"] else []
        tags = [str(t) for t in parsed]
    except (ValueError, TypeError):
        pass
    from ..provenance import recall_provenance
    from ._shared import parse_metadata_json

    metadata = parse_metadata_json(row["metadata_json"])
    return {
        "id": int(row["id"]),
        "type": str(row["type"]),
        "content": str(row["content"]),
        "tags": tags,
        "importance": float(row["importance"]),
        "archived_at": int(row["archived_at_ns"]) / 1e9,
        "reason": str(row["archive_reason"] or ""),
        "provenance": recall_provenance(metadata, source=str(row["source"] or "")),
        "timestamp": int(row["ts_ns"]) / 1e9,
        "sensitivity": str(metadata.get("sensitivity", "normal")),
    }


class SearchEngine:
    """memories 表多路检索与 memories + chunks 双轨统一搜索。"""

    def __init__(self, conn: MemoryConnectionManager, file_index: FileIndexStore) -> None:
        self._conn = conn
        self._file_index = file_index
        self._tag_intel = TagIntelligence(conn)

    # ------------------------------------------------------------------
    # 标签索引
    # ------------------------------------------------------------------

    async def list_tags(self, *, fresh: bool = False) -> Dict[str, int]:
        """聚合所有活跃标签及其出现次数（importance=0 的已合并条目不计）。"""
        df, _ = await self._tag_intel.tag_stats(fresh=fresh)
        return dict(df)

    async def get_tag_df(self) -> tuple[Dict[str, int], int]:
        """标签文档频率 + 活跃记忆总数（IDF 评分用；TagIntelligence TTL 缓存）。"""
        return await self._tag_intel.tag_stats()

    async def cooccurring_tags(
        self,
        seed_tags: list[str],
        *,
        limit: int = 3,
    ) -> list[tuple[str, float]]:
        """共现联想：与种子标签同现过的其他关联标签（共现次数×IDF 降序）。"""
        return await self._tag_intel.cooccurring_tags(seed_tags, limit=limit)

    async def extract_query_mentions(self, query: str, *, limit: int = 3) -> list[str]:
        """从查询文本识别已知实体/话题提及，返回对应标签。"""
        return await self._tag_intel.extract_mentions(query, limit=limit)

    async def tag_merge_candidates(self, *, limit: int = 20) -> list[Dict[str, Any]]:
        """标签归并候选（豁免过滤 + 签名下发；候选生产归 TagIntelligence）。"""
        if limit <= 0:
            return []
        raw = await self._tag_intel.merge_candidates(limit=None)
        from .tag_curation import curate_candidates

        return await curate_candidates(raw, self._conn, limit=limit)

    async def search_by_tags(
        self,
        tags: list[str],
        limit: int = 20,
    ) -> list[MemoryEntry]:
        """按标签交集筛选记忆（返回包含所有指定标签的记忆）。

        使用 json_each 精确匹配标签值，避免 LIKE '%"tag"%' 的子串误命中
        （如标签 "art" 误匹配 "article"）。
        """
        if not tags:
            return []
        db = await self._conn.get_db()
        conditions = " AND ".join("EXISTS (SELECT 1 FROM json_each(memories.tags_json) WHERE value = ?)" for _ in tags)
        params: list[Any] = list(tags)
        params.append(limit)
        cursor = await db.execute(
            f"SELECT {MEM_COLUMNS} FROM memories WHERE importance > 0 AND {conditions} ORDER BY ts_ns DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        return [row_to_entry(r) for r in rows]

    async def search_associative(
        self,
        tags: list[str],
        *,
        exclude_ids: Optional[set[int]] = None,
        limit: int = 5,
    ) -> list[tuple[MemoryEntry, float]]:
        """关联检索：查找与给定标签集合有任一交集的记忆（标签网络的一跳扩展）。

        匹配口径与 search_by_tags 一致（json_each 精确匹配，不用 LIKE）。
        评分 = IDF 加权标签命中 × 0.6 + 有效分 × 0.4——稀有标签命中的关联
        强度显著高于全局高频标签（如当前用户 user: 标签）。
        Returns: [(entry, score)] 按分数降序。
        """
        if not tags:
            return []
        db = await self._conn.get_db()
        conditions = " OR ".join("EXISTS (SELECT 1 FROM json_each(memories.tags_json) WHERE value = ?)" for _ in tags)
        cursor = await db.execute(
            f"SELECT {MEM_COLUMNS} FROM memories WHERE importance > 0 AND ({conditions}) ORDER BY ts_ns DESC LIMIT 200",
            list(tags),
        )
        rows = await cursor.fetchall()
        tag_df, total_docs = await self.get_tag_df()
        now = time.time()
        exclude = exclude_ids or set()
        scored: list[tuple[MemoryEntry, float]] = []
        for row in rows:
            entry = row_to_entry(row)
            if entry.id is None or entry.id in exclude:
                continue
            tag_score = idf_tag_score(tags, entry.tags, tag_df, total_docs)
            if tag_score == 0:
                continue
            score = tag_score * 0.6 + compute_effective_score(entry, now) * 0.4
            scored.append((entry, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    # ------------------------------------------------------------------
    # FTS5 全文检索
    # ------------------------------------------------------------------

    async def search_fts(
        self, query: str, limit: int = 10, *, tag_query: TagQuery | None = None
    ) -> list[tuple[MemoryEntry, float]]:
        """FTS5 关键词搜索，返回 (entry, bm25_score) 列表。"""
        db = await self._conn.get_db()
        if not self._conn.fts_available:
            return await self._search_like(query, limit, tag_query=tag_query)

        fts_query = build_fts_query(query)
        if not fts_query:
            return []

        try:
            tag_sql, tag_params = (tag_query or TagQuery()).sql("m.tags_json")
            fts_select = ", ".join(f"m.{c.strip()}" for c in MEM_COLUMNS.split(","))
            cursor = await db.execute(
                f"""
                SELECT {fts_select}, rank
                FROM memories_fts f
                JOIN memories m ON m.id = f.rowid
                WHERE memories_fts MATCH ? AND m.importance > 0 AND ({tag_sql})
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, *tag_params, limit),
            )
            rows = await cursor.fetchall()
            results: list[tuple[MemoryEntry, float]] = []
            for row in rows:
                entry = row_to_entry(row)
                # FTS5 rank 为负值且越小（越负）匹配越好；
                # 映射为 |rank|/(1+|rank|) 保持单调递增、有界 0~1
                magnitude = abs(row["rank"])
                score = magnitude / (1.0 + magnitude)
                results.append((entry, score))
            return results
        except Exception as exc:
            log(f"FTS 搜索异常，回退 LIKE: {exc}", "DEBUG")
            return await self._search_like(query, limit, tag_query=tag_query)

    async def _search_like(
        self, query: str, limit: int, *, tag_query: TagQuery | None = None
    ) -> list[tuple[MemoryEntry, float]]:
        """LIKE 回退搜索：将查询拆成关键词段，用 OR 匹配。"""
        db = await self._conn.get_db()
        keywords = extract_like_keywords(query)
        if not keywords:
            return []
        conditions = " OR ".join(r"content LIKE ? ESCAPE '\'" for _ in keywords)
        params: list[Any] = [f"%{escape_like(kw)}%" for kw in keywords]
        tag_sql, tag_params = (tag_query or TagQuery()).sql()
        params.extend([*tag_params, limit])
        cursor = await db.execute(
            f"SELECT {MEM_COLUMNS} FROM memories WHERE importance > 0 AND ({conditions}) AND ({tag_sql}) "
            "ORDER BY ts_ns DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        return [(row_to_entry(r), 0.5) for r in rows]

    # ------------------------------------------------------------------
    # 向量相似度搜索（keyset 分页分批扫描）
    # ------------------------------------------------------------------

    async def search_vector(
        self,
        query_vec: list[float],
        limit: int = 10,
        min_score: float = 0.3,
        *,
        tag_query: TagQuery | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """向量搜索：标签预过滤后的精确 KNN，不可用时分批精确扫描。"""
        if limit <= 0 or not query_vec:
            return []
        if self._conn.vec_available and self._conn.vec_dims == len(query_vec):
            try:
                return await self._search_vector_vec(query_vec, limit, min_score, tag_query=tag_query)
            except Exception as exc:
                log(f"vec 检索失败，降级全表扫描: {exc}", "WARNING")

        batch_size: int = get_memory_config_value("vector_search_batch_size", 500)

        # keyset 分页（id 游标）顺序扫描：避免 LIMIT/OFFSET 深页 O(n²) IO；
        # 共享连接上并发 gather 实际串行，顺序循环语义更真实且更省连接占用
        merged: list[tuple[MemoryEntry, float]] = []
        last_id = 0
        while True:
            rows, last_id = await self._fetch_vector_batch(last_id, batch_size, tag_query=tag_query)
            if not rows:
                break
            scored = await asyncio.to_thread(
                self._score_vector_rows,
                rows,
                query_vec,
                min_score,
                limit,
            )
            merged.extend(scored)
            merged.sort(key=lambda x: x[1], reverse=True)
            del merged[limit:]
            if len(rows) < batch_size:
                break

        merged.sort(key=lambda x: x[1], reverse=True)
        return merged[:limit]

    async def _fetch_vector_batch(
        self,
        last_id: int,
        batch_size: int,
        *,
        tag_query: TagQuery | None = None,
    ) -> tuple[list[Any], int]:
        """按 id 游标取一批带向量的记忆（keyset 分页），返回 (行, 新游标)。"""
        db = await self._conn.get_db()
        tag_sql, tag_params = (tag_query or TagQuery()).sql()
        cursor = await db.execute(
            f"SELECT {MEM_COLUMNS} FROM memories WHERE embedding_blob IS NOT NULL "
            f"AND importance > 0 AND id > ? AND ({tag_sql}) ORDER BY id LIMIT ?",
            (last_id, *tag_params, batch_size),
        )
        rows = list(await cursor.fetchall())
        new_last = int(rows[-1]["id"]) if rows else last_id
        return rows, new_last

    def _score_vector_rows(
        self,
        rows: list[Any],
        query_vec: list[float],
        min_score: float,
        limit: int,
    ) -> list[tuple[MemoryEntry, float]]:
        """对一批记忆行计算余弦相似度（纯 CPU，供 to_thread 调用）。"""
        ranked = rank_embedding_blobs(query_vec, [row["embedding_blob"] for row in rows],
                                      limit=limit, min_score=min_score)
        return [(row_to_entry(rows[index]), score) for index, score in ranked]

    async def _search_vector_vec(
        self,
        query_vec: list[float],
        limit: int,
        min_score: float,
        *,
        tag_query: TagQuery | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """通过 rowid 子查询预过滤，再由 vec0 求精确 top-k，稀有标签不欠召回。"""
        db = await self._conn.get_db()
        tag_sql, tag_params = (tag_query or TagQuery()).sql()
        cursor = await db.execute(
            "SELECT rowid, distance FROM memories_vec WHERE embedding MATCH ? AND k = ? "
            "AND rowid IN (SELECT id FROM memories WHERE importance > 0 "
            f"AND embedding_blob IS NOT NULL AND ({tag_sql})) ORDER BY distance",
            (pack_embedding(query_vec), limit, *tag_params),
        )
        vec_rows = await cursor.fetchall()
        if not vec_rows:
            return []

        ids = [int(r["rowid"]) for r in vec_rows]
        placeholders = ",".join("?" for _ in ids)
        cursor = await db.execute(
            f"SELECT {MEM_COLUMNS} FROM memories "
            f"WHERE importance > 0 AND embedding_blob IS NOT NULL AND id IN ({placeholders}) AND ({tag_sql})",
            [*ids, *tag_params],
        )
        entries = {int(row["id"]): row_to_entry(row) for row in await cursor.fetchall()}

        scored: list[tuple[MemoryEntry, float]] = []
        for r in vec_rows:
            score = 1.0 - float(r["distance"])
            if score < min_score:
                continue
            entry = entries.get(int(r["rowid"]))
            if entry is not None:
                scored.append((entry, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    # ------------------------------------------------------------------
    # 混合搜索（两路并行评分管线）
    # ------------------------------------------------------------------

    async def search_hybrid(
        self,
        query: str,
        query_vec: Optional[list[float]] = None,
        query_tags: Optional[list[str]] = None,
        limit: int = 10,
        min_score: float = 0.1,
        require_tags: Optional[list[str]] = None,
        tag_query: TagQuery | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """混合搜索：向量 + FTS 两路并行，取并集后统一评分。

        LIKE 全表扫描兜底由 search_fts 内部覆盖（FTS 不可用或异常时自动回退），
        不再作为独立一路重复执行（FTS 可用时 LIKE 与 FTS 命中高度重叠，
        却每次召回都付出一次全表扫描成本）。
        require_tags 为硬过滤：记忆必须包含全部指定标签才进入评分。
        """
        tag_query = tag_query or TagQuery(all_of=tuple(require_tags or ()))
        filter_args = {"tag_query": tag_query} if tag_query.active else {}
        pool_size = limit * 5
        t0 = time.monotonic()

        async def _empty_vec() -> list[tuple[MemoryEntry, float]]:
            return []

        # 两路并行搜索
        vec_coro = (
            self.search_vector(query_vec, limit=pool_size, min_score=0.05, **filter_args) if query_vec else _empty_vec()
        )
        fts_coro = self.search_fts(query, limit=pool_size, **filter_args)

        vec_results, fts_results = await asyncio.gather(vec_coro, fts_coro)

        # 运行指标：召回通道命中观测（进程内累计，memory_stats 工具查询）
        try:
            from .. import metrics

            metrics.incr("recall.requests")
            metrics.incr("recall.vec_hits", len(vec_results))
            metrics.incr("recall.fts_hits", len(fts_results))
            metrics.incr("recall.latency_ms_total", int((time.monotonic() - t0) * 1000))
            if not fts_results:
                metrics.incr("recall.fts_empty")
        except Exception:
            pass

        all_results = fts_results + vec_results
        max_access = max((e.access_count for e, _ in all_results), default=0)

        # 合并候选（两路去重）
        candidates: Dict[int, tuple[MemoryEntry, float, float, float]] = {}

        for entry, fts_score in fts_results:
            eid = entry.id or 0
            candidates[eid] = (entry, 0.0, fts_score, 0.0)

        for entry, vec_score in vec_results:
            eid = entry.id or 0
            if eid in candidates:
                e, _, fs, ls = candidates[eid]
                candidates[eid] = (e, vec_score, fs, ls)
            else:
                candidates[eid] = (entry, vec_score, 0.0, 0.0)

        q_tags = query_tags or []
        # 标签候选通道：query_tags 非空时把标签交集命中的记忆并入候选池，
        # 纯标签意图（文本/向量不命中）不再丢失结果
        if q_tags:
            tag_entries = await self.search_by_tags(q_tags, limit=pool_size)
            if tag_entries:
                try:
                    from .. import metrics

                    metrics.incr("recall.tag_hits", len(tag_entries))
                except Exception:
                    pass
            for entry in tag_entries:
                eid = entry.id or 0
                if eid not in candidates:
                    candidates[eid] = (entry, 0.0, 0.0, 0.0)

        if tag_query.active:
            db = await self._conn.get_db()
            tag_sql, tag_params = tag_query.sql()
            rows = await (
                await db.execute(
                    f"SELECT {MEM_COLUMNS} FROM memories WHERE importance>0 AND ({tag_sql}) ORDER BY ts_ns DESC LIMIT ?",
                    (*tag_params, pool_size),
                )
            ).fetchall()
            for row in rows:
                entry = row_to_entry(row)
                candidates.setdefault(entry.id or 0, (entry, 0.0, 0.0, 0.0))
            candidates = {key: value for key, value in candidates.items() if tag_query.matches(value[0].tags)}

        tag_df: Dict[str, int] = {}
        total_docs = 0
        if q_tags:
            tag_df, total_docs = await self.get_tag_df()

        w_semantic = _w("memory_search_w_semantic", 0.7)
        w_decay = _w("memory_search_w_decay", 0.3)
        w_vec = _w("memory_search_w_vec", 0.6)
        w_fts = _w("memory_search_w_fts", 0.25)
        w_tag = _w("memory_search_w_tag", 0.15)
        w_recency = _w("memory_search_w_recency", 0.5)
        w_frequency = _w("memory_search_w_frequency", 0.3)
        w_importance = _w("memory_search_w_importance", 0.2)

        # 通道内归一化基准（cosine 与 BM25 映射分尺度不同，先归一再加权）
        max_vec = max((s for _, s in vec_results), default=0.0)
        max_text = 0.0
        for _, _, fs, ls in candidates.values():
            if fs > max_text:
                max_text = fs
            if ls > max_text:
                max_text = ls
        # 通道缺席权重重分配：向量/关键词/标签任一路无候选时，其权重按比例
        # 让给有结果的通道——缺席通道的死权重会让衰减分主导排序，文本匹配
        # 强度失去区分度（embedder 不可用时尤其明显）
        w_vec_a = w_vec if max_vec > 0 else 0.0
        w_fts_a = w_fts if max_text > 0 else 0.0
        w_tag_a = w_tag if q_tags else 0.0
        w_sum = w_vec_a + w_fts_a + w_tag_a
        if w_sum > 0:
            w_vec_a, w_fts_a, w_tag_a = w_vec_a / w_sum, w_fts_a / w_sum, w_tag_a / w_sum
        # 小语料特判：候选总数不超过返回上限时忽略 min_score（小文档集
        # 绝对分不可靠，过滤只会丢结果）
        bypass_threshold = len(candidates) <= limit

        results: list[tuple[MemoryEntry, float]] = []
        winners: dict[int, str] = {}
        for entry, vec_score, fts_score, like_score in candidates.values():
            tag_score = idf_tag_score(q_tags, entry.tags, tag_df, total_docs) if q_tags else 0.0
            text_score = max(fts_score, like_score)
            vec_part = _normalize(vec_score, max_vec) * w_vec_a
            fts_part = _normalize(text_score, max_text) * w_fts_a
            tag_part = tag_score * w_tag_a
            semantic = vec_part + fts_part + tag_part

            recency = time_decay(entry.timestamp)
            freq = frequency_boost(entry.access_count, max_access)
            decay = recency * w_recency + freq * w_frequency + entry.importance * w_importance

            # temporal_scope 时间语义：超期 state/episode（状态已变/事件已过）
            # 整体降权——不再以"现状"的排位被召回，聊往事时仍可低权命中
            final = (semantic * w_semantic + decay * w_decay) * temporal_weight(entry.metadata, entry.timestamp)
            if bypass_threshold or final >= min_score:
                results.append((entry, final))
                parts = {"vec": vec_part, "fts": fts_part, "tag": tag_part}
                winner = max(parts, key=lambda key: parts[key])
                if parts[winner] > 0 and entry.id is not None:
                    winners[entry.id] = winner

        results.sort(key=lambda x: x[1], reverse=True)
        results = results[:limit]
        # 通道贡献观测：终选结果按分通道最高者计数（memory_stats 查询，不进上下文）
        try:
            from .. import metrics

            if results:
                for channel in ("vec", "fts", "tag"):
                    metrics.incr(f"recall.win_{channel}", sum(winners.get(entry.id or 0) == channel for entry, _ in results))
        except Exception:
            pass
        return results

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
        require_tags: Optional[list[str]] = None,
        tag_query: TagQuery | None = None,
    ) -> list[MemorySearchResult]:
        """统一搜索：同时检索 memories 表和 chunks 表，合并排序返回。

        require_tags 为硬过滤（仅 memories 轨生效；文件 chunk 无标签体系，
        启用硬过滤时不再返回文件结果）。
        """
        tag_query = tag_query or TagQuery(all_of=tuple(require_tags or ()))
        pool_size = limit * 3

        # memories 搜索 + chunks 搜索并行执行
        # （chunks 的 LIKE 兜底由 search_chunks_fts 内部覆盖，不再重复一路）
        async def _empty_chunk_vec() -> list[Dict[str, Any]]:
            return []

        async def _empty_chunk_fts() -> list[Dict[str, Any]]:
            return []

        mem_coro = self.search_hybrid(
            query=query,
            query_vec=query_vec,
            query_tags=query_tags,
            limit=pool_size,
            min_score=min_score,
            tag_query=tag_query,
        )
        chunk_vec_coro = (
            self._file_index.search_chunks_vector(query_vec, limit=pool_size, min_score=0.05, query=query)
            if query_vec and not tag_query.active
            else _empty_chunk_vec()
        )
        chunk_fts_coro = (
            self._file_index.search_chunks_fts(query, limit=pool_size) if not tag_query.active else _empty_chunk_fts()
        )

        mem_results, chunk_vec_results, chunk_fts_results = await asyncio.gather(
            mem_coro,
            chunk_vec_coro,
            chunk_fts_coro,
        )

        # 合并 chunks 候选
        chunk_candidates: Dict[str, Dict[str, Any]] = {}
        for r in chunk_fts_results:
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
        w_vec = _w("memory_search_w_vec", 0.6)
        w_chunk_text = _w("memory_search_w_fts", 0.25) + _w("memory_search_w_tag", 0.15)
        max_chunk_vec = max((ch["vec_score"] for ch in chunk_candidates.values()), default=0.0)
        max_chunk_text = max((ch["text_score"] for ch in chunk_candidates.values()), default=0.0)
        # 通道缺席权重重分配（与 memories 侧同规则）
        if max_chunk_vec <= 0 and max_chunk_text > 0:
            w_vec, w_chunk_text = 0.0, w_vec + w_chunk_text
        elif max_chunk_text <= 0 and max_chunk_vec > 0:
            w_vec, w_chunk_text = w_vec + w_chunk_text, 0.0
        for cid, ch in chunk_candidates.items():
            semantic = (
                _normalize(ch["vec_score"], max_chunk_vec) * w_vec
                + _normalize(ch["text_score"], max_chunk_text) * w_chunk_text
            )
            decay_mult = file_temporal_decay(ch.get("path", ""))
            final = semantic * decay_mult
            if final >= min_score:
                chunk_results.append(
                    MemorySearchResult(
                        id=cid,
                        path=ch.get("path", ""),
                        start_line=ch.get("start_line", 0),
                        end_line=ch.get("end_line", 0),
                        snippet=ch.get("snippet", ""),
                        score=final,
                        source="file",
                        provenance=ch.get("provenance", {}),
                    )
                )

        unified: list[MemorySearchResult] = []
        for entry, score in mem_results:
            unified.append(MemorySearchResult.from_entry(entry, score))

        unified.extend(chunk_results)
        unified.sort(key=lambda r: r.score, reverse=True)
        return unified[:limit]

    # ------------------------------------------------------------------
    # 遗忘层兜底检索（归档 + 墓碑："似曾相识"通道）
    # ------------------------------------------------------------------

    async def search_forgotten(
        self,
        query: str,
        query_vec: Optional[list[float]] = None,
        *,
        limit: int = 3,
        min_score: float = 0.3,
    ) -> list[Dict[str, Any]]:
        """遗忘层统一检索：归档记忆（可恢复）+ 墓碑 gist（仅痕迹），合并排序。

        归档向量余弦为强信号（可达 1.0）；关键词命中为弱信号（归档基准 0.35，
        墓碑上限 0.3）——弱信号只有主召回无果时才应被调用方采纳。
        Returns: [{"kind": "archived"|"tombstone", "score": float, ...}] 降序。
        """
        archived, tombstones = await asyncio.gather(
            self.search_archived(query, query_vec, limit=limit, min_score=min_score),
            self.search_tombstones(query, limit=limit),
        )
        items: list[Dict[str, Any]] = [{"kind": "archived", **row} for row in archived]
        items.extend({"kind": "tombstone", **row} for row in tombstones)
        items.sort(key=lambda item: item["score"], reverse=True)
        return items[:limit]

    async def search_archived(
        self,
        query: str,
        query_vec: Optional[list[float]] = None,
        *,
        limit: int = 3,
        min_score: float = 0.3,
    ) -> list[Dict[str, Any]]:
        """归档记忆检索：批量精确向量评分 + 关键词 LIKE 两路合并。

        归档表无 FTS/vec 索引（量级小、受保留期约束），不参与常规索引维护；
        关键词命中给固定基准分 0.35，低于向量强匹配，避免弱命中喧宾夺主。
        Returns: [{"id", "type", "content", "tags", "archived_at", "reason", "score"}] 降序。
        """
        if limit <= 0:
            return []
        scored: Dict[int, Dict[str, Any]] = {}

        if query_vec:
            db = await self._conn.get_db()
            last_id = 0
            while True:
                cursor = await db.execute(
                    "SELECT id, type, content, source, ts_ns, metadata_json, tags_json, importance, archived_at_ns, "
                    "archive_reason, embedding_blob FROM memories_archive "
                    "WHERE embedding_blob IS NOT NULL AND id > ? ORDER BY id LIMIT ?",
                    (last_id, 500),
                )
                rows = list(await cursor.fetchall())
                if not rows:
                    break
                last_id = int(rows[-1]["id"])
                for item in await asyncio.to_thread(
                    self._score_archived_batch,
                    rows,
                    query_vec,
                    min_score,
                    limit,
                ):
                    scored[item["id"]] = item
                scored = dict(sorted(scored.items(), key=lambda item: item[1]["score"], reverse=True)[:limit])
                if len(rows) < 500:
                    break

        for item in await self._match_archived_keywords(query, limit * 4):
            if item["score"] > scored.get(item["id"], {}).get("score", -1.0):
                scored[item["id"]] = item

        merged = sorted(scored.values(), key=lambda x: x["score"], reverse=True)
        return merged[:limit]

    @staticmethod
    def _score_archived_batch(
        rows: list[Any],
        query_vec: list[float],
        min_score: float,
        limit: int,
    ) -> list[Dict[str, Any]]:
        """对一批归档行计算余弦相似度（纯 CPU，供 to_thread 调用）。"""
        ranked = rank_embedding_blobs(query_vec, [row["embedding_blob"] for row in rows],
                                      limit=limit, min_score=min_score)
        return [{**_archived_row_dict(rows[index]), "score": score} for index, score in ranked]

    async def _match_archived_keywords(
        self,
        query: str,
        limit: int,
    ) -> list[Dict[str, Any]]:
        """归档内容关键词 LIKE 匹配（固定基准分 0.35 的弱信号）。"""
        keywords = extract_like_keywords(query)
        if not keywords:
            return []
        db = await self._conn.get_db()
        conditions = " OR ".join(r"content LIKE ? ESCAPE '\'" for _ in keywords)
        cursor = await db.execute(
            "SELECT id, type, content, source, ts_ns, metadata_json, tags_json, importance, archived_at_ns, "
            "archive_reason, NULL AS embedding_blob FROM memories_archive "
            f"WHERE {conditions} ORDER BY archived_at_ns DESC LIMIT ?",
            [*[f"%{escape_like(kw)}%" for kw in keywords], limit],
        )
        return [{**_archived_row_dict(row), "score": 0.35} for row in await cursor.fetchall()]

    async def search_tombstones(
        self,
        query: str,
        *,
        limit: int = 3,
    ) -> list[Dict[str, Any]]:
        """墓碑检索：已物理删除记忆的 gist 关键词匹配。

        分数 = 命中关键词比例 × 0.3——刻意压在归档弱信号（0.35）之下，
        gist 是截断梗概，匹配可信度最低。
        Returns: [{"id", "memory_id", "type", "gist", "tags", "reason", "purged_at", "score"}] 降序。
        """
        keywords = extract_like_keywords(query)
        if not keywords:
            return []
        db = await self._conn.get_db()
        conditions = " OR ".join(r"gist LIKE ? ESCAPE '\'" for _ in keywords)
        cursor = await db.execute(
            "SELECT id, memory_id, type, gist, tags_json, purge_reason, purged_at_ns "
            f"FROM memories_tombstone WHERE {conditions} "
            "ORDER BY purged_at_ns DESC LIMIT 100",
            [f"%{escape_like(kw)}%" for kw in keywords],
        )
        results: list[Dict[str, Any]] = []
        for row in await cursor.fetchall():
            gist = str(row["gist"])
            matched = sum(1 for kw in keywords if kw in gist)
            if matched <= 0:
                continue
            tags: list[str] = []
            try:
                parsed = json.loads(row["tags_json"]) if row["tags_json"] else []
                tags = [str(t) for t in parsed]
            except (ValueError, TypeError):
                pass
            results.append(
                {
                    "id": int(row["id"]),
                    "memory_id": int(row["memory_id"]),
                    "type": str(row["type"]),
                    "gist": gist,
                    "tags": tags,
                    "reason": str(row["purge_reason"] or ""),
                    "purged_at": int(row["purged_at_ns"]) / 1e9,
                    "score": matched / len(keywords) * 0.3,
                }
            )
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    # ------------------------------------------------------------------
    # 去重
    # ------------------------------------------------------------------

    async def has_similar_content(
        self, content: str, min_overlap: float = 0.6, *, tags: Optional[list[str]] = None
    ) -> bool:
        """检查是否已存在语义相近的记忆（基于 FTS 候选 + bigram 相似度）。"""
        results = await self.search_fts(content, limit=5)
        content_clean = content.replace(" ", "").replace("\n", "")
        for entry, _ in results:
            if tags is not None and (
                {t for t in entry.tags if t.startswith(("user:", "group:"))}
                != {t for t in tags if t.startswith(("user:", "group:"))}
            ):
                continue
            existing_clean = entry.content.replace(" ", "").replace("\n", "")
            if content_clean in existing_clean or existing_clean in content_clean:
                return True
            if bigram_similarity(content_clean, existing_clean) >= min_overlap:
                return True
        return False


# ------------------------------------------------------------------
# 配置注册（混合评分权重）
# ------------------------------------------------------------------

_SEARCH_CONFIGS = {
    "memory/search": {
        "memory_search_w_semantic": {
            "description": "语义分权重（与衰减分互补）",
            "default": 0.7,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "memory_search_w_decay": {
            "description": "衰减分权重（时间/频率/重要性）",
            "default": 0.3,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "memory_search_w_vec": {
            "description": "语义分内向量通道权重",
            "default": 0.6,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "memory_search_w_fts": {
            "description": "语义分内关键词通道权重",
            "default": 0.25,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "memory_search_w_tag": {
            "description": "语义分内标签通道权重",
            "default": 0.15,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "memory_search_w_recency": {
            "description": "衰减分内时间新近度权重",
            "default": 0.5,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "memory_search_w_frequency": {
            "description": "衰减分内访问频率权重",
            "default": 0.3,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
        "memory_search_w_importance": {
            "description": "衰减分内重要性权重",
            "default": 0.2,
            "advanced": True,
            "value_type": "range",
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
    },
}

try:
    from core.config import register_configs_safe

    register_configs_safe(_SEARCH_CONFIGS)
except Exception:
    pass
