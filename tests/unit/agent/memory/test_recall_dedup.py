"""召回注入跨来源内容去重单元测试（memories 表 vs cognee 图谱重复注入）。"""

from __future__ import annotations

from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_types import MemorySearchResult


def _result(rid: str, source: str, snippet: str, score: float = 0.9) -> MemorySearchResult:
    return MemorySearchResult(
        id=rid, snippet=snippet, score=score, source=source,
        memory_type="semantic" if source == "memory" else None,
    )


class TestCrossSourceDedup:
    def test_same_content_deduped(self) -> None:
        """同一内容同时命中 memories 与 cognee：只保留分数最高的一份。"""
        content = "主人喜欢温和耐心的音色，参考腾讯云 p2100862322"
        results = [
            _result("mem:6626", "memory", content, 0.92),
            _result("cognee:abc", "cognee_graph", content, 0.85),
        ]
        msgs = MemoryRetriever._format_unified_results(results)
        assert len(msgs) == 1
        assert "记忆召回" in msgs[0]["content"]
        assert "知识图谱" not in msgs[0]["content"]

    def test_whitespace_difference_still_deduped(self) -> None:
        """空白差异（换行/空格）归一化后仍判重。"""
        results = [
            _result("mem:1", "memory", "云鬓花颜金步摇 芙蓉帐暖度春宵", 0.9),
            _result("cognee:x", "cognee_graph", "云鬓花颜金步摇\n芙蓉帐暖度春宵", 0.8),
        ]
        msgs = MemoryRetriever._format_unified_results(results)
        assert len(msgs) == 1

    def test_distinct_content_kept(self) -> None:
        """不同内容不去重（同一注入消息内两个分组都在）。"""
        results = [
            _result("mem:1", "memory", "内容甲", 0.9),
            _result("cognee:x", "cognee_graph", "内容乙", 0.8),
        ]
        msgs = MemoryRetriever._format_unified_results(results)
        assert len(msgs) == 1
        assert "内容甲" in msgs[0]["content"] and "内容乙" in msgs[0]["content"]
