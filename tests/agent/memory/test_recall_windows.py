"""多窗口召回增强（memory_retriever）单元测试。"""

from __future__ import annotations

import time

from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_types import MemorySearchResult, MemoryType


def _result(rid: str, score: float, mtype: str = "semantic", ts: float = 0.0) -> MemorySearchResult:
    return MemorySearchResult(
        id=rid, snippet=f"内容{rid}", score=score, memory_type=mtype,
        provenance={"timestamp": ts} if ts else {},
    )


class TestFocusQuery:
    def test_extracts_last_user_message(self) -> None:
        conv = [
            {"role": "user", "content": "今天天气怎么样"},
            {"role": "assistant", "content": "晴天"},
            {"role": "user", "content": "那适合出去玩吗"},
        ]
        assert MemoryRetriever._extract_focus_query(conv) == "那适合出去玩吗"

    def test_skips_short_and_tags(self) -> None:
        conv = [{"role": "user", "content": "[uid:123] 嗯"}]
        assert MemoryRetriever._extract_focus_query(conv) == ""

    def test_empty_conversation(self) -> None:
        assert MemoryRetriever._extract_focus_query([]) == ""


class TestMergeResults:
    def test_dedupes_by_id_keeps_higher_score(self) -> None:
        primary = [_result("a", 0.5), _result("b", 0.3)]
        secondary = [_result("a", 0.8), _result("c", 0.6)]
        merged = MemoryRetriever._merge_results(primary, secondary, limit=10)
        ids = [r.id for r in merged]
        assert ids == ["a", "c", "b"]
        assert merged[0].score == 0.8

    def test_limit_applied(self) -> None:
        primary = [_result(f"r{i}", 0.1 * i) for i in range(10)]
        merged = MemoryRetriever._merge_results(primary, [], limit=3)
        assert len(merged) == 3


class TestTimeReference:
    def test_detects_chinese_time_words(self) -> None:
        assert MemoryRetriever._detect_time_reference("我昨天说了什么")
        assert MemoryRetriever._detect_time_reference("上次讨论的话题")
        assert MemoryRetriever._detect_time_reference("最近怎么样")

    def test_no_time_reference(self) -> None:
        assert not MemoryRetriever._detect_time_reference("今天天气不错")


class TestTemporalBoost:
    def test_episodic_boosted(self) -> None:
        results = [
            _result("e1", 0.5, mtype=MemoryType.EPISODIC.value),
            _result("s1", 0.5, mtype=MemoryType.SEMANTIC.value),
        ]
        boosted = MemoryRetriever._apply_temporal_boost(results)
        episodic = next(r for r in boosted if r.id == "e1")
        semantic = next(r for r in boosted if r.id == "s1")
        assert episodic.score > semantic.score

    def test_recent_memory_boosted(self) -> None:
        now = time.time()
        results = [
            _result("new", 0.5, ts=now - 3600),           # 1 小时前
            _result("old", 0.5, ts=now - 30 * 86400),     # 30 天前
        ]
        boosted = MemoryRetriever._apply_temporal_boost(results)
        recent = next(r for r in boosted if r.id == "new")
        old = next(r for r in boosted if r.id == "old")
        assert recent.score > old.score

    def test_sorted_by_score(self) -> None:
        results = [_result("a", 0.3), _result("b", 0.9)]
        boosted = MemoryRetriever._apply_temporal_boost(results)
        assert boosted[0].score >= boosted[1].score
