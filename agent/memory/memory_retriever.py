"""MemoryRetriever：被动记忆召回，从对话上下文中自动检索相关记忆注入上下文。

检索由 LLM 规划驱动（plan_retrieval：多查询/实体定位/是否深探），多计划
查询经 cognee 联邦检索后共识融合（merge_consensus：多路命中加成）；与
tools.recall 主动召回保持一致的搜索范围。

Model Experience（召回归属标注）:
- 模型看到什么：每条召回记忆行首的归属标注（称呼[uid:xxx] / [group_id:xxx]，
  与会话消息 [uid:] 标签同构）+ 记忆召回块头的归属确认提示（先确认记忆
  涉及谁再下定论，uid 不符不张冠李戴）
- token 影响：每条记忆 +10~20 字符，块头 +约 80 字符（仅召回非空时注入）
- 缓存影响：召回块在尾部动态区（vol>30），不触碰前缀缓存层
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from core.log import log

from .embedding import Embedder
from .memory_store import MemoryStore
from .memory_types import (
    MemoryEntry,
    MemorySearchResult,
    MemoryType,
    RetrievalPlan,
    normalized_content_key,
)
from .probe import deep_probe_hub
from .recall_format import format_memory_line
from .store.tag_intel import ASSOC_PREFIXES

if TYPE_CHECKING:
    from .cognee.config import CogneeConfig

DEFAULT_TOP_K = 5
DEFAULT_MIN_SCORE = 0.1

_PLAN_PROMPT = (
    "分析以下对话上下文，为记忆检索制定计划，只输出 JSON（不要解释）：\n"
    '{"queries": ["适合记忆检索的查询1", "查询2"], "entities": ["对话中提到的实体名"], '
    '"deep_needed": false, "rationale": "一句依据"}\n'
    "规则：\n"
    "- queries：1-3 条互补的关键词组合/短句（覆盖不同检索角度，不重复，首条为主查询）\n"
    "- entities：对话中明确提到的人名/称呼/项目名/话题（最多 4 个，没有就空数组）\n"
    "- deep_needed：仅当涉及\"谁和谁是什么关系/历史渊源\"、多实体交叉、或明确回忆过去事件时为 true\n"
    "- rationale：一句话说明判断依据\n\n"
    "对话上下文：\n"
)


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

    @staticmethod
    def _cognee_config() -> "CogneeConfig":
        """Cognee 联邦召回配置（每次召回热读，Web 保存/手改 cognee.json 即时生效）。"""
        from .cognee.config import load_cognee_config
        return load_cognee_config()

    def set_rerank_client(self, client: object) -> None:
        """Set reranker (MediaClient instance) for post-search reranking."""
        self._rerank_client = client

    @property
    def _graph(self):
        """图谱查询面（store 未注入时为 None，格式化层空安全）。"""
        return self._store.graph if self._store is not None else None

    async def recall(
        self,
        conversation: List[Dict],
        *,
        top_k: Optional[int] = None,
        entity_scope: str = "",
        related_scopes: Optional[List[str]] = None,
        query_vec: Optional[List[float]] = None,
        fire_probe: bool = False,
    ) -> List[Dict]:
        """根据对话上下文召回相关记忆，返回 messages 格式列表（画像在前）。"""
        profile_msgs, memory_msgs = await self.recall_split(
            conversation,
            top_k=top_k, entity_scope=entity_scope,
            related_scopes=related_scopes, query_vec=query_vec,
            fire_probe=fire_probe,
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
        fire_probe: bool = False,
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
        # 回复路径开账：重置召回账本（防重复三键集合），后续基底层注入/
        # 工具返回/异步深探共用——一次回复内同一事实只出现一次
        if fire_probe:
            deep_probe_hub.begin_reply(entity_scope)
        # 实体画像加载与检索并行（独立的 DB 读取，不依赖查询结果）
        profiles_task = asyncio.create_task(self._load_entity_profiles(all_scopes))

        query = self._extract_query(conversation)
        if not query:
            log("💾 被动召回: 无有效查询，回退近期记忆", tag="思维")
            pinned = await self._load_permanent_pins([])
            entity_msgs, fallback = await asyncio.gather(profiles_task, self._fallback_recent(k))
            pinned_msgs = await self._format_unified_results(pinned) if pinned else []
            return entity_msgs, pinned_msgs + fallback

        # 平凡消息节流：纯客套/短回复轮跳过检索与改写（省一次改写 LLM 调用
        # + embedding + 多路检索），画像与永久记忆照常注入
        if self._is_trivial_turn(conversation):
            log("💾 被动召回: 平凡消息轮，跳过检索", "DEBUG", tag="思维")
            pinned = await self._load_permanent_pins([])
            entity_msgs = await profiles_task
            pinned_msgs = await self._format_unified_results(pinned) if pinned else []
            return entity_msgs, pinned_msgs

        log(f"💾 被动召回: \"{query[:50]}\" (embedding={'是' if self._embedder.available else '否'})", tag="思维")

        # 检索规划 + 查询提及识别：并行执行（互不依赖，都在检索关键路径上）
        # 规划：轻量 LLM 把对话尾部转成结构化计划（多查询/实体/是否深探，
        # 含实体→图谱节点解析），失败回退原查询单发——召回由 LLM 决策
        # 而非被动关键词匹配
        # 提及识别：对话中提到的已知实体/话题 → 标签（主评分与联想种子）
        async def _mentions() -> List[str]:
            try:
                return await self._store.extract_query_mentions(query)
            except Exception as exc:
                log(f"查询提及识别失败: {exc}", "DEBUG", tag="思维")
                return []

        plan, mention_tags = await asyncio.gather(
            self.plan_retrieval(query), _mentions(),
        )
        search_query = plan.queries[0] if plan.queries else query

        # 计划查询（互补多查询，首条带实体定向）；规划失败时退化为原查询
        planned_queries: List[str] = [q for q in plan.queries[:3] if q and len(q.strip()) >= 4]
        if not planned_queries:
            planned_queries = [search_query] if search_query else []

        async def _query_search(q: str, *, targeted: bool = False) -> List[MemorySearchResult]:
            vec = query_vec if (query_vec is not None and q == query) else await self._embedder.embed_query(q)
            from .cognee.fusion import federated_search
            from .cognee.runtime import get_cognee_client
            cognee_config = self._cognee_config()
            return await federated_search(
                self._store.search_unified(
                    query=q,
                    query_vec=vec,
                    query_tags=mention_tags or None,
                    limit=k * cognee_config.recall_pool_multiplier,
                    min_score=min_score,
                ),
                query=q,
                client=get_cognee_client(),
                config=cognee_config,
                limit=k,
                entity_scope=entity_scope,
                query_tags=mention_tags or None,
                node_names=plan.node_labels if targeted else None,
            )

        # 多窗口补充：以最近一条用户消息为焦点查询（规划退化为单查询时
        # 焦点窗口仍是有效的第二检索角度）；焦点不参与共识计数
        focus_query = self._extract_focus_query(conversation)

        async def _focus_search() -> List[MemorySearchResult]:
            focus_vec = await self._embedder.embed_query(focus_query)
            return await self._store.search_unified(
                query=focus_query,
                query_vec=focus_vec,
                limit=k,
                min_score=min_score,
            )

        async def _searches() -> List[MemorySearchResult]:
            if not planned_queries:
                return []
            tasks = [_query_search(q, targeted=(i == 0)) for i, q in enumerate(planned_queries)]
            n_plan_lanes = len(tasks)
            if focus_query and focus_query not in planned_queries:
                tasks.append(_focus_search())
            lanes = list(await asyncio.gather(*tasks))
            return self.merge_consensus(lanes, limit=k * 2, consensus_lanes=n_plan_lanes)

        # 召回总时限：检索路径整体超时后直接走回退，不阻塞对话主流程
        recall_timeout = 5.0
        try:
            from core.config import get_config_float
            recall_timeout = max(1.0, get_config_float("memory_recall_timeout_seconds", 5.0))
        except Exception:
            pass
        try:
            results = await asyncio.wait_for(_searches(), timeout=recall_timeout)
        except asyncio.TimeoutError:
            log(f"💾 被动召回超时（{recall_timeout}s），回退近期记忆", "WARNING", tag="思维")
            try:
                from . import metrics
                metrics.incr("recall.timeout")
            except Exception:
                pass
            results = []
        entity_msgs = await profiles_task

        # 时间感知：检测到时间引用词时，提升事件记忆与近期记忆权重
        if self._detect_time_reference(query):
            results = self._apply_temporal_boost(results)

        # 上下文加权：与当前对话实体（用户/群）标签匹配的记忆加权
        results = self._apply_scope_boost(results, all_scopes)

        # 关联扩展：沿标签网络发现一跳关联记忆（想到一件事 → 联想到相关的事）
        results = await self._expand_associations(results, limit=k, extra_seeds=mention_tags)

        # 回复路径收尾钩子：基底层产物入召回账本 + 按需启动异步深探
        # （LLM 思考期间完成的深度检索经 think_loop 轮顶增量注入，与基底层去重）
        def _fire_probe(base_results: List[MemorySearchResult]) -> None:
            if not fire_probe:
                return
            try:
                deep_probe_hub.record(entity_scope, results=base_results)
                deep_probe_hub.start(scope=entity_scope, plan=plan, store=self._store)
            except Exception as exc:
                log(f"深探启动失败: {exc}", "DEBUG", tag="记忆")

        if not results:
            log("💾 统一搜索无结果，回退近期记忆", tag="思维")
            # 首轮无结果正是深探最有价值的场景（表层检索挖不到 → 深层图检索补充），
            # 回退前先按计划启动探针（账本只含本分支的空基线）
            _fire_probe(results)
            pinned = await self._load_permanent_pins([])
            fallback = await self._fallback_recent(k)
            pinned_msgs = await self._format_unified_results(pinned) if pinned else []
            return entity_msgs, pinned_msgs + fallback

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

        # 永久记忆置顶：主人教导/规则类记忆不参与打分竞争，每轮固定注入
        pinned = await self._load_permanent_pins(results)
        if pinned:
            results = pinned + results

        _fire_probe(results)

        for r in results:
            src_label = (
                f"[{r.source}]"
                if r.source != "memory"
                else f"[{r.memory_type or 'memory'}]"
            )
            tag_str = f" [{','.join(r.tags)}]" if r.tags else ""
            path_str = f" {r.path}" if r.path else ""
            log(f"  💡 {src_label}{tag_str}{path_str} score={r.score:.2f}: {r.snippet[:50]}", tag="思维")

        return entity_msgs, await self._format_unified_results(results)

    @staticmethod
    async def _resolve_scope_alias(scope: str) -> str:
        """将 scope 解析到 primary（若存在 alias）。"""
        try:
            from agent.runtime.singleton import get_runtime
            rt = get_runtime()
            if rt is None:
                return scope
            sqlite = rt.data_center.sqlite
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
        # 关系快照入召回账本：异步深探按边 id 去重，同一关系不重复注入
        try:
            deep_probe_hub.record(
                scopes[0] if scopes else "", edge_ids=(int(e["id"]) for e in edges),
            )
        except Exception:
            pass

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
        # 与召回行同一归属标注格式：回退路径同样不能丢失"这是谁的记忆"
        lines: List[str] = []
        for e in entries:
            tags = await self._humanize_entity_tags(e.tags) if e.tags else []
            head = f"{'·'.join(tags)} " if tags else ""
            lines.append(f"{head}{e.content}")
        from core.sanitizer import sanitize_for_context
        return [{"role": "system", "content": sanitize_for_context(
            "[近期记忆]（归属标注：称呼[uid:xxx] 指明记忆涉及谁）\n"
            + "\n---\n".join(lines)
        )}]

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

    async def plan_retrieval(self, query: str) -> RetrievalPlan:
        """检索规划：轻量 LLM 把对话尾部转成结构化检索计划（失败回退单查询）。

        取代旧版纯改写：多查询互补扩大召回面（共识命中加成），实体名驱动
        图谱解析与 cognee 定向检索，deep_needed 决定是否异步深探——
        召回由 LLM 决策而非被动关键词匹配。解析全路径容错；返回的
        plan 已完成实体→图谱节点解析（node_keys/node_labels 就绪）。
        """
        fallback = RetrievalPlan(queries=[query] if query else [])
        try:
            from core.config import get_config_bool
            if not get_config_bool("memory_query_rewrite_enabled", True):
                return fallback
        except Exception:
            return fallback
        if len(query) < 20:
            return fallback
        plan = fallback
        try:
            from .dedup import light_llm
            raw = await asyncio.wait_for(
                light_llm(_PLAN_PROMPT + query, temperature=0.1),
                timeout=8.0,
            )
            parsed = self._parse_plan_json(raw)
            if parsed is not None:
                queries = [
                    str(q).strip() for q in parsed.get("queries", [])
                    if isinstance(q, str) and 4 <= len(q.strip()) <= 200
                ][:3]
                entities = [
                    str(e).strip() for e in parsed.get("entities", [])
                    if isinstance(e, str) and e.strip()
                ][:4]
                plan = RetrievalPlan(
                    queries=queries or ([query] if query else []),
                    entities=entities,
                    deep_needed=bool(parsed.get("deep_needed")),
                    rationale=str(parsed.get("rationale", ""))[:200],
                )
                try:
                    from . import metrics
                    metrics.incr("recall.planned")
                except Exception:
                    pass
                log(
                    f"💾 检索规划: {len(plan.queries)} 查询, 实体 {plan.entities or '无'}, "
                    f"深探={'是' if plan.deep_needed else '否'}",
                    "DEBUG", tag="思维",
                )
        except Exception as exc:
            log(f"检索规划失败，回退原查询: {exc}", "DEBUG", tag="思维")
        if plan.entities:
            try:
                plan.node_keys, plan.node_labels = await self._resolve_plan_entities(plan.entities)
            except Exception as exc:
                log(f"规划实体图谱解析失败: {exc}", "DEBUG", tag="思维")
        return plan

    @staticmethod
    def _parse_plan_json(raw: str) -> Optional[Dict[str, Any]]:
        """宽松解析规划 LLM 输出：截取首个 JSON 对象，格式不符返回 None。"""
        import json as _json
        text = (raw or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = _json.loads(text[start:end + 1])
        except _json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    async def _resolve_plan_entities(self, names: List[str]) -> Tuple[List[str], List[str]]:
        """规划实体名 → 图谱节点（node_keys 邻域查询 + node_labels 定向检索）。"""
        nodes = await self._store.graph.resolve_nodes_for_tags(names, limit=4)
        keys: List[str] = []
        labels: List[str] = []
        for node in nodes:
            key = str(node.get("node_key", ""))
            if key and key not in keys:
                keys.append(key)
            label = str(node.get("label", "")).strip()
            if label and label not in labels:
                labels.append(label)
        return keys, labels

    @staticmethod
    def merge_consensus(
        lanes: List[List[MemorySearchResult]],
        *,
        limit: int,
        consensus_lanes: Optional[int] = None,
        consensus_boost: float = 1.1,
    ) -> List[MemorySearchResult]:
        """多路召回统一融合：同键取最高分，多条计划查询命中的结果加成。

        共识是确定性可靠性信号——独立检索角度都命中的事实更可能是
        正确记忆（内容可靠），比单路命中更值得注入。consensus_lanes
        指定前 N 条 lane 参与共识计数（计划查询），其余 lane（焦点窗口
        等补充角度）只参与合并竞争不计共识。
        """
        from .cognee.fusion import dedupe_key
        n_consensus = len(lanes) if consensus_lanes is None else max(0, consensus_lanes)
        best: Dict[str, MemorySearchResult] = {}
        hits: Dict[str, int] = {}
        for lane_index, lane in enumerate(lanes):
            counts_consensus = lane_index < n_consensus
            for r in lane:
                key = dedupe_key(r)
                if counts_consensus:
                    hits[key] = hits.get(key, 0) + 1
                current = best.get(key)
                if current is None or r.score > current.score:
                    best[key] = r
        results: List[MemorySearchResult] = []
        for key, r in best.items():
            if hits.get(key, 0) >= 2:
                r.score = round(min(1.0, r.score * consensus_boost), 4)
            results.append(r)
        results.sort(key=lambda r: r.score, reverse=True)
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
            extra_seeds: Optional[list[str]] = None,
    ) -> list[MemorySearchResult]:
        """关联扩展：沿标签网络发现关联记忆，追加到结果尾部。

        人脑联想机制：想到一件事时，与之相关的人/事也会被唤起。
        种子标签三层扩展：直接命中标签 → 图谱邻居（实体间已知关系）→
        标签共现（常一起出现的话题/实体）。关联记忆分数打折（0.75），
        并标记 associated=True 供呈现层区分。
        """
        if not results and not extra_seeds:
            return results

        # 收集关联边：主结果中的实体/主题标签 + 查询提及识别的种子
        assoc_tags: list[str] = []
        for r in results[:limit]:
            for tag in r.tags:
                if tag.startswith(ASSOC_PREFIXES) and tag not in assoc_tags:
                    assoc_tags.append(tag)
        for tag in extra_seeds or []:
            if tag not in assoc_tags:
                assoc_tags.append(tag)
        if not assoc_tags:
            return results

        # 种子三层扩展（图谱邻居 + 标签共现），统一由门面实现
        assoc_tags = await self._store.expand_tag_seeds(assoc_tags)

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
                timestamp=entry.timestamp,
                sensitivity=str(entry.metadata.get("sensitivity", "normal")),
                provenance={"associated": True},
            ))
        return results

    @classmethod
    def _is_trivial_turn(cls, conversation: List[Dict]) -> bool:
        """平凡消息轮判定：最近一条用户消息为纯客套/短回复时跳过检索。

        保守阈值：仅当消息 ≤6 个字符、不含时间引用词、不含问号时才跳过——
        "我钥匙放哪了"这类短但记忆相关的查询不受影响。
        """
        try:
            from core.config import get_config_bool
            if not get_config_bool("memory_recall_skip_trivial", True):
                return False
        except Exception:
            return False
        for msg in reversed(conversation):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                return False
            cleaned = _strip_tags(content).strip()
            if not cleaned:
                return False
            if len(cleaned) > 6:
                return False
            if "?" in cleaned or "？" in cleaned:
                return False
            if cls._detect_time_reference(cleaned):
                return False
            return True
        return False

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
            if r.timestamp and (now - r.timestamp) / 3600.0 < recent_hours:
                r.score *= recent_boost
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    async def _load_permanent_pins(
        self, results: list[MemorySearchResult],
    ) -> list[MemorySearchResult]:
        """加载永久记忆置顶位（与召回结果按 id 去重，标记 pinned 供分区呈现）。"""
        try:
            from core.config import get_config_int
            pin_limit = get_config_int("memory_recall_permanent_pin", 3)
        except Exception:
            pin_limit = 3
        if pin_limit <= 0:
            return []
        existing = {r.id for r in results}
        try:
            entries = await self._store.list_recent(
                limit=pin_limit * 2, memory_type=MemoryType.PERMANENT,
            )
        except Exception as exc:
            log(f"永久记忆置顶加载失败: {exc}", "DEBUG", tag="思维")
            return []
        out: list[MemorySearchResult] = []
        # list_recent 窗口内按时间升序返回，反转为最新优先
        from agent.memory.hub import HUB_TAG
        for e in reversed(entries):
            if HUB_TAG in e.tags:
                continue  # 主标签记忆有专属注入块（hub 层），不占 pin 名额
            rid = f"mem:{e.id}"
            if rid in existing:
                continue
            out.append(MemorySearchResult(
                id=rid, snippet=e.content[:500], score=1.0, source="memory",
                memory_type=e.memory_type.value, tags=e.tags,
                timestamp=e.timestamp,
                sensitivity=str(e.metadata.get("sensitivity", "normal")),
                provenance={"pinned": True},
            ))
            if len(out) >= pin_limit:
                break
        return out

    @staticmethod
    def _dedup_key(snippet: str) -> str:
        """跨来源内容去重键：去空白后的前缀（memories 表与 cognee 图谱可能注入同一条内容）。"""
        return normalized_content_key(snippet)

    async def _humanize_entity_tags(self, tags: List[str]) -> List[str]:
        """归属标注（委托 recall_format 权威实现）。"""
        from .recall_format import humanize_entity_tags
        return await humanize_entity_tags(self._store.graph, tags)

    @staticmethod
    def _format_memory_time(ts: float) -> str:
        """记忆时间格式（委托 recall_format 权威实现）。"""
        from .recall_format import format_memory_time
        return format_memory_time(ts)

    async def _format_unified_results(self, results: list[MemorySearchResult]) -> List[Dict]:
        """将统一搜索结果格式化为注入消息。

        注入纪律：只保留对 AI 有信息量的内容——归因（谁的/什么主题）、正文、
        记录时间；score/内部类型/来源数据集等调试信息只进 log 不进上下文。
        跨来源内容去重：同一条记忆可能同时命中 memories 表与 cognee 图谱
        （id 命名空间不同无法按 id 去重），按归一化内容前缀去重。
        """
        if not results:
            return []

        pinned_lines: list[str] = []
        mem_lines: list[str] = []
        file_lines: list[str] = []
        seen: set[str] = set()
        deduped = 0

        for r in results:
            snippet = r.snippet[:500]
            key = self._dedup_key(snippet)
            if key and key in seen:
                deduped += 1
                continue
            if key:
                seen.add(key)
            pinned = bool(r.provenance.get("pinned")) if r.provenance else False
            associated = bool(r.provenance.get("associated")) if r.provenance else False
            marker = "📌" if pinned else ("🔗" if associated else "💡")
            if r.source == "file":
                loc = f"[{r.path}:{r.start_line}-{r.end_line}]" if r.path else ""
                file_lines.append(f"{marker} {loc} {snippet}".replace("  ", " "))
                continue
            # memory / cognee_* 统一按记忆行呈现（cognee 只是投影层，对 AI 无区别）；
            # 行格式为共享权威（recall_format），与异步深探的增量行同构
            line = await format_memory_line(
                self._graph, snippet=snippet, tags=r.tags,
                provenance=r.provenance, timestamp=r.timestamp,
                sensitivity=r.sensitivity, marker=marker,
            )
            if pinned:
                pinned_lines.append(line)
            else:
                mem_lines.append(line)

        if deduped:
            log(f"召回跨来源去重: 移除 {deduped} 条重复内容", "DEBUG", tag="思维")

        # LLM 出向边界：统一脱敏（记忆中可能存过密钥/URL凭证）+ 孤代理清理 + 中部截断
        from core.sanitizer import sanitize_for_context
        # 永久记忆与召回/检索分消息返回：永久块字节稳定（仅增删时变化），
        # 会被 recollection 提升进 context 层走内容寻址缓存；召回/检索逐条
        # 消息变化，留在尾部动态区——合并成一条会把每轮变化的召回内容带进
        # context 层，击穿其后摘要与对话历史的缓存前缀（实测每轮新回复
        # 命中恒被截断在同一位置）
        messages: List[Dict] = []
        if pinned_lines:
            messages.append({
                "role": "system",
                "content": sanitize_for_context(
                    "[系统注入·永久记忆] 以下为永久记忆（主人教导/既定规则），始终生效：\n"
                    + "\n".join(pinned_lines)
                ),
            })
        dynamic_lines: list[str] = []
        if mem_lines:
            dynamic_lines.append(
                "[系统注入·记忆召回] 以下为自动检索到的相关记忆，不代表当前任务进程，仅作参考"
                "（💡=直接相关，🔗=联想关联；每条开头的归属标注指明记忆涉及谁，"
                "uid 与当前对话对象不符勿张冠李戴，「私事」不外传——纪律见记忆体系铁律）：\n"
                + "\n".join(mem_lines)
            )
        if file_lines:
            dynamic_lines.append(
                "[系统注入·知识检索] 以下为便签文件检索结果：\n"
                + "\n".join(file_lines)
            )
        if dynamic_lines:
            messages.append({
                "role": "system",
                "content": sanitize_for_context("\n\n".join(dynamic_lines)),
            })
        return messages


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


def _strip_tags(text: str) -> str:
    """剥离检索查询中的消息元数据标签（与出站清洗共用 core.tags 的 10 键清单）。"""
    from core.tags import strip_message_meta_tags
    return strip_message_meta_tags(text).strip()
