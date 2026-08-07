"""MemoryRetriever：被动记忆召回，从对话上下文中自动检索相关记忆注入上下文。

使用 search_unified 实现双轨召回（memories 表 + MD 文件 chunks），
与 tools.recall 主动召回保持一致的搜索范围。
"""

from __future__ import annotations

import asyncio
import re
from typing import Dict, List, Optional, Tuple

from core.log import log

from .embedding import Embedder
from .memory_store import MemoryStore
from .memory_types import MemoryEntry, MemorySearchResult, MemoryType

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
        query_vec: Optional[List[float]] = None,
    ) -> List[Dict]:
        """根据对话上下文召回相关记忆，返回 messages 格式列表（画像在前）。"""
        profile_msgs, memory_msgs = await self.recall_split(
            conversation,
            top_k=top_k, entity_scope=entity_scope,
            related_scopes=related_scopes, query_vec=query_vec,
        )
        return profile_msgs + memory_msgs

    async def recall_split(
        self,
        conversation: List[Dict],
        *,
        top_k: Optional[int] = None,
        entity_scope: str = "",
        related_scopes: Optional[List[str]] = None,
        query_vec: Optional[List[float]] = None,
    ) -> Tuple[List[Dict], List[Dict]]:
        """召回相关记忆，画像与检索结果分开返回 (profile_msgs, memory_msgs)。

        画像与检索结果的变更频率不同（画像随实体沉淀低频变，检索结果随
        每条消息变），分开返回让上下文组装能把它们放到不同的分层位置，
        保证缓存前缀稳定。

        同时搜索 memories 表和 MD 文件 chunks（双轨统一召回）。
        related_scopes 用于群聊场景下加载活跃成员的画像。
        query_vec 为调用方预计算的查询向量（三条召回路径共享一次 embedding），
        为 None 时内部按需自行计算。
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
        # 实体画像加载与检索并行（独立的 DB 读取，不依赖查询结果）
        profiles_task = asyncio.create_task(self._load_entity_profiles(all_scopes))

        query = self._extract_query(conversation)
        if not query:
            log("💾 被动召回: 无有效查询，回退近期记忆", tag="思维")
            entity_msgs, fallback = await asyncio.gather(profiles_task, self._fallback_recent(k))
            return entity_msgs, fallback

        log(f"💾 被动召回: \"{query[:50]}\" (embedding={'是' if self._embedder.available else '否'})", tag="思维")

        async def _main_search() -> List[MemorySearchResult]:
            vec = query_vec
            if vec is None:
                vec = await self._embedder.embed_query(query)
            from .cognee.fusion import federated_search
            from .cognee.runtime import get_cognee_client
            return await federated_search(
                self._store.search_unified(
                    query=query,
                    query_vec=vec,
                    limit=k * self._cognee_config.recall_pool_multiplier,
                    min_score=min_score,
                ),
                query=query,
                client=get_cognee_client(),
                config=self._cognee_config,
                limit=k,
                entity_scope=entity_scope,
            )

        # 多窗口补充：以最近一条用户消息为焦点查询，与主查询并行检索后融合
        focus_query = self._extract_focus_query(conversation)

        async def _focus_search() -> List[MemorySearchResult]:
            focus_vec = await self._embedder.embed_query(focus_query)
            return await self._store.search_unified(
                query=focus_query,
                query_vec=focus_vec,
                limit=k,
                min_score=min_score,
            )

        if focus_query and focus_query != query:
            results, focus_results, entity_msgs = await asyncio.gather(
                _main_search(), _focus_search(), profiles_task,
            )
            results = self._merge_results(results, focus_results, limit=k * 2)
        else:
            results, entity_msgs = await asyncio.gather(_main_search(), profiles_task)

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
            return entity_msgs, fallback

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

        return entity_msgs, self._format_unified_results(results)

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
            log("_resolve_scope_alias 异常已忽略", "DEBUG")
        return scope

    async def _load_entity_profiles(self, scopes: List[str]) -> List[Dict]:
        """加载多个实体的画像记忆，按 scope 分组标注。

        多个 alias 指向同一 primary 时自动去重，仅加载一次；
        各 scope 的画像读取互相独立，并发执行避免串行 DB 往返。
        """
        if not scopes:
            return []
        primaries = await asyncio.gather(*(self._resolve_scope_alias(s) for s in scopes))
        resolved_map: dict[str, str] = {}
        for scope, primary in zip(scopes, primaries, strict=False):
            if primary not in resolved_map:
                resolved_map[primary] = scope

        async def _load_one(primary_scope: str) -> Tuple[str, str, List[MemoryEntry]]:
            """加载单个实体的画像条目，返回 (primary_scope, source, entries)。"""
            entity_id = primary_scope.split("_", 1)[1] if "_" in primary_scope else primary_scope
            if not entity_id:
                return primary_scope, "", []
            source = f"entity_{entity_id}"
            entries = await self._store.list_recent(
                limit=2, memory_type=MemoryType.ENTITY, source=source,
            )
            if not entries:
                tag = f"user:{entity_id}" if primary_scope.startswith("user_") else f"group:{entity_id}"
                entries = await self._store.search_by_tags([tag], limit=3)
            return primary_scope, source, entries

        loaded = await asyncio.gather(*(_load_one(p) for p in resolved_map))

        # 每实体一条独立消息、按 scope 排序：实体间字节互不干扰，
        # 参与人不变时整块画像字节稳定（缓存/快照 diff 友好）
        from core.sanitizer import sanitize_for_context
        messages: List[Dict] = []
        loaded_count = 0
        for primary_scope, source, entries in sorted(loaded, key=lambda item: item[0]):
            if not entries:
                continue
            entity_id = primary_scope.split("_", 1)[1] if "_" in primary_scope else primary_scope
            scope_label = f"[uid:{entity_id}]" if primary_scope.startswith("user_") else f"[group_id:{entity_id}]"
            body = "\n---\n".join(e.content for e in entries)
            messages.append({
                "role": "system",
                "content": sanitize_for_context(
                    f"[系统注入·人物画像] {scope_label} 的画像信息：\n{body}"
                ),
            })
            loaded_count += len(entries)
            log(f"实体画像加载: {source} ({len(entries)} 条)", "DEBUG", tag="思维")

        if not messages:
            return []
        log(f"实体画像注入: {len(scopes)} 个 scope, {loaded_count} 条画像", tag="思维")
        return messages

    async def load_relation_snippets(
        self,
        scopes: List[str],
        *,
        max_edges: int = 15,
        max_chars: int = 2000,
    ) -> List[Dict]:
        """加载一组实体 scope 的关系网络快照（结构化关系事实注入块）。

        scopes 为 entity_scope 格式（``user_qq:123``），内部转换为图谱节点 key
        （``user:qq:123``，别名归一由 GraphStore 处理）。无图谱数据时返回空。
        """
        if not scopes:
            return []
        node_keys: List[str] = []
        for scope in scopes:
            if "_" not in scope:
                continue
            prefix, value = scope.split("_", 1)
            if prefix in ("user", "group") and value:
                key = f"{prefix}:{value}"
                if key not in node_keys:
                    node_keys.append(key)
        if not node_keys:
            return []
        # 每轮直查（与画像加载同构：一次索引邻域查询）；node_keys 排序 +
        # 边按强度确定性排序，参与人与图谱不变时注入内容字节级稳定
        node_keys_sorted = sorted(node_keys)
        try:
            edges = await self._store.graph.edges_for_scopes(node_keys_sorted, limit=max_edges)
        except Exception as exc:
            log(f"关系网络快照加载失败: {exc}", "DEBUG", tag="思维")
            return []
        if not edges:
            return []

        from .graph import format_triple
        lines = [
            "[系统注入·关系网络] 以下为当前会话相关实体的已知关系"
            "（结构化事实，非用户消息；完整网络可用 graph_query 查询）："
        ]
        used = len(lines[0])
        for edge in edges:
            line = f"- {format_triple(edge)}"
            if edge["evidence"]:
                line += f"（{edge['evidence'][:80]}）"
            if used + len(line) > max_chars:
                lines.append(f"- …（共 {len(edges)} 条，已截断，graph_query 可查全量）")
                break
            lines.append(line)
            used += len(line)

        from core.sanitizer import sanitize_for_context
        log(f"关系网络注入: {len(scopes)} 个 scope, {len(lines) - 1} 条关系", tag="思维")
        return [{"role": "system", "content": sanitize_for_context("\n".join(lines))}]

    async def _fallback_recent(self, limit: int) -> List[Dict]:
        entries = await self._store.list_recent(limit=limit)
        if entries:
            log(f"💾 回退: 取最近 {len(entries)} 条记忆", tag="思维")
        if not entries:
            return []
        lines = [e.content for e in entries]
        from core.sanitizer import sanitize_for_context
        return [{"role": "system", "content": sanitize_for_context("[近期记忆]\n" + "\n---\n".join(lines))}]

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
    def _dedup_key(snippet: str) -> str:
        """跨来源内容去重键：去空白后的前缀（memories 表与 cognee 图谱可能注入同一条内容）。"""
        return re.sub(r"\s+", "", snippet)[:120]

    @classmethod
    def _format_unified_results(cls, results: list[MemorySearchResult]) -> List[Dict]:
        """将统一搜索结果格式化为注入消息，保留 score/type 元数据。

        跨来源内容去重：同一条记忆可能同时命中 memories 表与 cognee 图谱
        （id 命名空间不同无法按 id 去重），按归一化内容前缀去重，
        保留分数最高（最先出现）的一份，避免重复注入浪费 token。
        """
        if not results:
            return []

        mem_lines: list[str] = []
        file_lines: list[str] = []
        graph_lines: list[str] = []
        seen: set[str] = set()
        deduped = 0

        for r in results:
            snippet = r.snippet[:500]
            key = cls._dedup_key(snippet)
            if key and key in seen:
                deduped += 1
                continue
            if key:
                seen.add(key)
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
                tag_str = f" [{_cap_tags(r.tags)}]" if r.tags else ""
                mem_lines.append(
                    f"{marker} [{mtype}]{tag_str} score={r.score:.2f}: {snippet}"
                )

        if deduped:
            log(f"召回跨来源去重: 移除 {deduped} 条重复内容", "DEBUG", tag="思维")

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
        # LLM 出向边界：统一脱敏（记忆中可能存过密钥/URL凭证）+ 孤代理清理 + 中部截断
        from core.sanitizer import sanitize_for_context
        return [{"role": "system", "content": sanitize_for_context("\n\n".join(parts))}]


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
                log("_apply_rerank 异常已忽略", "DEBUG")

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


def _cap_tags(tags: List[str], limit: int = 6) -> str:
    """标签列表截断展示：超限显示前 limit 个 + 总数。

    召回注入中标签仅作来源提示，长标签串（如逐次累积的 topic 标签）
    对 AI 是纯噪声且浪费 token。
    """
    if len(tags) <= limit:
        return ",".join(tags)
    return f"{','.join(tags[:limit])} 等{len(tags)}个"


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()
