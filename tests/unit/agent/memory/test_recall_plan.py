"""检索规划单元测试：LLM 计划宽松解析 / 失败回退 / 多查询共识融合。"""

from __future__ import annotations

import pytest

from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_types import MemorySearchResult, RetrievalPlan


class _NullEmbedder:
    available = False

    async def embed_query(self, _query: str):
        return None


def _retriever(store) -> MemoryRetriever:
    return MemoryRetriever(store, _NullEmbedder())


def _patch_light_llm(monkeypatch, payload):
    import agent.memory.dedup as dedup_mod

    async def _fake(prompt: str, **kwargs):
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(dedup_mod, "light_llm", _fake)


# ==================================================================
# 计划 JSON 宽松解析
# ==================================================================

def test_parse_plan_json_plain() -> None:
    raw = '{"queries": ["阿辰 火锅", "阿辰 口味"], "entities": ["阿辰"], "deep_needed": true, "rationale": "涉及人物"}'
    parsed = MemoryRetriever._parse_plan_json(raw)
    assert parsed is not None
    assert parsed["deep_needed"] is True


def test_parse_plan_json_embedded_in_prose() -> None:
    raw = '好的，这是计划：\n{"queries": ["q1"], "entities": [], "deep_needed": false}\n以上。'
    parsed = MemoryRetriever._parse_plan_json(raw)
    assert parsed is not None
    assert parsed["queries"] == ["q1"]


def test_parse_plan_json_invalid_returns_none() -> None:
    assert MemoryRetriever._parse_plan_json("not json at all") is None
    assert MemoryRetriever._parse_plan_json('{"broken": ') is None
    assert MemoryRetriever._parse_plan_json('[]') is None


# ==================================================================
# 规划调用：成功 / 回退
# ==================================================================

@pytest.mark.asyncio
async def test_plan_retrieval_parses_llm_output(store, monkeypatch) -> None:
    _patch_light_llm(
        monkeypatch,
        '{"queries": ["阿辰 喜欢 什么", "阿辰 口味 偏好"], '
        '"entities": ["阿辰"], "deep_needed": true, "rationale": "人物偏好类问题"}',
    )
    retriever = _retriever(store)
    plan = await retriever.plan_retrieval("这是一段足够长的对话上下文" * 4)
    assert plan.queries == ["阿辰 喜欢 什么", "阿辰 口味 偏好"]
    assert plan.entities == ["阿辰"]
    assert plan.deep_needed is True


@pytest.mark.asyncio
async def test_plan_retrieval_falls_back_on_garbage(store, monkeypatch) -> None:
    query = "这是一段足够长的对话上下文" * 4
    _patch_light_llm(monkeypatch, "模型自由发挥不想输出 JSON")
    plan = await _retriever(store).plan_retrieval(query)
    assert plan.queries == [query]
    assert plan.deep_needed is False


@pytest.mark.asyncio
async def test_plan_retrieval_falls_back_on_error(store, monkeypatch) -> None:
    query = "这是一段足够长的对话上下文" * 4
    _patch_light_llm(monkeypatch, RuntimeError("llm down"))
    plan = await _retriever(store).plan_retrieval(query)
    assert plan.queries == [query]


@pytest.mark.asyncio
async def test_plan_retrieval_short_query_skips_llm(store, monkeypatch) -> None:
    called = {"n": 0}

    import agent.memory.dedup as dedup_mod

    async def _should_not_call(prompt: str, **kwargs):
        called["n"] += 1
        return "{}"

    monkeypatch.setattr(dedup_mod, "light_llm", _should_not_call)
    plan = await _retriever(store).plan_retrieval("短查询")
    assert called["n"] == 0
    assert plan.queries == ["短查询"]


# ==================================================================
# 多查询共识融合
# ==================================================================

def _item(rid: str, score: float, memory_id: str = "") -> MemorySearchResult:
    return MemorySearchResult(
        id=rid, snippet=f"内容{rid}", score=score,
        provenance={"anelf_memory_id": memory_id} if memory_id else {},
    )


def test_merge_consensus_boosts_multi_lane_hits() -> None:
    shared_a = _item("cognee:1", 0.6, memory_id="7")   # 与原生 mem:7 同源
    shared_b = _item("mem:7", 0.8, memory_id="7")
    only_first = _item("mem:8", 0.85)

    merged = MemoryRetriever.merge_consensus(
        [[shared_a, only_first], [shared_b]],
        limit=10,
    )
    ids = [r.id for r in merged]
    # 同 anelf_memory_id 的两路命中合并为一条（取最高分），且获得共识加成后置顶
    assert ids.count("mem:7") + ids.count("cognee:1") == 1
    assert merged[0].id == "mem:7"
    assert merged[0].score == pytest.approx(min(1.0, 0.8 * 1.1), abs=0.001)
    # 单路命中的分数不受加成
    only = next(r for r in merged if r.id == "mem:8")
    assert only.score == 0.85


def test_merge_consensus_respects_limit() -> None:
    lanes = [[_item(f"mem:{i}", 0.5) for i in range(10)]]
    assert len(MemoryRetriever.merge_consensus(lanes, limit=3)) == 3


def test_plan_dataclass_defaults() -> None:
    plan = RetrievalPlan(queries=["q"])
    assert plan.entities == [] and plan.node_keys == [] and plan.node_labels == []
    assert plan.deep_needed is False and plan.rationale == ""
