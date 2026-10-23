"""记忆去重裁决单元测试：候选准入 / light_llm 调用行为（流式通道 + 思考档位）。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.memory import dedup
from agent.memory.memory_types import MemoryEntry, MemoryType


async def test_candidates_retain_relevant_old_facts_and_cross_identity(monkeypatch) -> None:
    old = MemoryEntry(id=1, memory_type=MemoryType.SEMANTIC, content="旧地址", timestamp=1, tags=["user:qq:1"])
    other = MemoryEntry(id=2, memory_type=MemoryType.SEMANTIC, content="另一个人的地址", timestamp=2,
                        tags=["user:qq:2"])
    recent = MemoryEntry(id=3, memory_type=MemoryType.SEMANTIC, content="无关新事实", timestamp=999)
    text_started, vector_started = asyncio.Event(), asyncio.Event()

    async def text_lane(*args, **kwargs):
        text_started.set()
        await vector_started.wait()
        return [(old, 0.2), (other, 0.1), (recent, 0.01)]

    async def embed(*args):
        vector_started.set()
        await text_started.wait()
        return [1, 0]

    store = SimpleNamespace(search_fts=text_lane, search_vector=AsyncMock(return_value=[(old, 0.9)]))
    monkeypatch.setattr(dedup, "get_config_int", lambda key, default: 2)
    async with asyncio.timeout(3):
        candidates = await dedup.gather_dedup_candidates(store, SimpleNamespace(embed_query=embed), "搬家了")
    assert [entry.id for entry in candidates] == [1, 2]
    # 没有向量证据的跨身份 FTS 候选仍交给模型，不能被阈值提前判成 novel。
    assert candidates[1].tags == ["user:qq:2"]


async def test_disabled_dedup_does_not_request_embeddings_or_candidates(monkeypatch) -> None:
    store = SimpleNamespace(search_fts=AsyncMock(), search_vector=AsyncMock())
    embedder = SimpleNamespace(embed_query=AsyncMock())
    monkeypatch.setattr(dedup, "get_config_bool", lambda key, default: False)
    assert await dedup.gather_dedup_candidates(store, embedder, "新事实") == []
    store.search_fts.assert_not_called()
    store.search_vector.assert_not_called()
    embedder.embed_query.assert_not_called()


@pytest.mark.asyncio
async def test_gather_candidates_excludes_goal_source(tmp_path) -> None:
    """goal 条目（结构化 JSON，规划状态机独占）不进判重候选——回归
    2026-09-15 事故：带 goal:{id} 标签的 memorize 语义合并把目标条目吞掉。"""
    from agent.memory.memory_store import MemoryStore
    from agent.memory.memory_types import GOAL_SOURCE, MemoryEntry, MemoryType

    store = MemoryStore(str(tmp_path / "mem.db"))
    try:
        goal_doc = json.dumps({
            "goal_id": "ebafea16", "title": "反思死循环二次复发排查与闭环",
            "status": "active",
            "steps": [{"index": 0, "content": "夜间排查根因", "status": "pending"}],
        }, ensure_ascii=False)
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content=goal_doc,
            source=GOAL_SOURCE, tags=["goal:ebafea16"],
        ))
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content="反思死循环排查的结论与时间线",
        ))

        query = "反思死循环二次复发排查与闭环"
        # 前置：FTS 确实召回了 goal 条目（保证过滤逻辑被真实执行）
        fts_hits = [e for e, _ in await store.search_fts(query, limit=5)]
        assert any(e.source == GOAL_SOURCE for e in fts_hits)

        candidates = await dedup.gather_dedup_candidates(store, None, query)
        assert all(e.source != GOAL_SOURCE for e in candidates)
        # 普通语义记忆仍可作为候选
        assert any(e.source != GOAL_SOURCE for e in candidates)
    finally:
        await store.close()


class _FakeManager:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def chat_with_fallback(self, messages: list[dict], **kwargs: Any) -> Any:
        self.kwargs = kwargs
        self.messages = messages
        return SimpleNamespace(content='{"action": "store"}')


@pytest.mark.asyncio
async def test_light_llm_uses_stream_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """裁决调用走流式通道：空闲判死（思考中不掐断），长思考模型不被墙钟截断。"""
    manager = _FakeManager()
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)
    monkeypatch.setattr(dedup, "get_config", lambda key, default=None: default)

    result = await dedup.light_llm("裁决提示词")

    assert result == '{"action": "store"}'
    assert manager.kwargs["stream"] is True
    assert manager.kwargs["timeout"] == 120.0
    assert manager.kwargs["options"] == {"temperature": 0.1}
    assert manager.messages == [{"role": "user", "content": "裁决提示词"}]


@pytest.mark.asyncio
async def test_light_llm_applies_judge_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """memory_judge_reasoning_effort 配置生效时注入 per-call 思考档位。"""
    manager = _FakeManager()
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)
    monkeypatch.setattr(
        dedup, "get_config",
        lambda key, default=None: "low" if key == "memory_judge_reasoning_effort" else default,
    )

    await dedup.light_llm("p", temperature=0.2)

    assert manager.kwargs["options"] == {"temperature": 0.2, "reasoning_effort": "low"}


@pytest.mark.asyncio
async def test_light_llm_ignores_invalid_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法思考档位被 normalize_effort 归一为空，不注入 options。"""
    manager = _FakeManager()
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)
    monkeypatch.setattr(
        dedup, "get_config",
        lambda key, default=None: "bogus" if key == "memory_judge_reasoning_effort" else default,
    )

    await dedup.light_llm("p")

    assert "reasoning_effort" not in manager.kwargs["options"]


@pytest.mark.asyncio
async def test_light_llm_uses_dedicated_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """memory_light_model 配置生效时经专用轻量模型 client 调用。"""
    manager = _FakeManager()
    sentinel = object()
    manager.get_enabled_client = lambda name: sentinel if name == "fast-model" else None  # type: ignore[method-assign]
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)
    monkeypatch.setattr(
        dedup, "get_config",
        lambda key, default=None: "fast-model" if key == "memory_light_model" else default,
    )

    await dedup.light_llm("p")

    assert manager.kwargs["client"] is sentinel


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [False, True])
async def test_light_llm_unknown_model_falls_back_default(
    monkeypatch: pytest.MonkeyPatch, exists: bool,
) -> None:
    """专用模型不存在/停用时 client 为 None，回落默认主模型。"""
    manager = _FakeManager()
    manager.get_enabled_client = lambda name: None  # type: ignore[method-assign]
    manager.get_client = lambda name: object() if exists else None
    logs = []
    monkeypatch.setattr(dedup, "log", lambda message, *a, **kw: logs.append(message))
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)
    monkeypatch.setattr(
        dedup, "get_config",
        lambda key, default=None: "ghost-model" if key == "memory_light_model" else default,
    )

    await dedup.light_llm("p")

    assert manager.kwargs["client"] is None
    assert len(logs) == 1
    assert "memory_light_model='ghost-model'" in logs[0]
    assert ("已停用" if exists else "不存在") in logs[0]
    assert "清空此项跟随默认模型" in logs[0]
