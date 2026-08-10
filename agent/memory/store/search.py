"""记忆搜索管线：FTS5 / 向量 / 混合评分 / 标签关联 / 双轨统一搜索。

语义评分 (Vector + FTS + TagMatch) × 衰减评分 (Recency + Frequency + Importance)。
向量检索优先 sqlite-vec ANN 索引，不可用时 keyset 分页（id 游标）全表扫描；
FTS 不可用或异常时回退 LIKE（ESCAPE 转义）。chunks 侧检索委托 FileIndexStore。
连接由 MemoryConnectionManager 提供，本模块不自建连接。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from core.log import log

from ..memory_types import MemoryEntry, MemorySearchResult
from ..memory_utils import cosine_similarity, pack_embedding
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


class SearchEngine:
    """memories 表多路检索与 memories + chunks 双轨统一搜索。"""

    def __init__(self, conn: MemoryConnectionManager, file_index: FileIndexStore) -> None:
        self._conn = conn
        self._file_index = file_index
        self._tag_intel = TagIntelligence(conn)

    # ------------------------------------------------------------------
    # 标签索引
    # ------------------------------------------------------------------

    async def list_tags(self) -> Dict[str, int]:
        """聚合所有活跃标签及其出现次数（importance=0 的已合并条目不计）。"""
        df, _ = await self._tag_intel.tag_stats()
        return dict(df)

    async def get_tag_df(self) -> tuple[Dict[str, int], int]:
        """标签文档频率 + 活跃记忆总数（IDF 评分用；TagIntelligence TTL 缓存）。"""
        return await self._tag_intel.tag_stats()

    async def cooccurring_tags(
        self, seed_tags: list[str], *, limit: int = 3,
    ) -> list[tuple[str, float]]:
        """共现联想：与种子标签同现过的其他关联标签（共现次数×IDF 降序）。"""
        return await self._tag_intel.cooccurring_tags(seed_tags, limit=limit)

    async def extract_query_mentions(self, query: str, *, limit: int = 3) -> list[str]:
        """从查询文本识别已知实体/话题提及，返回对应标签。"""
        return await self._tag_intel.extract_mentions(query, limit=limit)

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
        conditions = " AND ".join(
            "EXISTS (SELECT 1 FROM json_each(memories.tags_json) WHERE value = ?)"
            for _ in tags
        )
        params: list[Any] = list(tags)
        params.append(limit)
        cursor = await db.execute(
            f"SELECT {MEM_COLUMNS} FROM memories WHERE importance > 0 AND {conditions} "
            "ORDER BY ts_ns DESC LIMIT ?",
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
        conditions = " OR ".join(
            "EXISTS (SELECT 1 FROM json_each(memories.tags_json) WHERE value = ?)"
            for _ in tags
        )
        cursor = await db.execute(
            f"SELECT {MEM_COLUMNS} FROM memories WHERE importance > 0 AND ({conditions}) "
            "ORDER BY ts_ns DESC LIMIT 200",
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

    async def search_fts(self, query: str, limit: int = 10) -> list[tuple[MemoryEntry, float]]:
        """FTS5 关键词搜索，返回 (entry, bm25_score) 列表。"""
        db = await self._conn.get_db()
        if not self._conn.fts_available:
            return await self._search_like(query, limit)

        fts_query = build_fts_query(query)
        if not fts_query:
            return []

        try:
            fts_select = ", ".join(f"m.{c.strip()}" for c in MEM_COLUMNS.split(","))
            cursor = await db.execute(
                f"""
                SELECT {fts_select}, rank
                FROM memories_fts f
                JOIN memories m ON m.id = f.rowid
                WHERE memories_fts MATCH ? AND m.importance > 0
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit),
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
            return await self._search_like(query, limit)

    async def _search_like(self, query: str, limit: int) -> list[tuple[MemoryEntry, float]]:
        """LIKE 回退搜索：将查询拆成关键词段，用 OR 匹配。"""
        db = await self._conn.get_db()
        keywords = extract_like_keywords(query)
        if not keywords:
            return []
        conditions = " OR ".join(r"content LIKE ? ESCAPE '\'" for _ in keywords)
        params: list[Any] = [f"%{escape_like(kw)}%" for kw in keywords]
        params.append(limit)
        cursor = await db.execute(
            f"SELECT {MEM_COLUMNS} FROM memories WHERE importance > 0 AND ({conditions}) "
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
    ) -> list[tuple[MemoryEntry, float]]:
        """向量搜索：优先走 sqlite-vec ANN 索引，不可用时分批全表扫描。"""
        if self._conn.vec_available and self._conn.vec_dims is not None:
            try:
                return await self._search_vector_vec(query_vec, limit, min_score)
            except Exception as exc:
                log(f"vec 检索失败，降级全表扫描: {exc}", "WARNING")

        batch_size: int = get_memory_config_value("vector_search_batch_size", 500)

        # keyset 分页（id 游标）顺序扫描：避免 LIMIT/OFFSET 深页 O(n²) IO；
        # 共享连接上并发 gather 实际串行，顺序循环语义更真实且更省连接占用
        merged: list[tuple[MemoryEntry, float]] = []
        last_id = 0
        while True:
            rows, last_id = await self._fetch_vector_batch(last_id, batch_size)
            if not rows:
                break
            scored = await asyncio.to_thread(
                self._score_vector_rows, rows, query_vec, min_score,
            )
            merged.extend(scored)
            if len(rows) < batch_size:
                break
            # 定期修剪，避免大库扫描时中间列表无限增长
            if len(merged) > limit * 4:
                merged.sort(key=lambda x: x[1], reverse=True)
                merged = merged[:limit]

        merged.sort(key=lambda x: x[1], reverse=True)
        return merged[:limit]

    async def _fetch_vector_batch(
        self, last_id: int, batch_size: int,
    ) -> tuple[list[Any], int]:
        """按 id 游标取一批带向量的记忆（keyset 分页），返回 (行, 新游标)。"""
        db = await self._conn.get_db()
        cursor = await db.execute(
            f"SELECT {MEM_COLUMNS} FROM memories WHERE embedding_blob IS NOT NULL "
            "AND importance > 0 AND id > ? ORDER BY id LIMIT ?",
            (last_id, batch_size),
        )
        rows = await cursor.fetchall()
        new_last = int(rows[-1]["id"]) if rows else last_id
        return rows, new_last

    def _score_vector_rows(
        self,
        rows: list[Any],
        query_vec: list[float],
        min_score: float,
    ) -> list[tuple[MemoryEntry, float]]:
        """对一批记忆行计算余弦相似度（纯 CPU，供 to_thread 调用）。"""
        scored: list[tuple[MemoryEntry, float]] = []
        for row in rows:
            entry = row_to_entry(row)
            if not entry.embedding:
                continue
            score = cosine_similarity(query_vec, entry.embedding)
            if score >= min_score:
                scored.append((entry, score))
        return scored

    async def _search_vector_vec(
        self,
        query_vec: list[float],
        limit: int,
        min_score: float,
    ) -> list[tuple[MemoryEntry, float]]:
        """基于 sqlite-vec 的 ANN 检索（cosine distance → similarity）。"""
        db = await self._conn.get_db()
        cursor = await db.execute(
            "SELECT rowid, distance FROM memories_vec "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (pack_embedding(query_vec), max(1, limit)),
        )
        vec_rows = await cursor.fetchall()
        if not vec_rows:
            return []

        ids = [int(r["rowid"]) for r in vec_rows]
        placeholders = ",".join("?" for _ in ids)
        cursor = await db.execute(
            f"SELECT {MEM_COLUMNS} FROM memories WHERE importance > 0 AND id IN ({placeholders})",
            ids,
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
    ) -> list[tuple[MemoryEntry, float]]:
        """混合搜索：向量 + FTS 两路并行，取并集后统一评分。

        LIKE 全表扫描兜底由 search_fts 内部覆盖（FTS 不可用或异常时自动回退），
        不再作为独立一路重复执行（FTS 可用时 LIKE 与 FTS 命中高度重叠，
        却每次召回都付出一次全表扫描成本）。
        require_tags 为硬过滤：记忆必须包含全部指定标签才进入评分。
        """
        pool_size = limit * 5
        t0 = time.monotonic()

        async def _empty_vec() -> list[tuple[MemoryEntry, float]]:
            return []

        # 两路并行搜索
        vec_coro = self.search_vector(query_vec, limit=pool_size, min_score=0.05) if query_vec else _empty_vec()
        fts_coro = self.search_fts(query, limit=pool_size)

        vec_results, fts_results = await asyncio.gather(vec_coro, fts_coro)

        # 运行指标：召回通道命中观测（进程内累计，心跳状态区块展示）
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

        tag_df: Dict[str, int] = {}
        total_docs = 0
        if q_tags:
            tag_df, total_docs = await self.get_tag_df()
        hard_tags = set(require_tags or [])

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
        for entry, vec_score, fts_score, like_score in candidates.values():
            if hard_tags and not hard_tags.issubset(entry.tags):
                continue
            tag_score = idf_tag_score(q_tags, entry.tags, tag_df, total_docs) if q_tags else 0.0
            text_score = max(fts_score, like_score)
            semantic = (
                _normalize(vec_score, max_vec) * w_vec_a
                + _normalize(text_score, max_text) * w_fts_a
                + tag_score * w_tag_a
            )

            recency = time_decay(entry.timestamp)
            freq = frequency_boost(entry.access_count, max_access)
            decay = recency * w_recency + freq * w_frequency + entry.importance * w_importance

            final = semantic * w_semantic + decay * w_decay
            if bypass_threshold or final >= min_score:
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
        require_tags: Optional[list[str]] = None,
    ) -> list[MemorySearchResult]:
        """统一搜索：同时检索 memories 表和 chunks 表，合并排序返回。

        require_tags 为硬过滤（仅 memories 轨生效；文件 chunk 无标签体系，
        启用硬过滤时不再返回文件结果）。
        """
        pool_size = limit * 3

        # memories 搜索 + chunks 搜索并行执行
        # （chunks 的 LIKE 兜底由 search_chunks_fts 内部覆盖，不再重复一路）
        async def _empty_chunk_vec() -> list[Dict[str, Any]]:
            return []

        async def _empty_chunk_fts() -> list[Dict[str, Any]]:
            return []

        mem_coro = self.search_hybrid(
            query=query, query_vec=query_vec, query_tags=query_tags,
            limit=pool_size, min_score=min_score, require_tags=require_tags,
        )
        chunk_vec_coro = (
            self._file_index.search_chunks_vector(query_vec, limit=pool_size, min_score=0.05)
            if query_vec and not require_tags else _empty_chunk_vec()
        )
        chunk_fts_coro = (
            self._file_index.search_chunks_fts(query, limit=pool_size)
            if not require_tags else _empty_chunk_fts()
        )

        mem_results, chunk_vec_results, chunk_fts_results = await asyncio.gather(
            mem_coro, chunk_vec_coro, chunk_fts_coro,
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
            activity_date = str(entry.metadata.get("activity_date", ""))
            unified.append(MemorySearchResult(
                id=f"mem:{entry.id}",
                snippet=entry.content[:700],
                score=score,
                source="memory",
                memory_type=entry.memory_type.value,
                tags=entry.tags,
                timestamp=entry.timestamp,
                sensitivity=str(entry.metadata.get("sensitivity", "normal")),
                provenance=({"activity_date": activity_date} if activity_date else {}),
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
            if bigram_similarity(content_clean, existing_clean) >= min_overlap:
                return True
        return False


# ------------------------------------------------------------------
# 配置注册（混合评分权重）
# ------------------------------------------------------------------

_SEARCH_CONFIGS = {
    "记忆": {
        "memory_search_w_semantic": {
            "description": "混合评分：语义分权重（与衰减分互补）",
            "default": 0.7,
        },
        "memory_search_w_decay": {
            "description": "混合评分：衰减分权重（时间/频率/重要性）",
            "default": 0.3,
        },
        "memory_search_w_vec": {
            "description": "混合评分：语义分内向量通道权重",
            "default": 0.6,
        },
        "memory_search_w_fts": {
            "description": "混合评分：语义分内关键词通道权重",
            "default": 0.25,
        },
        "memory_search_w_tag": {
            "description": "混合评分：语义分内标签通道权重",
            "default": 0.15,
        },
        "memory_search_w_recency": {
            "description": "混合评分：衰减分内时间新近度权重",
            "default": 0.5,
        },
        "memory_search_w_frequency": {
            "description": "混合评分：衰减分内访问频率权重",
            "default": 0.3,
        },
        "memory_search_w_importance": {
            "description": "混合评分：衰减分内重要性权重",
            "default": 0.2,
        },
    },
}

try:
    from core.config import register_configs_safe
    register_configs_safe(_SEARCH_CONFIGS)
except Exception:
    pass
