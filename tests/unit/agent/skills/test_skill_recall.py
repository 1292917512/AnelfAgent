"""技能召回链路集成测试：记忆检索计划复用（plan_out）+ 车道拼接 + 归档命中渲染。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_types import RetrievalPlan
from agent.mind import recollection
from agent.skills.skill_store import Skill, SkillState


class _NullEmbedder:
    available = False

    async def embed_query(self, _query: str):
        return None


# ==================================================================
# recall_split 的 plan_out 出参（技能匹配复用记忆检索计划的通道）
# ==================================================================

@pytest.mark.asyncio
async def test_plan_out_filled_with_planned_queries(store, monkeypatch) -> None:
    """正常召回：plan_out 拿到 LLM 规划的最终多查询（技能车道直接复用）。"""

    async def _plan(self, query: str, *, timeout: float | None = None) -> RetrievalPlan:
        return RetrievalPlan(queries=[query, "规划查询甲", "规划查询乙"])

    monkeypatch.setattr(MemoryRetriever, "plan_retrieval", _plan)
    retriever = MemoryRetriever(store, _NullEmbedder())
    conversation = [{"role": "user", "content": "帮我查一下服务器最近的巡检记录和告警情况"}]
    holder: dict = {}
    await retriever.recall_split(conversation, plan_out=holder)
    assert len(holder["queries"]) == 3
    assert holder["queries"][1:] == ["规划查询甲", "规划查询乙"]


@pytest.mark.asyncio
async def test_plan_out_empty_on_trivial_turn(store, monkeypatch) -> None:
    """平凡轮早退：无规划发生，plan_out 落笔为空（技能侧退回基查询单发）。"""
    monkeypatch.setattr(MemoryRetriever, "_is_trivial_turn", lambda self, c: True)
    retriever = MemoryRetriever(store, _NullEmbedder())
    holder: dict = {}
    await retriever.recall_split(
        [{"role": "user", "content": "好的好的，知道了"}], plan_out=holder,
    )
    assert holder["queries"] == []


@pytest.mark.asyncio
async def test_plan_out_empty_when_no_query(store) -> None:
    """无有效查询（短消息）早退：plan_out 同样落笔为空。"""
    retriever = MemoryRetriever(store, _NullEmbedder())
    holder: dict = {}
    await retriever.recall_split([{"role": "user", "content": "嗯"}], plan_out=holder)
    assert holder["queries"] == []


# ==================================================================
# _match_skills 的车道拼接与归档渲染
# ==================================================================

def _fake_skill(name: str, state: SkillState = SkillState.ACTIVE) -> Skill:
    return Skill(name=name, description=f"技能 {name} 的用途说明",
                 content=f"{name} 正文", state=state)


def _mind_with_matcher(matched=None, captured: dict | None = None):
    """最小 Mind 替身：matcher 返回固定结果并捕获查询车道。"""
    store = SimpleNamespace(
        get=lambda name: None,
        record_use=MagicMock(),
        record_match=MagicMock(),
        restore=MagicMock(),
    )

    async def match(queries, *, top_k=3, min_score=None, query_vec=None):
        if captured is not None:
            captured["queries"] = list(queries)
            captured["query_vec"] = query_vec
        return list(matched or [])

    return SimpleNamespace(
        _skills_enabled=lambda: True,
        skill_store=store,
        skill_matcher=SimpleNamespace(match=match),
        pfc=SimpleNamespace(add_temporary=MagicMock()),
        _pending_skill_gestures={},
    )


class TestMatchSkillsLanes:
    @pytest.mark.asyncio
    async def test_planned_queries_become_lanes(self) -> None:
        """规划查询拼成后续车道：基查询去重、总量上限 3 条。"""
        captured: dict = {}
        mind = _mind_with_matcher(captured=captured)
        await recollection._match_skills(
            mind, "基查询", query_vec=[0.1, 0.2],
            planned_queries=["基查询", "规划乙", "规划丙", "规划丁"],
        )
        assert captured["queries"] == ["基查询", "规划乙", "规划丙"]
        assert captured["query_vec"] == [0.1, 0.2]  # 预计算向量透传首车道

    @pytest.mark.asyncio
    async def test_empty_base_with_planned_only(self) -> None:
        """基查询为空时车道 = 规划查询（无预计算向量照配首车道语义）。"""
        captured: dict = {}
        mind = _mind_with_matcher(captured=captured)
        await recollection._match_skills(mind, "", planned_queries=["规划乙"])
        assert captured["queries"] == ["规划乙"]

    @pytest.mark.asyncio
    async def test_no_lanes_skips_matcher(self) -> None:
        """无任何查询文本时不调用匹配器（空车道无意义）。"""
        captured: dict = {}
        mind = _mind_with_matcher(captured=captured)
        msgs = await recollection._match_skills(mind, "", planned_queries=[])
        assert msgs == [] and "queries" not in captured


class TestMatchSkillsArchivedRendering:
    @pytest.mark.asyncio
    async def test_archived_hit_labeled_with_restore_hint(self) -> None:
        """归档命中：行内【已归档】标注 + 块尾 restore_skill 指引，照计 match。"""
        archived = _fake_skill("dusty-skill", state=SkillState.ARCHIVED)
        active = _fake_skill("fresh-skill", state=SkillState.ACTIVE)
        mind = _mind_with_matcher(matched=[(archived, 0.6), (active, 0.5)])
        msgs = await recollection._match_skills(mind, "查询")
        assert msgs
        content = msgs[0]["content"]
        assert "## dusty-skill【已归档】" in content
        assert "## fresh-skill — " in content  # 在役行不带标注
        assert "restore_skill" in content
        assert mind.skill_store.record_match.call_count == 2

    @pytest.mark.asyncio
    async def test_no_archived_no_hint(self) -> None:
        """无归档命中时块尾不加恢复指引（零噪音）。"""
        mind = _mind_with_matcher(matched=[(_fake_skill("fresh-skill"), 0.5)])
        msgs = await recollection._match_skills(mind, "查询")
        assert msgs and "restore_skill" not in msgs[0]["content"]
