"""异步深探单元测试：召回账本三键去重 + hub 生命周期 + drain 一次性消费。"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.memory.memory_types import RetrievalPlan, normalized_content_key
from agent.memory.probe import DeepProbeHub, RecallLedger, deep_probe_hub

# ==================================================================
# 召回账本（三键防重复）
# ==================================================================

def test_ledger_result_and_content_keys() -> None:
    ledger = RecallLedger()
    ledger.record_result("mem:1", "阿辰喜欢火锅")
    assert ledger.seen_result("mem:1")
    assert not ledger.seen_result("mem:2")
    # 内容键：空白差异不影响命中（同事实不同格式）
    assert ledger.seen_content("阿辰 喜欢 火锅\n")
    assert not ledger.seen_content("完全无关内容")


def test_ledger_edges() -> None:
    ledger = RecallLedger()
    ledger.record_edges([3, 7])
    assert ledger.seen_edge(3)
    assert ledger.seen_edge(7)
    assert not ledger.seen_edge(4)


def test_ledger_reset_clears_all_keys() -> None:
    ledger = RecallLedger()
    ledger.record_result("mem:1", "内容")
    ledger.record_edges([1])
    ledger.reset()
    assert not ledger.seen_result("mem:1")
    assert not ledger.seen_edge(1)
    assert not ledger.seen_content("内容")


# ==================================================================
# hub 生命周期
# ==================================================================

@pytest.mark.asyncio
async def test_hub_begin_reply_resets_ledger() -> None:
    hub = DeepProbeHub()
    hub.ledger("user_qq:1").record_result("mem:1", "旧回复内容")
    hub.begin_reply("user_qq:1")
    # 新回复开账：上一回复的记录不延续
    assert not hub.ledger("user_qq:1").seen_result("mem:1")


@pytest.mark.asyncio
async def test_hub_start_gates_on_plan_intent() -> None:
    """规划无 deep_needed 且无实体节点 → 不启动（按需触发非被动）。"""
    hub = DeepProbeHub()
    hub.begin_reply("user_qq:1")
    hub.start(scope="user_qq:1", plan=RetrievalPlan(queries=["查询"]), store=None)  # type: ignore[arg-type]
    assert hub.state_for("user_qq:1").task is None
    # 实体节点存在 → 启动（store 在 _probe 内才使用，None 不影响门控）
    hub.start(
        scope="user_qq:1",
        plan=RetrievalPlan(queries=["查询"], node_keys=["user:qq:1"]),
        store=None,  # type: ignore[arg-type]
    )
    assert hub.state_for("user_qq:1").task is not None
    await hub.state_for("user_qq:1").task


@pytest.mark.asyncio
async def test_hub_start_single_flight() -> None:
    """同 scope 在途单飞：重复 start 不另起任务。"""
    hub = DeepProbeHub()
    hub.begin_reply("user_qq:1")
    plan = RetrievalPlan(queries=["q"], deep_needed=True)

    async def _hang_probe(state, plan, store):
        await asyncio.sleep(3600)

    hub._run = _hang_probe  # type: ignore[method-assign]
    hub.start(scope="user_qq:1", plan=plan, store=None)  # type: ignore[arg-type]
    first = hub.state_for("user_qq:1").task
    hub.start(scope="user_qq:1", plan=plan, store=None)  # type: ignore[arg-type]
    assert hub.state_for("user_qq:1").task is first
    first.cancel()


@pytest.mark.asyncio
async def test_current_render_is_durable() -> None:
    """持久渲染语义：provider 每轮读取同一值，无新产物拿旧值（不消失）。"""
    hub = DeepProbeHub()
    state = hub.begin_reply("user_qq:1")
    state.rendered = "[系统注入·记忆召回·续] 增量内容"
    assert hub.current_render("user_qq:1") == "[系统注入·记忆召回·续] 增量内容"
    assert hub.current_render("user_qq:1") == "[系统注入·记忆召回·续] 增量内容"
    assert hub.current_render("unknown_scope") == ""
    # 新回复开账重置：不跨回复持久（防与基底层召回常驻重复）
    hub.begin_reply("user_qq:1")
    assert hub.current_render("user_qq:1") == ""


# ==================================================================
# 探针端到端（真实 store，cognee 未绑定 → 仅原生图谱邻域）
# ==================================================================

@pytest.mark.asyncio
async def test_probe_renders_relation_delta_with_ledger_dedup(store) -> None:
    await store.graph.add_relation(
        "user:qq:1", "朋友", "user:qq:2", subject_label="阿辰", object_label="老王",
        evidence="常一起吃饭",
    )
    hub = DeepProbeHub()
    state = hub.begin_reply("user_qq:1")
    plan = RetrievalPlan(queries=["阿辰的朋友是谁"], node_keys=["user:qq:1"])
    # 基底层已注入的关系边入账 → 探针不重复（cognee 客户端未绑定，天然跳过）
    state.ledger.record_edges([1])
    await hub._probe(state, plan, store)

    # 账本里的边被跳过 → 无增量
    assert state.rendered == ""

    # 新账本（未记录该边）→ 渲染关系增量
    state2 = hub.begin_reply("user_qq:1")
    await hub._probe(state2, plan, store)
    assert state2.rendered
    content = state2.rendered
    assert "记忆召回·续" in content
    assert "朋友" in content
    # 边 id 已入账：重复 drain 不会再现
    assert state2.ledger.seen_edge(1)


def test_normalized_content_key_strips_whitespace() -> None:
    assert normalized_content_key("a b\nc") == normalized_content_key("abc")
    assert normalized_content_key("") == ""


def test_singleton_exported() -> None:
    assert isinstance(deep_probe_hub, DeepProbeHub)


# ==================================================================
# 端到端注入管线（recall_split fire_probe → 探针完成 → drain 增量）
# ==================================================================

class _NullEmbedder:
    available = False

    async def embed_query(self, _query: str):
        return None


@pytest.mark.asyncio
async def test_recall_split_fires_probe_and_drains_delta(store, monkeypatch) -> None:
    """回复路径端到端：被动召回（无结果 fallback 分支）→ 深探完成 →
    轮顶 drain 出格式正确的增量消息（cognee 未绑定，仅原生图谱邻域）。"""
    import agent.memory.memory_retriever as retriever_mod
    from agent.memory.memory_retriever import MemoryRetriever
    from agent.memory.memory_types import RetrievalPlan

    await store.graph.add_relation(
        "user:qq:1", "朋友", "user:qq:2", subject_label="阿辰", object_label="老王",
        evidence="常一起吃饭",
    )

    async def _plan(self, query: str) -> RetrievalPlan:
        return RetrievalPlan(queries=["阿辰的朋友"], deep_needed=True, node_keys=["user:qq:1"])

    monkeypatch.setattr(MemoryRetriever, "plan_retrieval", _plan)
    hub = DeepProbeHub()
    # retriever 模块级持有 hub 引用，patch 必须打到它的绑定上
    monkeypatch.setattr(retriever_mod, "deep_probe_hub", hub)

    retriever = MemoryRetriever(store, _NullEmbedder())
    conversation = [{"role": "user", "content": "阿辰的朋友是谁呀，我想了解一下他的情况"}]
    _profile, _memory = await retriever.recall_split(
        conversation, entity_scope="user_qq:1", fire_probe=True,
    )

    # 空库无结果 → fallback 分支也必须启动深探（首轮挖不到更需深层补充）
    state = hub.state_for("user_qq:1")
    assert state is not None and state.task is not None
    await state.task

    content = hub.current_render("user_qq:1")
    assert content.startswith("[系统注入·记忆召回·续]")
    assert "▸ 相关关系 · graph_query 可查全量" in content
    assert "  - 阿辰" in content and "朋友" in content and "老王" in content
    # 持久在场：provider 每轮读取同一渲染（字节稳定，不消失）
    assert hub.current_render("user_qq:1") == content


@pytest.mark.asyncio
async def test_recall_split_without_fire_probe_skips_hub(store, monkeypatch) -> None:
    """非回复路径（fire_probe=False，心跳/任务/子代理）不触碰深探状态。"""
    import agent.memory.memory_retriever as retriever_mod
    from agent.memory.memory_retriever import MemoryRetriever

    hub = DeepProbeHub()
    monkeypatch.setattr(retriever_mod, "deep_probe_hub", hub)
    retriever = MemoryRetriever(store, _NullEmbedder())
    await retriever.recall_split(
        [{"role": "user", "content": "随便聊点什么内容的一句话"}],
        entity_scope="user_qq:1",
    )
    assert hub.state_for("user_qq:1") is None


def test_record_broadcasts_when_scope_unknown() -> None:
    """记账无 scope 时广播：进行中回复的账本也能感知（防隔离漏去重）。"""
    from agent.memory.memory_types import MemorySearchResult

    hub = DeepProbeHub()
    reply_state = hub.begin_reply("user_qq:1")
    hub.record("", results=[
        MemorySearchResult(id="mem:9", snippet="阿辰 喜欢 火锅", score=0.9),
    ])
    assert reply_state.ledger.seen_result("mem:9")
    assert reply_state.ledger.seen_content("阿辰喜欢火锅")
    # scope 已知时定向记账，不影响其他账本
    other = hub.begin_reply("user_qq:2")
    hub.record("user_qq:1", results=[
        MemorySearchResult(id="mem:10", snippet="另一条", score=0.8),
    ])
    assert hub.ledger("user_qq:1").seen_result("mem:10")
    assert not other.ledger.seen_result("mem:10")


# ==================================================================
# provider 通道（持久渲染缓存的消费面）
# ==================================================================

def test_probe_provider_registered_and_reads_cache(monkeypatch) -> None:
    """provider 注册在案（priority/门控键/函数模式），provide 直读持久缓存。"""
    from core.context_provider import ContextProviderRegistry

    meta = ContextProviderRegistry._providers.get("memory_deep_probe")
    assert meta is not None
    assert meta.priority == 34
    assert meta.group == "memory"
    assert meta.inject_key == "memory_probe_inject"

    import agent.memory.probe as probe_mod
    hub = DeepProbeHub()
    monkeypatch.setattr(probe_mod, "deep_probe_hub", hub)  # provide 读模块级单例
    state = hub.begin_reply("user_qq:1")
    state.rendered = "增量渲染"
    assert meta.provide_fn("user_qq:1") == "增量渲染"  # type: ignore[operator]
    assert meta.provide_fn("user_qq:2") == ""  # type: ignore[operator]


def test_begin_reply_keeps_old_task_from_serving() -> None:
    """新回复重置渲染：上一回复的深探结果不泄漏进新回复的注入。"""
    hub = DeepProbeHub()
    state = hub.begin_reply("user_qq:1")
    state.rendered = "旧回复的增量"
    hub.begin_reply("user_qq:1")
    assert hub.current_render("user_qq:1") == ""


@pytest.mark.asyncio
async def test_probe_staged_fast_section_publishes_before_slow(store) -> None:
    """分阶段渲染（快唤醒→慢巩固）：慢段执行时快段已在缓存中可见。"""
    from agent.memory.memory_types import RetrievalPlan

    await store.graph.add_relation(
        "user:qq:1", "朋友", "user:qq:2", subject_label="阿辰", object_label="老王",
    )
    hub = DeepProbeHub()
    state = hub.begin_reply("user_qq:1")
    plan = RetrievalPlan(queries=["q"], deep_needed=True, node_keys=["user:qq:1"])

    seen_at_slow_start: list[str] = []

    async def _slow_cognee(state_arg, plan_arg, store_arg):
        seen_at_slow_start.append(hub.current_render("user_qq:1"))
        return [], ""

    hub._collect_cognee = _slow_cognee  # type: ignore[method-assign]
    await hub._probe(state, plan, store)

    # 慢段启动时，快段（图谱邻域，无 LLM）已渲染在场
    assert "相关关系" in seen_at_slow_start[0]
    assert "阿辰" in seen_at_slow_start[0]


# ==================================================================
# 跨通道重复审计：被动注入 / AI 工具 / 异步深探 三通道零重复
# ==================================================================

@pytest.mark.asyncio
async def test_cross_channel_no_duplication(store, monkeypatch) -> None:
    """机械化保证：同一回复内，基底层关系快照、recall 工具返回的关系边、
    深探增量分节——三个通道的产物互不重复（账本三键去重的端到端证明）。"""
    import agent.memory.memory_retriever as retriever_mod
    import agent.memory.tools as mem_tools
    from agent.memory.memory_retriever import MemoryRetriever
    from agent.memory.memory_types import RetrievalPlan

    await store.graph.add_relation(
        "user:qq:1", "朋友", "user:qq:2", subject_label="阿辰", object_label="老王",
        evidence="常一起吃饭",
    )
    hub = DeepProbeHub()
    monkeypatch.setattr(retriever_mod, "deep_probe_hub", hub)
    monkeypatch.setattr(mem_tools, "deep_probe_hub", hub)

    # 通道 1：被动召回（基底层关系快照入账）
    async def _plan(self, query: str) -> RetrievalPlan:
        return RetrievalPlan(queries=["q"], deep_needed=True, node_keys=["user:qq:1"])

    retriever = MemoryRetriever(store, _NullEmbedder())
    hub.begin_reply("user_qq:1")
    await retriever.load_relation_snippets(["user_qq:1"])  # 通道 1：基底层关系快照（边入账）

    # 通道 2：AI 主动 recall 工具（deep 返回 relations，边同样入账）
    from agent.memory.tools import recall as recall_tool
    mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
    out = json.loads(await recall_tool(
        "阿辰的朋友", tags="user:qq:1", depth="deep",
    ))
    assert out.get("relations"), "工具应返回关系边"

    # 通道 3：异步深探（对账本外的边渲染；确定性时序——记账全部先行）
    hub.start(
        scope="user_qq:1",
        plan=RetrievalPlan(queries=["阿辰的朋友"], deep_needed=True, node_keys=["user:qq:1"]),
        store=store,
    )
    state = hub.state_for("user_qq:1")
    assert state is not None and state.task is not None
    await state.task

    content = hub.current_render("user_qq:1")
    # 基底层与工具都已展示过这条边 → 深探增量不再包含（零重复）
    assert "相关关系" not in content


@pytest.mark.asyncio
async def test_tool_edges_alone_block_probe_duplication(store, monkeypatch) -> None:
    """通道 2 单独隔离验证：仅工具返回过关系边（无基底层快照），
    深探也不再注入同一边——工具边记账自身的防重复证明。"""
    import agent.memory.tools as mem_tools
    from agent.memory.memory_types import RetrievalPlan

    await store.graph.add_relation(
        "user:qq:1", "朋友", "user:qq:2", subject_label="阿辰", object_label="老王",
    )
    hub = DeepProbeHub()
    monkeypatch.setattr(mem_tools, "deep_probe_hub", hub)
    hub.begin_reply("user_qq:1")

    from agent.memory.tools import recall as recall_tool
    mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
    out = json.loads(await recall_tool("朋友", tags="user:qq:1", depth="deep"))
    assert out.get("relations")

    hub.start(
        scope="user_qq:1",
        plan=RetrievalPlan(queries=["朋友"], deep_needed=True, node_keys=["user:qq:1"]),
        store=store,
    )
    state = hub.state_for("user_qq:1")
    assert state is not None and state.task is not None
    await state.task
    assert "相关关系" not in hub.current_render("user_qq:1")


def test_record_broadcast_supports_generator_iterables() -> None:
    """广播记账对生成器入参完整：多账本各得全量（生成器只能消费一次的回归锁）。"""
    from agent.memory.memory_types import MemorySearchResult

    hub = DeepProbeHub()
    hub.begin_reply("user_qq:1")
    hub.begin_reply("user_qq:2")
    hub.record("", results=(r for r in [
        MemorySearchResult(id="mem:1", snippet="内容", score=0.9),
    ]))
    hub.record("", edge_ids=(i for i in [3, 7]))
    for scope in ("user_qq:1", "user_qq:2"):
        ledger = hub.ledger(scope)
        assert ledger.seen_result("mem:1")
        assert ledger.seen_edge(3) and ledger.seen_edge(7)
