"""召回注入跨来源内容去重单元测试（memories 表 vs cognee 图谱重复注入）。"""

from __future__ import annotations

from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_types import MemorySearchResult


def _result(rid: str, source: str, snippet: str, score: float = 0.9) -> MemorySearchResult:
    return MemorySearchResult(
        id=rid, snippet=snippet, score=score, source=source,
        memory_type="semantic" if source == "memory" else None,
    )


async def _format(results: list[MemorySearchResult]) -> list[dict]:
    # store/embedder 不参与无标签结果的格式化，传 None 即可
    return await MemoryRetriever(None, None)._format_unified_results(results)  # type: ignore[arg-type]


class TestCrossSourceDedup:
    async def test_same_content_deduped(self) -> None:
        """同一内容同时命中 memories 与 cognee：只保留分数最高的一份。"""
        content = "主人喜欢温和耐心的音色，参考腾讯云 p2100862322"
        results = [
            _result("mem:6626", "memory", content, 0.92),
            _result("cognee:abc", "cognee_graph", content, 0.85),
        ]
        msgs = await _format(results)
        assert len(msgs) == 1
        assert "记忆召回" in msgs[0]["content"]

    async def test_whitespace_difference_still_deduped(self) -> None:
        """空白差异（换行/空格）归一化后仍判重。"""
        results = [
            _result("mem:1", "memory", "云鬓花颜金步摇 芙蓉帐暖度春宵", 0.9),
            _result("cognee:x", "cognee_graph", "云鬓花颜金步摇\n芙蓉帐暖度春宵", 0.8),
        ]
        msgs = await _format(results)
        assert len(msgs) == 1
        assert msgs[0]["content"].count("云鬓花颜金步摇") == 1

    async def test_distinct_content_kept(self) -> None:
        """不同内容不去重。"""
        results = [
            _result("mem:1", "memory", "内容甲", 0.9),
            _result("cognee:x", "cognee_graph", "内容乙", 0.8),
        ]
        msgs = await _format(results)
        assert len(msgs) == 1
        assert "内容甲" in msgs[0]["content"] and "内容乙" in msgs[0]["content"]

    async def test_no_debug_noise_injected(self) -> None:
        """注入文本不含 score/来源数据集等调试噪音，保留归因与记录时间。"""
        results = [
            MemorySearchResult(
                id="mem:1", snippet="主人上周搬到了新家", score=0.9,
                source="memory", memory_type="episodic",
                tags=["user:qq:123", "topic:搬家", "type:event", "merged"],
                timestamp=1767225600.0,  # 2026-01-01
            ),
        ]
        msgs = await _format(results)
        content = msgs[0]["content"]
        assert "score=" not in content
        assert "type:event" not in content and "merged" not in content
        assert "搬家" in content  # topic: 标签去前缀保留
        assert "01-01" in content  # 记录时间

    async def test_private_memory_marked(self) -> None:
        """私密记忆注入时带「私事」标注。"""
        results = [
            MemorySearchResult(
                id="mem:2", snippet="小李最近在悄悄准备考试", score=0.9,
                source="memory", memory_type="episodic",
                tags=["topic:考试"], sensitivity="private",
            ),
        ]
        msgs = await _format(results)
        assert "私事" in msgs[0]["content"]
