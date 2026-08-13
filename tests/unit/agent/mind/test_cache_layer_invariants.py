"""缓存分层不变量测试：记忆系统内容永不污染 LLM 前缀缓存。

红线（对应 AGENTS.md 缓存命中率排查手册）：
1. 记忆/画像/关系/技能/状态类内容块的 volatility 必须 > VOL_HISTORY(30)，
   永不进入 stable/summary/conversation 前缀层；
2. 永久记忆 pin 块必须独立成一条消息——与每轮变化的召回内容合并成一条
   会把变化带进 context 层，击穿其后摘要与对话历史的缓存前缀
   （AGENTS.md:399 记录过的已根治回归）；
3. 召回渲染时间戳只准日期粒度（秒级时间戳会让内容每轮变化）。
"""

from __future__ import annotations

import re

import pytest

import agent.mind.context_assembly  # noqa: F401  导入触发 @context_block 注册
from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_types import MemorySearchResult
from agent.mind.context_pipeline import (
    _LAYER_REGISTRY,
    VOL_HISTORY,
    VOL_STABLE,
)

# 记忆系统相关内容块：必须全部位于历史锚点之后的尾部动态区
_MEMORY_FAMILY_LAYERS = (
    "status", "profile", "relation", "goals", "volatile", "memory", "provider",
)
# 缓存前缀层：stable/summary/conversation 之外任何内容不得混入
_PREFIX_LAYERS = ("stable", "summary", "conversation")


class TestLayerVolatilityInvariants:
    def test_prefix_layers_at_or_below_history(self):
        for layer in _PREFIX_LAYERS:
            meta = _LAYER_REGISTRY.get(layer)
            assert meta is not None, f"层未注册: {layer}"
            assert meta.volatility <= VOL_HISTORY, (
                f"{layer} 是前缀缓存层，volatility 必须 ≤ {VOL_HISTORY}，实际 {meta.volatility}"
            )

    def test_memory_family_layers_after_history_anchor(self):
        for layer in _MEMORY_FAMILY_LAYERS:
            meta = _LAYER_REGISTRY.get(layer)
            assert meta is not None, f"层未注册: {layer}"
            assert meta.volatility > VOL_HISTORY, (
                f"记忆相关层 {layer} 的 volatility={meta.volatility} ≤ {VOL_HISTORY}，"
                "会进入缓存前缀，每轮变化将击穿 stable/summary/conversation 锚点"
            )

    def test_stable_layer_is_zero(self):
        assert _LAYER_REGISTRY["stable"].volatility == VOL_STABLE


def _result(rid: str, snippet: str, *, pinned: bool = False, source: str = "memory",
            tags=None) -> MemorySearchResult:
    return MemorySearchResult(
        id=rid, snippet=snippet, score=0.9, source=source,
        memory_type="semantic", tags=tags or [], timestamp=1_760_000_000.0,
        provenance={"pinned": True} if pinned else {},
    )


class TestPermanentPinBlockInvariant:
    """pin 块独立成消息（防 AGENTS.md:399 回归）。"""

    @pytest.fixture
    def retriever(self):
        # store/embedder 在本测试路径不触达（标签无实体前缀，不查图谱）
        return MemoryRetriever(store=None, embedder=None)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_pin_block_is_standalone_message(self, retriever):
        results = [
            _result("mem:1", "主人不吃辣", pinned=True, tags=["type:permanent"]),
            _result("mem:2", "上周五去看了演唱会", tags=["type:event"]),
            _result("file:1", "便签内容", source="file"),
        ]
        messages = await retriever._format_unified_results(results)
        pin_msgs = [m for m in messages if m["content"].startswith("[系统注入·永久记忆]")]
        assert len(pin_msgs) == 1, "永久记忆块必须且只能是一条独立消息"
        # pin 块不混入召回/检索内容；召回块不混入 pin 内容
        assert "上周五" not in pin_msgs[0]["content"]
        assert "便签内容" not in pin_msgs[0]["content"]
        other = [m for m in messages if not m["content"].startswith("[系统注入·永久记忆]")]
        assert other and "主人不吃辣" not in other[0]["content"]

    @pytest.mark.asyncio
    async def test_pin_block_byte_stable_across_calls(self, retriever):
        """同样的 pin 输入两次渲染字节一致（内容寻址缓存的前提）。"""
        results = [_result("mem:1", "主人不吃辣", pinned=True)]
        first = await retriever._format_unified_results(results)
        second = await retriever._format_unified_results(results)
        assert first[0]["content"] == second[0]["content"]

    @pytest.mark.asyncio
    async def test_no_pin_no_pin_message(self, retriever):
        messages = await retriever._format_unified_results(
            [_result("mem:2", "普通记忆")],
        )
        assert all(not m["content"].startswith("[系统注入·永久记忆]") for m in messages)


class TestRecallTimeGranularity:
    def test_memory_time_is_day_granularity(self):
        import time as _time
        now = _time.time()
        out = MemoryRetriever._format_memory_time(now)
        assert re.fullmatch(r"\d{2}-\d{2}|\d{4}-\d{2}-\d{2}", out), (
            f"召回时间戳必须是日期粒度，实际: {out!r}"
        )


class TestRecallFailOpen:
    """记忆子系统故障只产生空块，绝不向上下文写入错误文案。"""

    @pytest.mark.asyncio
    async def test_relation_snippets_fail_open(self):
        class _ExplodingGraph:
            async def edges_for_scopes(self, keys, limit=15):
                raise RuntimeError("graph db down")

        class _Store:
            graph = _ExplodingGraph()

        retriever = MemoryRetriever(store=_Store(), embedder=None)  # type: ignore[arg-type]
        assert await retriever.load_relation_snippets(["user_qq:123"]) == []

    @pytest.mark.asyncio
    async def test_format_results_without_provenance(self):
        retriever = MemoryRetriever(store=None, embedder=None)  # type: ignore[arg-type]
        r = MemorySearchResult(
            id="mem:9", snippet="无 provenance 的记忆", score=0.5,
            source="memory", memory_type="semantic", tags=[], timestamp=0.0,
        )
        messages = await retriever._format_unified_results([r])
        assert len(messages) == 1
        assert "无 provenance 的记忆" in messages[0]["content"]
