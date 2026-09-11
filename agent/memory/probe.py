"""异步深探（Deep Probe）：回复期间与 LLM 思考并行的深度记忆/图谱检索。

检索规划（``RetrievalPlan``，retriever 产出）判定需要深度检索或已解析出
查询相关实体时，被动召回完成后异步启动，分两阶段刷新持久渲染缓存
（类脑唤醒机制：快唤醒 → 慢巩固）——

- 快段：原生图谱邻域（毫秒级直查权威库，无 LLM），先行渲染注入；
- 慢段：cognee 图谱综合与定向实体检索（经 fusion 统一拼装，LLM 参与），
  完成后并入更新渲染。

产物经召回账本去重、经 recall_format 行格式化后写入持久渲染缓存，经
上下文提供者（memory_deep_probe）每轮读取注入——LLM 思考期间即可看到
首轮召回之外的补充记忆；缓存持有最新渲染（异步完成前为空，完成后每轮
在场且字节稳定，新回复开账重置）。

召回账本（RecallLedger）：per-reply 防重复，被动召回注入、AI recall
工具返回、异步深探产出三条通道共用——同一事实在一次回复中只出现一次。

Model Experience:
- 模型看到什么：至多一条 `[系统注入·记忆召回·续]` provider 消息（相关
  关系 / 新增记忆 / 图谱综合分节），仅当深探发现账本外增量时出现
- token 影响：每回复 ≤memory_probe_max_chars（默认 1600 字符）；无增量零注入
- 缓存影响：provider 层每轮新鲜段（与桌面环境时间天气同区），不触碰
  任何 prompt 前缀缓存层
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Tuple

from core.async_helper import spawn
from core.config import get_config_bool, get_config_float, get_config_int, register_configs_safe
from core.context_provider import ContextProviderRegistry, ProviderMeta
from core.log import log

from .memory_types import RetrievalPlan, normalized_content_key

if TYPE_CHECKING:
    from .memory_store import MemoryStore
    from .memory_types import MemorySearchResult

_PROBE_TTL_SECONDS = 600.0
_RENDERED_TTL_SECONDS = 86400.0

_DELTA_HEADER = (
    "[系统注入·记忆召回·续] 回复期间异步补充的深度检索"
    "（与此前注入的记忆已去重，仅作参考，不代表当前任务进程）："
)


def _section(title: str, note: str, lines: List[str]) -> str:
    """深探增量的分节渲染：``▸ 标题 · 说明`` + 缩进行条目。"""
    body = "\n".join(f"  - {line}" for line in lines)
    return f"▸ {title} · {note}\n{body}"


def _render_delta(sections: List[str], max_chars: int) -> str:
    """组装增量消息（头部 + 分节，预算超限时截断并引导主动检索）；空分节返回空。"""
    if not sections:
        return ""
    used = len(_DELTA_HEADER)
    kept: List[str] = []
    for section in sections:
        if used + len(section) > max_chars:
            kept.append("（其余增量因预算截断，可经 recall(depth=deep) 主动获取）")
            break
        kept.append(section)
        used += len(section)
    return _DELTA_HEADER + "\n" + "\n".join(kept)


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


class DeepProbeHub:
    """按 scope 管理深探状态：新回复替换旧任务，惰性 TTL 清扫（无定时器）。"""

    def __init__(self, ttl_seconds: float = _PROBE_TTL_SECONDS) -> None:
        self._states: Dict[str, _ProbeState] = {}
        self._ttl = ttl_seconds

    def begin_reply(self, scope: str) -> _ProbeState:
        """新回复开账：重置该 scope 的账本并取消上一回复的残留任务。"""
        self._sweep()
        state = _ProbeState(scope=scope)
        old = self._states.get(scope)
        if old is not None and old.task is not None and not old.task.done():
            old.task.cancel()
        self._states[scope] = state
        return state

    def state_for(self, scope: str) -> Optional[_ProbeState]:
        return self._states.get(scope)

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
        """召回记账唯一入口：三条通道（基底层注入 / AI 工具 / 关系快照）共用。

        scope 已知时定向记入该账本；未知（工具未携带实体标签等）广播到
        全部活跃账本——定向记账会与进行中回复的账本隔离，广播保证深探
        仍能感知 AI 已取回的内容。结果与边可同批记入。
        """
        # 一次性物化：广播到多个账本时生成器只能被消费一次，
        # 调用方（工具层）传的是生成器表达式
        result_list = list(results) if results is not None else None
        edge_list = list(edge_ids) if edge_ids is not None else None
        ledgers = [self.ledger(scope)] if scope else [
            state.ledger for state in self._states.values()
        ]
        for ledger in ledgers:
            if result_list is not None:
                ledger.record_results(result_list)
            if edge_list is not None:
                ledger.record_edges(edge_list)

    def start(
        self,
        *,
        scope: str,
        plan: RetrievalPlan,
        store: "MemoryStore",
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
        self._metrics("probe.started")
        state.task = spawn(
            self._run(state, plan, store),
            name=f"memory.probe.{scope or 'global'}",
        )

    def current_render(self, scope: str) -> str:
        """provider 读取该 scope 的最新渲染（持久：无新产物时返回旧值，空为零注入）。"""
        state = self._states.get(scope)
        return state.rendered if state is not None else ""

    async def _run(self, state: _ProbeState, plan: RetrievalPlan, store: "MemoryStore") -> None:
        timeout = max(5.0, get_config_float("memory_probe_timeout_seconds", 60.0))
        try:
            await asyncio.wait_for(self._probe(state, plan, store), timeout=timeout)
            self._metrics("probe.completed")
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            log(f"深探超时（>{timeout:.0f}s），本轮放弃增量注入", "DEBUG", tag="记忆")
            self._metrics("probe.timeout")
        except Exception as exc:
            log(f"深探执行失败（不影响回复）: {exc}", "DEBUG", tag="记忆")

    async def _probe(self, state: _ProbeState, plan: RetrievalPlan, store: "MemoryStore") -> None:
        """深探主体：快段（图谱邻域，无 LLM）先渲染，慢段（cognee）并入更新。

        分阶段刷新持久渲染缓存：快段毫秒级完成即注入（第一轮被动召回
        之后的最早补充），慢段 LLM 检索完成后整体重渲染——provider 每轮
        读最新值，两阶段对模型表现为"先有关系事实，再有深层记忆/综合"。
        """
        max_chars = max(400, get_config_int("memory_probe_max_chars", 1600))
        sections: List[str] = []
        injected = False

        def _publish() -> None:
            nonlocal injected
            rendered = _render_delta(sections, max_chars)
            if not rendered:
                return
            from core.sanitizer import sanitize_for_context
            state.rendered = sanitize_for_context(rendered)
            if not injected:
                injected = True
                self._metrics("probe.injected")

        # 快段：原生图谱邻域（毫秒级直查权威库，无 LLM）
        relation_lines = await self._collect_relations(state, plan, store)
        if relation_lines:
            sections.append(_section("相关关系", "graph_query 可查全量", relation_lines))
            _publish()
        # 慢段：cognee 深检索（LLM 参与通道），完成后并入更新渲染
        memory_lines, graph_answer = await self._collect_cognee(state, plan, store)
        if memory_lines:
            sections.append(_section("深层记忆", "与首轮召回已去重", memory_lines))
        if graph_answer:
            sections.append(_section("图谱综合", "知识图谱对当前话题的回答", [graph_answer]))
        if memory_lines or graph_answer:
            _publish()
        if sections:
            log(f"深探完成: {len(sections)} 个增量分节（快段先出，慢段更新）", tag="记忆")

    async def _collect_relations(
        self, state: _ProbeState, plan: RetrievalPlan, store: "MemoryStore",
    ) -> List[str]:
        """原生图谱邻域：查询相关实体的一跳活跃边（毫秒级直查权威库，无 LLM）。"""
        if not plan.node_keys:
            return []
        try:
            edges = await store.graph.edges_for_scopes(plan.node_keys, limit=8)
        except Exception as exc:
            log(f"深探图谱邻域查询失败: {exc}", "DEBUG", tag="记忆")
            return []
        from .graph import format_triple
        lines: List[str] = []
        fresh_ids: List[int] = []
        for edge in edges:
            if state.ledger.seen_edge(int(edge["id"])):
                continue
            fresh_ids.append(int(edge["id"]))
            line = format_triple(edge)
            if edge.get("evidence"):
                line += f"（{str(edge['evidence'])[:80]}）"
            lines.append(line)
        state.ledger.record_edges(fresh_ids)
        return lines

    async def _collect_cognee(
        self, state: _ProbeState, plan: RetrievalPlan, store: "MemoryStore",
    ) -> Tuple[List[str], str]:
        """cognee 深检索（经 fusion 统一拼装）：定向实体事实 + 图谱综合答案。"""
        if not plan.queries:
            return [], ""
        try:
            from .cognee.config import load_cognee_config
            from .cognee.fusion import datasets_for_scope, search_cognee
            from .cognee.runtime import get_cognee_client
            config = load_cognee_config()
            client = get_cognee_client()
        except Exception:
            return [], ""
        if client is None or not config.enabled or not config.recall_enabled:
            return [], ""
        query = plan.queries[0]
        datasets = datasets_for_scope(config, state.scope, None)
        try:
            results = await search_cognee(
                client, config, query, datasets, 5,
                ["GRAPH_COMPLETION", "GRAPH_COMPLETION_CONTEXT_EXTENSION", "CHUNKS"],
                node_names=plan.node_labels or None,
            )
        except Exception as exc:
            log(f"深探 cognee 检索失败: {exc}", "DEBUG", tag="记忆")
            return [], ""

        from .recall_format import format_memory_line
        memory_lines: List[str] = []
        graph_answer = ""
        for r in results:
            text = r.snippet.strip()
            if not text or state.ledger.seen_content(text):
                continue
            state.ledger.record_result(r.id, text)
            if r.source == "cognee_graph" and not graph_answer:
                graph_answer = text[:800]
            elif r.source == "cognee_chunk" and len(memory_lines) < 5:
                # 行格式与基底层召回同构（💡 归属标注 正文（时间 记））——
                # fusion 已解析投影文档：snippet 是干净正文、tags 已回填
                memory_lines.append(await format_memory_line(
                    store.graph, snippet=text[:300], tags=r.tags,
                    provenance=r.provenance, timestamp=r.timestamp,
                    sensitivity=r.sensitivity,
                ))
        return memory_lines, graph_answer

    def _sweep(self) -> None:
        """惰性清扫：空态按 TTL 清；持渲染态放宽到日级（超长回复中途不丢失，
        死 scope 的残留上限一天——正常路径由下一回复 begin_reply 重置）。"""
        now = time.monotonic()
        stale = [
            scope for scope, state in self._states.items()
            if now - state.created_at > self._ttl
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
# 回复内通常零变化，异步完成时一次性切换到新渲染）。provider 消息
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
            "description": "深探整体超时（含 cognee 图谱综合 LLM 调用）",
            "default": 60.0,
            "advanced": True,
            "unit": "秒",
        },
    },
}

register_configs_safe(_PROBE_CONFIGS)
