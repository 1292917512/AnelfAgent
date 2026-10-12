"""轮内异步召回：新话题先查本地记忆，深入档再补充图谱。

首轮深入召回复用已有规划启动图谱探测；后续新话题最多补查两次。
所有结果按消费会话去重，在整轮预算内追加，结束时取消检索并回收。

Model Experience:
- 模型看到什么：至多一条 `[系统注入·记忆召回·续]` provider 消息（相关
  关系 / 新增记忆 / 图谱上下文分节），仅当深探发现账本外增量时出现
- token 影响：每回复 ≤memory_probe_max_chars（默认 1600 字符）；无增量零注入
- 缓存影响：provider 层每轮新鲜段（与桌面环境时间天气同区），不触碰
  任何 prompt 前缀缓存层
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator, Dict, Iterable, List, Optional

from agent.judgment.gate import assess_optional_work, conversation_evidence
from agent.judgment.types import JsonValue
from core.async_helper import spawn
from core.config import get_config_bool, get_config_float, get_config_int, register_configs_safe
from core.context_provider import ContextProviderRegistry, ProviderMeta
from core.hooks import HookMode, current_hook_run, hook_execution
from core.log import log

from .memory_types import RetrievalPlan, normalized_content_key
from .probe_render import RecallBuffer, RecallDelta

if TYPE_CHECKING:
    from .memory_retriever import MemoryRetriever
    from .memory_store import MemoryStore
    from .memory_types import MemorySearchResult

_PROBE_TTL_SECONDS = 600.0
_RENDERED_TTL_SECONDS = 86400.0


@dataclass
class RecallLedger:
    """per-reply 召回账本：结果 id / 图谱边 id / 内容前缀三键防重复。

    一个回复周期内所有召回产物（被动注入 / AI 工具 / 异步深探）共用，
    保证"一条逻辑记忆面"内零重复；新回复经 begin_reply 重置。
    """

    result_ids: set[str] = field(default_factory=set)
    edge_ids: set[int] = field(default_factory=set)
    content_keys: set[str] = field(default_factory=set)

    def reset(self) -> None:
        self.result_ids.clear()
        self.edge_ids.clear()
        self.content_keys.clear()

    def record_results(self, results: Iterable["MemorySearchResult"]) -> None:
        """记录一批召回结果（id + 内容键双记）。"""
        for r in results:
            self.record_result(r.id, r.snippet)

    def record_result(self, result_id: str, snippet: str = "") -> None:
        if result_id:
            self.result_ids.add(result_id)
        key = normalized_content_key(snippet)
        if key:
            self.content_keys.add(key)

    def record_edges(self, edge_ids: Iterable[int]) -> None:
        for edge_id in edge_ids:
            if edge_id is not None:
                self.edge_ids.add(int(edge_id))

    def seen_result(self, result_id: str) -> bool:
        return result_id in self.result_ids

    def seen_edge(self, edge_id: int) -> bool:
        return edge_id in self.edge_ids

    def seen_content(self, snippet: str) -> bool:
        key = normalized_content_key(snippet)
        return bool(key) and key in self.content_keys


@dataclass
class _ProbeState:
    """单 scope 的深探状态：账本 + 异步任务 + 持久渲染缓存。

    rendered 持有最新一次深探渲染（异步完成前为空；完成后每轮在场且
    字节稳定，provider 每轮读取；新回复 begin_reply 重置——不跨回复
    持久，避免与每轮重建的基底层召回形成常驻重复）。
    """

    scope: str
    ledger: RecallLedger = field(default_factory=RecallLedger)
    task: Optional[asyncio.Task] = None
    rendered: str = ""
    created_at: float = field(default_factory=time.monotonic)
    queries: set[str] = field(default_factory=set)
    retriever: MemoryRetriever | None = None
    revision: int = 0
    managed: bool = False
    evidence: list[JsonValue] = field(default_factory=list)
    recalled: list[JsonValue] = field(default_factory=list)
    buffer: RecallBuffer = field(default_factory=lambda: RecallBuffer(
        get_config_int("memory_probe_max_chars", 1600),
    ))


class DeepProbeHub:
    """按 scope 管理深探状态：新回复替换旧任务，惰性 TTL 清扫（无定时器）。"""

    def __init__(self, ttl_seconds: float = _PROBE_TTL_SECONDS) -> None:
        self._states: Dict[str, _ProbeState] = {}
        self._ttl = ttl_seconds

    def begin_reply(self, scope: str, *, managed: bool = False) -> _ProbeState:
        """新回复开账：重置该 scope 的账本并取消上一回复的残留任务。"""
        self._sweep()
        state = _ProbeState(scope=scope, managed=managed)
        old = self._states.get(scope)
        if old is not None and old.task is not None and not old.task.done():
            old.task.cancel()
        self._states[scope] = state
        return state

    def state_for(self, scope: str) -> Optional[_ProbeState]:
        return self._states.get(scope)

    def stop(self, scope: str) -> Optional[asyncio.Task]:
        """取消当前轮次的补充检索并拒绝迟到结果，保留已经呈现的事实。"""
        state = self._states.get(scope)
        if state is None:
            return None
        state.revision += 1
        if state.task is not None and not state.task.done():
            state.task.cancel()
        return state.task

    async def end_reply(self, scope: str, *, expected: _ProbeState | None = None) -> None:
        """回收本轮后台检索，禁止旧结果流入后续对话。"""
        if expected is not None and self._states.get(scope) is not expected:
            return
        task = self.stop(scope)
        state = self._states.pop(scope, None)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if state is not None:
            state.rendered = ""

    def prepare(self, scope: str, conversation: List[Dict], retriever: MemoryRetriever) -> None:
        """绑定本轮检索依赖，首轮已处理的话题不再后台重复检索。"""
        state = self.state_for(scope)
        if state is not None:
            state.retriever = retriever
            state.queries.add(retriever._extract_focus_query(conversation))

    def refresh(self, scope: str, conversation: List[Dict]) -> None:
        """按当前策略和新话题启动有限次异步检索；无话题变化不重复调用。"""
        from .recall_policy import current_recall_policy
        policy = current_recall_policy(scope)
        state = self.state_for(scope)
        if state is None or policy.tags or not policy.background or policy.level not in {"fast", "deep"}:
            return
        retriever = state.retriever
        if retriever is None:
            return
        if not get_config_bool("memory_probe_enabled", True) or (state.task and not state.task.done()):
            return
        query = retriever._extract_focus_query(conversation)
        if not query or retriever._is_trivial_turn(conversation) or query in state.queries or len(state.queries) >= 3:
            return
        state.queries.add(query)
        revision = state.revision

        async def execute() -> None:
            async with self._observe(state):
                from .recall_fast import search_local
                await self._stage("本地记忆检索")
                results = await search_local(retriever, query)
                deltas = await self._memory_deltas(results, retriever._store, "补充记忆")
                if not self._publish(state, revision, deltas):
                    return
                if policy.level == "deep":
                    state.evidence = conversation_evidence(conversation)
                    state.recalled = [{"content": item.snippet[:400], "tags": list(item.tags)} for item in results[:8]]
                    if not await self._allow_deep(state, [query]) or not self._is_current(state, revision):
                        return
                    await self._stage("检索规划（最多 5 秒，超时回退原查询）")
                    plan = await retriever.plan_retrieval(query, timeout=5)
                    if self._is_current(state, revision) and (plan.deep_needed or plan.node_keys):
                        await self._probe(state, plan, retriever._store, revision=revision)
        state.task = spawn(execute(), name=f"memory.probe.{scope}")

    def ledger(self, scope: str) -> RecallLedger:
        """取（或建）该 scope 的账本——供基底层注入与工具返回时记账。"""
        state = self._states.get(scope)
        if state is None:
            state = _ProbeState(scope=scope)
            self._states[scope] = state
        return state.ledger

    def record(
        self,
        scope: str,
        *,
        results: Optional[Iterable["MemorySearchResult"]] = None,
        edge_ids: Optional[Iterable[int]] = None,
    ) -> None:
        """仅为实际消费结果的活跃会话记账，无归属或无回复时不创建状态。"""
        state = self.state_for(scope) if scope else None
        if state is None:
            return
        if results is not None:
            state.ledger.record_results(results)
        if edge_ids is not None:
            state.ledger.record_edges(edge_ids)

    def start(
        self,
        *,
        scope: str,
        plan: RetrievalPlan,
        store: "MemoryStore",
        conversation: List[Dict] | None = None,
        recalled: list[JsonValue] | None = None,
    ) -> None:
        """异步启动深探（按需触发：规划判定 deep_needed 或已解析出实体节点）。"""
        if not plan.deep_needed and not plan.node_keys:
            return
        if not get_config_bool("memory_probe_enabled", True):
            return
        state = self.state_for(scope)
        if state is None:
            state = _ProbeState(scope=scope)
            self._states[scope] = state
        if state.task is not None and not state.task.done():
            return  # 单飞：本回复已有深探在途
        state.evidence = conversation_evidence(conversation or [])
        state.recalled = list((recalled or [])[:8])
        state.task = spawn(
            self._run(state, plan, store, state.revision),
            name=f"memory.probe.{scope or 'global'}",
        )

    def current_render(self, scope: str) -> str:
        """provider 读取该 scope 的最新渲染（持久：无新产物时返回旧值，空为零注入）。"""
        state = self._states.get(scope)
        return state.rendered if state is not None else ""

    def snapshot(self) -> List[Dict[str, object]]:
        """观测快照：各 scope 的深探状态（在途任务/渲染规模/预览），诊断注入是否发生。"""
        now = time.monotonic()
        return [
            {
                "scope": state.scope,
                "running": state.task is not None and not state.task.done(),
                "rendered_chars": len(state.rendered),
                "rendered_preview": state.rendered[:160],
                "age_seconds": int(now - state.created_at),
            }
            for state in self._states.values()
        ]

    async def _run(self, state: _ProbeState, plan: RetrievalPlan, store: "MemoryStore", revision: int) -> None:
        async with self._observe(state):
            if plan.deep_needed and plan.queries and not await self._allow_deep(state, plan.queries, plan.node_keys):
                return
            if not self._is_current(state, revision):
                return
            await self._probe(state, plan, store, revision=revision)

    @staticmethod
    async def _allow_deep(state: _ProbeState, queries: list[str], nodes: list[str] | None = None) -> bool:
        decision = await assess_optional_work(
            "深度记忆召回",
            "是否仍需要深层记忆或关系检索？比较当前请求、已有召回和待查问题。需要历史细节、跨实体关联、"
            "已有事实不足或明确要求回忆时执行；只有已有证据足够或明确与记忆无关时才跳过。不得把不同身份的事实混用。",
            {"scope": state.scope, "conversation": state.evidence, "queries": list(queries),
             "already_recalled": list(state.recalled), "entity_nodes": list(nodes or [])},
        )
        return decision.run

    @asynccontextmanager
    async def _observe(self, state: _ProbeState) -> AsyncIterator[None]:
        """统一记录检索阶段与预算截止，超时保留已经发布的增量。"""
        self._metrics("probe.started")
        timeout = max(5.0, get_config_float("memory_probe_timeout_seconds", 60.0))
        before = len(state.rendered)
        async with hook_execution("memory.probe", "context_refresh", HookMode.ASYNC, scope=state.scope) as run:
            budget = asyncio.timeout(timeout)
            try:
                async with budget:
                    yield
            except Exception as exc:
                run.status = "error"
                if isinstance(exc, TimeoutError) and budget.expired():
                    run.detail = f"检索预算 {timeout:g} 秒已用尽；未完成阶段：{run.operation or '准备'}。已保留 {len(state.rendered)} 字符补充记忆，主回复继续。"
                    self._metrics("probe.timeout")
                else:
                    run.detail = f"{run.operation}: {type(exc).__name__}: {str(exc) or '检索未完成'}；已保留 {len(state.rendered)} 字符补充记忆。"
                log(f"深探失败 scope={state.scope}: {run.detail}", "DEBUG", tag="记忆")
            else:
                added = len(state.rendered) - before
                summary = f"新增上下文 {added} 字符" if added > 0 else "当前话题无需补充记忆"
                run.detail = f"{run.detail}；{summary}" if run.detail else summary
                self._metrics("probe.completed")

    @staticmethod
    async def _stage(operation: str) -> None:
        run = current_hook_run.get()
        if run is not None and run.name == "memory.probe":
            run.phase, run.operation, run.updated_at = "retrieval", operation, time.time()
            await run.publish()

    def _is_current(self, state: _ProbeState, revision: int) -> bool:
        return self._states.get(state.scope) is state and state.revision == revision

    def _publish(self, state: _ProbeState, revision: int, deltas: Iterable[RecallDelta]) -> bool:
        """仅接纳当前检索版本的结果，在同一无等待步骤内渲染并记账。"""
        if not self._is_current(state, revision):
            return False
        previous = state.rendered
        for delta in deltas:
            if (delta.edge_id is not None and state.ledger.seen_edge(delta.edge_id)) or (
                state.ledger.seen_result(delta.result_id) or state.ledger.seen_content(delta.snippet)
            ):
                continue
            if state.buffer.append(delta):
                if delta.edge_id is not None:
                    state.ledger.record_edges([delta.edge_id])
                state.ledger.record_result(delta.result_id, delta.snippet)
        state.rendered = state.buffer.render()
        if state.rendered and state.rendered != previous:
            self._metrics("probe.injected")
        return True

    async def _probe(self, state: _ProbeState, plan: RetrievalPlan, store: "MemoryStore",
                     *, revision: int | None = None) -> None:
        """先呈现本地图谱，再追加远端记忆；旧策略或旧回复的结果不得回写。"""
        revision = state.revision if revision is None else revision
        if not self._is_current(state, revision):
            return
        await self._stage("本地图谱关系检索")
        relations = await self._collect_relations(state, plan, store)
        if not self._publish(state, revision, relations):
            return
        await self._collect_cognee(state, plan, store)

    @staticmethod
    async def _memory_deltas(results: Iterable["MemorySearchResult"], store: "MemoryStore",
                             section: str) -> list[RecallDelta]:
        """沿用同步召回的实体标签、时间与隐私标记格式。"""
        from .recall_format import format_memory_line
        deltas: list[RecallDelta] = []
        for result in results:
            text = result.snippet.strip()
            if not text:
                continue
            graph_context = result.source == "cognee_graph"
            line = await format_memory_line(
                store.graph, snippet=text[:800 if graph_context else 300], tags=result.tags,
                provenance=result.provenance, timestamp=result.timestamp, sensitivity=result.sensitivity,
            )
            deltas.append(RecallDelta("图谱上下文" if graph_context else section, line, result.id, text))
        return deltas

    async def _collect_relations(
        self, state: _ProbeState, plan: RetrievalPlan, store: "MemoryStore",
    ) -> List[RecallDelta]:
        """原生图谱邻域：查询相关实体的一跳活跃边（毫秒级直查权威库，无 LLM）。"""
        if not plan.node_keys:
            return []
        try:
            edges = await store.graph.edges_for_scopes(plan.node_keys, limit=8)
        except Exception as exc:
            log(f"深探图谱邻域查询失败: {exc}", "DEBUG", tag="记忆")
            return []
        from .graph import format_triple
        deltas: List[RecallDelta] = []
        for edge in edges:
            if state.ledger.seen_edge(int(edge["id"])):
                continue
            line = format_triple(edge)
            if edge.get("evidence"):
                line += f"（{str(edge['evidence'])[:80]}）"
            deltas.append(RecallDelta("相关关系", line, edge_id=int(edge["id"])))
        return deltas

    async def _collect_cognee(
        self, state: _ProbeState, plan: RetrievalPlan, store: "MemoryStore",
    ) -> None:
        """自动补充只取事实与图谱上下文，按通道即时发布，不启动多轮生成扩展。"""
        if not plan.queries or not plan.deep_needed:
            return
        try:
            from .cognee.config import load_cognee_config
            from .cognee.fusion import CogneeSearchBatch, datasets_for_scope, search_cognee
            from .cognee.runtime import get_cognee_client
            config = load_cognee_config()
            client = get_cognee_client()
        except Exception as exc:
            log(f"深探后端不可用: {exc}", "DEBUG", tag="记忆")
            return
        if client is None or not config.enabled or not config.recall_enabled:
            return
        query = plan.queries[0]
        # node_keys（user:适配器:uid 形态）与记忆实体标签同构：除当前会话
        # scope 外，把查询提及实体的数据集也纳入路由（与被动召回同一语义）
        datasets = datasets_for_scope(config, state.scope, plan.node_keys)
        revision = state.revision
        pending: set[str] = set()
        failures: list[str] = []

        async def consume(batch: CogneeSearchBatch) -> None:
            if batch.status == "running":
                pending.add(batch.lane)
            else:
                pending.discard(batch.lane)
                if batch.error:
                    failures.append(f"{batch.lane}: {batch.error}")
                if self._is_current(state, revision) and batch.results:
                    deltas = await self._memory_deltas(batch.results, store, "深层记忆")
                    self._publish(state, revision, deltas)
            waiting = ", ".join(sorted(pending))
            await self._stage(f"Cognee 检索：{len(pending)} 通道待完成；{waiting}" if pending else "Cognee 检索完成")

        await self._stage("Cognee 初始化与检索")
        await search_cognee(
            client, config, query, datasets, 5,
            ["CHUNKS", "GRAPH_COMPLETION"],
            node_names=plan.node_labels or None, on_batch=consume,
        )
        if failures:
            raise RuntimeError(f"{len(failures)} 个检索通道失败；{failures[0]}")

    def _sweep(self) -> None:
        """回收未托管的过期状态；由 recall_turn 托管的回复只在轮次结束时清理。"""
        now = time.monotonic()
        stale = [
            scope for scope, state in self._states.items()
            if not state.managed and now - state.created_at > self._ttl
            and (not state.rendered or now - state.created_at > _RENDERED_TTL_SECONDS)
        ]
        for scope in stale:
            state = self._states.pop(scope)
            if state.task is not None and not state.task.done():
                state.task.cancel()

    @staticmethod
    def _metrics(key: str) -> None:
        try:
            from . import metrics
            metrics.incr(key)
        except Exception:
            pass


deep_probe_hub = DeepProbeHub()


def _provide_probe(scope: str) -> str:
    """函数式 provide：读深探持久渲染缓存（零 I/O，满足 provider 契约）。

    异步生成完成前返回空（管线跳过空内容）；完成后返回最新渲染——
    provider 每轮读取同一持久值，无新产物时拿旧值（字节稳定，不消失）。
    """
    return deep_probe_hub.current_render(scope)


# 深探增量经 provider 层注入（priority 34 会话操作态势档；变动率语义：
# 回复内按检索通道到达追加事实，无新增时保持原字节）。provider 消息
# 不进压缩历史（每轮重新收集、逐字存活），不触碰任何前缀缓存层。
ContextProviderRegistry.register(ProviderMeta(
    name="memory_deep_probe",
    priority=34,
    max_tokens=600,
    group="memory",
    inject_key="memory_probe_inject",
    provide_fn=_provide_probe,
    description="异步深探增量（记忆召回·续）：回复期间并行完成的深度检索缓存",
))


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_PROBE_CONFIGS = {
    "memory/probe": {
        "memory_probe_inject": {
            "description": "深探增量的上下文注入开关（关闭后深探仍执行与记账，仅不再注入）",
            "default": True,
            "advanced": True,
        },
        "memory_probe_enabled": {
            "description": "异步深探：回复期间并行执行深度记忆/图谱检索，完成后经轮内增量注入（由检索规划判定按需触发）",
            "default": True,
        },
        "memory_probe_max_chars": {
            "description": "深探增量注入的字符预算（超出部分截断，引导主动 recall）",
            "default": 1600,
            "advanced": True,
            "unit": "字符",
        },
        "memory_probe_timeout_seconds": {
            "description": "动态记忆补充的整体检索预算；截止后保留已返回结果，不影响主回复",
            "default": 60.0,
            "advanced": True,
            "unit": "秒",
        },
    },
}

register_configs_safe(_PROBE_CONFIGS)
