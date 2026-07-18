"""MemoryRetriever：被动记忆召回，从对话上下文中自动检索相关记忆注入上下文。

使用 search_unified 实现双轨召回（memories 表 + MD 文件 chunks），
与 tools.recall 主动召回保持一致的搜索范围。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from core.log import log
from .embedder import Embedder
from .memory_store import MemoryStore
from .memory_types import MemorySearchResult, MemoryType

DEFAULT_TOP_K = 5
DEFAULT_MIN_SCORE = 0.1


class MemoryRetriever:
    """从 MemoryStore 中根据对话上下文检索相关记忆（被动召回）。"""

    def __init__(
        self,
        store: MemoryStore,
        embedder: Embedder,
        *,
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._top_k = top_k
        self._min_score = min_score
        self._rerank_client: Optional[object] = None
        from .cognee.config import load_cognee_config
        self._cognee_config = load_cognee_config()

    def set_rerank_client(self, client: object) -> None:
        """Set reranker (MediaClient instance) for post-search reranking."""
        self._rerank_client = client

    async def recall(
        self,
        conversation: List[Dict],
        *,
        top_k: Optional[int] = None,
        entity_scope: str = "",
        related_scopes: Optional[List[str]] = None,
    ) -> List[Dict]:
        """根据对话上下文召回相关记忆，返回 messages 格式列表。

        同时搜索 memories 表和 MD 文件 chunks（双轨统一召回）。
        related_scopes 用于群聊场景下加载活跃成员的画像。
        """
        try:
            from agent.config import get_mind_config
            mind_config = get_mind_config()
            k = top_k or mind_config.memory_recall_top_k
            min_score = mind_config.memory_recall_min_score
        except Exception:
            k = top_k or self._top_k
            min_score = self._min_score

        all_scopes: List[str] = []
        if entity_scope:
            all_scopes.append(entity_scope)
        for s in (related_scopes or []):
            if s and s not in all_scopes:
                all_scopes.append(s)
        entity_msgs = await self._load_entity_profiles(all_scopes) if all_scopes else []

        query = self._extract_query(conversation)
        if not query:
            log("💾 被动召回: 无有效查询，回退近期记忆", tag="思维")
            fallback = await self._fallback_recent(k)
            return entity_msgs + fallback

        log(f"💾 被动召回: \"{query[:50]}\" (embedding={'是' if self._embedder.available else '否'})", tag="思维")

        query_vec: Optional[list[float]] = None
        if self._embedder.available:
            query_vec = await self._embedder.embed_one(query)

        from .cognee.fusion import federated_search
        from .cognee.runtime import get_cognee_client
        results = await federated_search(
            self._store.search_unified(
                query=query,
                query_vec=query_vec,
                limit=k * self._cognee_config.recall_pool_multiplier,
                min_score=min_score,
            ),
            query=query,
            client=get_cognee_client(),
            config=self._cognee_config,
            limit=k,
            entity_scope=entity_scope,
        )

        # 多窗口补充：以最近一条用户消息为焦点查询，融合主查询结果
        focus_query = self._extract_focus_query(conversation)
        if focus_query and focus_query != query:
            focus_vec = await self._embedder.embed_one(focus_query) if self._embedder.available else None
            focus_results = await self._store.search_unified(
                query=focus_query,
                query_vec=focus_vec,
                limit=k,
                min_score=min_score,
            )
            results = self._merge_results(results, focus_results, limit=k * 2)

        # 时间感知：检测到时间引用词时，提升事件记忆与近期记忆权重
        if self._detect_time_reference(query):
            results = self._apply_temporal_boost(results)

        # 上下文加权：与当前对话实体（用户/群）标签匹配的记忆加权
        results = self._apply_scope_boost(results, all_scopes)

        # 关联扩展：沿标签网络发现一跳关联记忆（想到一件事 → 联想到相关的事）
        results = await self._expand_associations(results, limit=k)

        if not results:
            log("💾 统一搜索无结果，回退近期记忆", tag="思维")
            fallback = await self._fallback_recent(k)
            return entity_msgs + fallback

        # Rerank if available
        if self._rerank_client and len(results) > 1:
            results = await self._apply_rerank(query, results, k)

        # 隐式反馈：仅对 memories 表结果更新访问计数
        mem_ids = [
            int(r.id.split(":")[1])
            for r in results
            if r.source == "memory" and r.id.startswith("mem:")
        ]
        if mem_ids:
            await self._store.record_access(mem_ids)

        for r in results:
            src_label = (
                f"[{r.source}]"
                if r.source != "memory"
                else f"[{r.memory_type or 'memory'}]"
            )
            tag_str = f" [{','.join(r.tags)}]" if r.tags else ""
            path_str = f" {r.path}" if r.path else ""
            log(f"  💡 {src_label}{tag_str}{path_str} score={r.score:.2f}: {r.snippet[:50]}", tag="思维")

        return entity_msgs + self._format_unified_results(results)

    @staticmethod
    async def _resolve_scope_alias(scope: str) -> str:
        """将 scope 解析到 primary（若存在 alias）。"""
        try:
            from services._runtime import require_runtime
            sqlite = require_runtime().data_center.sqlite
            scope_type = "user" if scope.startswith("user_") else "group"
            scope_id = scope.split("_", 1)[1] if "_" in scope else scope
            primary = await sqlite.resolve_alias(scope_type, scope_id)
            if primary:
                return f"{primary[0]}_{primary[1]}"
        except Exception:
            pass
        return scope

    async def _load_entity_profiles(self, scopes: List[str]) -> List[Dict]:
        """加载多个实体的画像记忆，按 scope 分组标注。

        多个 alias 指向同一 primary 时自动去重，仅加载一次。
        """
        resolved_map: dict[str, str] = {}
        for scope in scopes:
            primary = await self._resolve_scope_alias(scope)
            if primary not in resolved_map:
                resolved_map[primary] = scope

        all_parts: List[str] = []
        for primary_scope, original_scope in resolved_map.items():
            entity_id = primary_scope.split("_", 1)[1] if "_" in primary_scope else primary_scope
            if not entity_id:
                continue

            source = f"entity_{entity_id}"
            entries = await self._store.list_recent(
                limit=2, memory_type=MemoryType.ENTITY, source=source,
            )
            if not entries:
                tag = f"user:{entity_id}" if primary_scope.startswith("user_") else f"group:{entity_id}"
                entries = await self._store.search_by_tags([tag], limit=3)

            if not entries:
                continue

            scope_label = f"[uid:{entity_id}]" if primary_scope.startswith("user_") else f"[group_id:{entity_id}]"
            for e in entries:
                all_parts.append(f"{scope_label}\n{e.content}")
            log(f"实体画像加载: {source} ({len(entries)} 条)", "DEBUG", tag="思维")

        if not all_parts:
            return []
        log(f"实体画像注入: {len(scopes)} 个 scope, {len(all_parts)} 条画像", tag="思维")
        return [{"role": "system", "content": "[系统注入·人物画像] 以下为相关实体的画像信息：\n" + "\n---\n".join(all_parts)}]

    async def _fallback_recent(self, limit: int) -> List[Dict]:
        entries = await self._store.list_recent(limit=limit)
        if entries:
            log(f"💾 回退: 取最近 {len(entries)} 条记忆", tag="思维")
        if not entries:
            return []
        lines = [e.content for e in entries]
        return [{"role": "system", "content": "[近期记忆]\n" + "\n---\n".join(lines)}]

    @staticmethod
    def _extract_query(conversation: List[Dict], max_chars: int = 500) -> str:
        """从对话中提取检索查询，过滤无意义短文本，优先使用 user 消息。"""
        texts: list[str] = []
        for msg in reversed(conversation):
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            cleaned = _strip_tags(content)
            if not cleaned or len(cleaned) < 4:
                continue
            if role == "assistant":
                cleaned = cleaned[:100]
            texts.append(cleaned)
            if len(texts) >= 8:
                break
        if not texts:
            return ""
        return " ".join(reversed(texts))[:max_chars]

    @staticmethod
    def _extract_focus_query(conversation: List[Dict], max_chars: int = 200) -> str:
        """提取焦点查询：最近一条用户消息（即时窗口，捕捉当前话题）。"""
        for msg in reversed(conversation):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            cleaned = _strip_tags(content)
            if len(cleaned) >= 4:
                return cleaned[:max_chars]
        return ""

    @staticmethod
    def _merge_results(
            primary: list[MemorySearchResult],
            secondary: list[MemorySearchResult],
            *,
            limit: int,
    ) -> list[MemorySearchResult]:
        """融合两路召回结果：同 id 取最高分，按分数降序截断。"""
        merged: dict[str, MemorySearchResult] = {}
        for r in list(primary) + list(secondary):
            existing = merged.get(r.id)
            if existing is None or r.score > existing.score:
                merged[r.id] = r
        results = sorted(merged.values(), key=lambda r: r.score, reverse=True)
        return results[:limit]

    # 时间引用词：检测到这些词时提升事件记忆与近期记忆权重
    _TIME_REFERENCE_WORDS = (
        "昨天", "前天", "上次", "之前", "最近", "刚才", "上周", "上周",
        "前几天", "以前", "曾经", "yesterday", "recently", "last time",
    )

    @staticmethod
    def _apply_scope_boost(
            results: list[MemorySearchResult],
            scopes: list[str],
            *,
            boost: float = 1.15,
    ) -> list[MemorySearchResult]:
        """上下文加权：标签命中当前对话实体（user:/group:）的记忆分数提升。"""
        scope_tags = set()
        for scope in scopes:
            if scope.startswith("user_"):
                scope_tags.add(f"user:{scope[5:]}")
            elif scope.startswith("group_"):
                scope_tags.add(f"group:{scope[6:]}")
        if not scope_tags:
            return results
        for r in results:
            if scope_tags & set(r.tags):
                r.score *= boost
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    async def _expand_associations(
            self,
            results: list[MemorySearchResult],
            *,
            limit: int,
            max_extra: int = 2,
    ) -> list[MemorySearchResult]:
        """关联扩展：沿标签网络发现一跳关联记忆，追加到结果尾部。

        人脑联想机制：想到一件事时，与之相关的人/事也会被唤起。
        关联记忆分数打折（0.75），并标记 associated=True 供呈现层区分。
        """
        if not results:
            return results

        # 收集关联边：主结果中的实体/主题标签
        assoc_tags: list[str] = []
        for r in results[:limit]:
            for tag in r.tags:
                if tag.startswith(("user:", "group:", "topic:")) and tag not in assoc_tags:
                    assoc_tags.append(tag)
        if not assoc_tags:
            return results

        existing_ids = {
            int(r.id.split(":")[1]) for r in results
            if r.source == "memory" and r.id.startswith("mem:")
        }
        related = await self._store.search_associative(
            assoc_tags, exclude_ids=existing_ids, limit=max_extra,
        )
        for entry, score in related:
            results.append(MemorySearchResult(
                id=f"mem:{entry.id}",
                snippet=entry.content[:500],
                score=round(score * 0.75, 4),
                source="memory",
                memory_type=entry.memory_type.value,
                tags=entry.tags,
                provenance={"associated": True, "timestamp": entry.timestamp},
            ))
        return results

    @classmethod
    def _detect_time_reference(cls, query: str) -> bool:
        """检测查询中是否包含时间引用（用户在回忆过去的事件）。"""
        lowered = query.lower()
        return any(w in lowered for w in cls._TIME_REFERENCE_WORDS)

    @staticmethod
    def _apply_temporal_boost(
            results: list[MemorySearchResult],
            *,
            episodic_boost: float = 1.2,
            recent_boost: float = 1.1,
            recent_hours: float = 168.0,  # 7 天
    ) -> list[MemorySearchResult]:
        """时间感知加权：事件记忆 + 近期记忆分数提升（参考 nekro 检索加权）。"""
        import time as _time
        now = _time.time()
        for r in results:
            if r.memory_type == MemoryType.EPISODIC.value:
                r.score *= episodic_boost
            ts = r.provenance.get("timestamp", 0) if r.provenance else 0
            if ts and (now - ts) / 3600.0 < recent_hours:
                r.score *= recent_boost
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    @staticmethod
    def _format_unified_results(results: list[MemorySearchResult]) -> List[Dict]:
        """将统一搜索结果格式化为注入消息，保留 score/type 元数据。"""
        if not results:
            return []

        mem_lines: list[str] = []
        file_lines: list[str] = []
        graph_lines: list[str] = []

        for r in results:
            snippet = r.snippet[:500]
            associated = r.provenance.get("associated") if r.provenance else False
            marker = "🔗" if associated else "💡"
            if r.source == "file":
                loc = f"[{r.path}:{r.start_line}-{r.end_line}]" if r.path else ""
                file_lines.append(f"{marker} {loc} score={r.score:.2f}: {snippet}")
            elif r.source.startswith("cognee_"):
                dataset = f" [{r.dataset_name}]" if r.dataset_name else ""
                graph_lines.append(
                    f"{marker} [{r.source}]{dataset} score={r.score:.2f}: {snippet}"
                )
            else:
                mtype = r.memory_type or "semantic"
                tag_str = f" [{','.join(r.tags)}]" if r.tags else ""
                mem_lines.append(
                    f"{marker} [{mtype}]{tag_str} score={r.score:.2f}: {snippet}"
                )

        parts: list[str] = []
        if mem_lines:
            parts.append(
                "[系统注入·记忆召回] 以下为系统自动检索的相关记忆，非用户消息"
                "（💡=直接相关，🔗=联想关联）：\n"
                + "\n".join(mem_lines)
            )
        if file_lines:
            parts.append(
                "[系统注入·知识检索] 以下为便签文件检索结果：\n"
                + "\n".join(file_lines)
            )
        if graph_lines:
            parts.append(
                "[系统注入·知识图谱召回] 以下为 Cognee 图谱与语义检索结果：\n"
                + "\n".join(graph_lines)
            )

        if not parts:
            return []
        return [{"role": "system", "content": "\n\n".join(parts)}]


    async def _apply_rerank(
        self,
        query: str,
        results: list[MemorySearchResult],
        top_k: int,
    ) -> list[MemorySearchResult]:
        """Apply reranker to reorder search results by relevance."""
        try:
            documents = [r.snippet for r in results]
            rerank_model = ""
            try:
                from agent.llm import get_llm_manager
                rerank_model = get_llm_manager().get_rerank_model() or ""
            except Exception:
                pass

            kwargs = {"query": query, "documents": documents, "top_n": top_k}
            if rerank_model:
                kwargs["model"] = rerank_model

            reranked = await self._rerank_client.rerank(**kwargs)  # type: ignore[union-attr]

            reordered: list[MemorySearchResult] = []
            for item in reranked:
                idx = item.get("index", 0)
                if 0 <= idx < len(results):
                    r = results[idx]
                    r.score = item.get("relevance_score", r.score)
                    reordered.append(r)

            log(f"rerank: {len(results)} -> {len(reordered)} results", "DEBUG", tag="思维")
            return reordered
        except Exception as exc:
            log(f"rerank failed, using original order: {exc}", "WARNING", tag="思维")
            return results


_TAG_RE = re.compile(r"\[(?:time|uid|group_id|name|nickname):[^\]]*\]")


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()
