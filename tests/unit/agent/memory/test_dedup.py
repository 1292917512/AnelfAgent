"""记忆去重裁决单元测试：候选准入 / light_llm 调用行为（流式通道 + 思考档位）。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent.memory import dedup


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
