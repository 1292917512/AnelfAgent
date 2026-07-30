"""记忆召回单元测试：多窗口召回增强 + tiktoken 精确切块。"""

from __future__ import annotations

import time

import tiktoken

from agent.memory import memory_sync
from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_sync import chunk_markdown
from agent.memory.memory_types import MemorySearchResult, MemoryType

# ==================================================================
# 多窗口召回增强（memory_retriever）
# ==================================================================

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


# ==================================================================
# tiktoken 精确切块：token 上限、重叠保留、超长行、降级路径
# ==================================================================

_enc = tiktoken.get_encoding("cl100k_base")


def _tokens(text: str) -> int:
    return len(_enc.encode(text))


def test_chunks_respect_token_limit_english() -> None:
    content = "\n".join(f"line {i}: " + "word " * 30 for i in range(50))
    chunks = chunk_markdown(content, chunk_tokens=100, overlap_tokens=20)
    assert len(chunks) > 1
    for ch in chunks:
        assert _tokens(ch["text"]) <= 100


def test_chunks_respect_token_limit_chinese() -> None:
    # 中文在 cl100k 下约 1 字 1 token，字符估算会严重超发
    content = "\n".join("这是一段中文测试文本，用于验证切块的准确性。" * 3 for _ in range(40))
    chunks = chunk_markdown(content, chunk_tokens=100, overlap_tokens=20)
    assert len(chunks) > 1
    for ch in chunks:
        assert _tokens(ch["text"]) <= 100


def test_overlap_lines_carried_to_next_chunk() -> None:
    lines = [f"unique-marker-{i} " + "x " * 20 for i in range(30)]
    chunks = chunk_markdown("\n".join(lines), chunk_tokens=80, overlap_tokens=30)
    assert len(chunks) > 1
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        prev_lines = prev["text"].split("\n")
        nxt_first = nxt["text"].split("\n")[0]
        # 下一块开头来自上一块尾部（重叠保留）
        assert nxt_first in prev_lines
        # 行号区间存在交叠
        assert nxt["start_line"] <= prev["end_line"]


def test_single_long_line_not_hard_split() -> None:
    long_line = "word " * 500  # 远超 chunk_tokens
    content = f"short\n{long_line}\ntail"
    chunks = chunk_markdown(content, chunk_tokens=64, overlap_tokens=8)
    texts = [ch["text"] for ch in chunks]
    assert any(long_line.strip() in t for t in texts)
    # 长行独占一块，前后短行在各自块中
    assert any(t.startswith("short") for t in texts)


def test_empty_and_small_content() -> None:
    # 空内容按既有语义产出单个空 chunk（"".split 为 [""]），由 sync 层决定是否跳过
    chunks = chunk_markdown("")
    assert len(chunks) == 1 and chunks[0]["text"] == ""
    chunks = chunk_markdown("hello world")
    assert len(chunks) == 1
    assert chunks[0]["start_line"] == 1 and chunks[0]["end_line"] == 1
    assert len(chunks[0]["hash"]) == 64


def test_overlap_clamped_below_chunk_size() -> None:
    # overlap >= chunk_tokens 时必须收敛，不得死循环
    content = "\n".join("word " * 10 for _ in range(20))
    chunks = chunk_markdown(content, chunk_tokens=32, overlap_tokens=1000)
    assert len(chunks) >= 1
    for ch in chunks:
        assert _tokens(ch["text"]) <= 32


def test_char_estimation_fallback(monkeypatch) -> None:
    monkeypatch.setattr(memory_sync, "_get_encoder", lambda: None)
    content = "\n".join("abcde " * 5 for _ in range(30))  # 每行 30 字符 ≈ 11 token
    chunks = chunk_markdown(content, chunk_tokens=60, overlap_tokens=12)
    assert len(chunks) > 1
    # 降级路径：1 token ≈ 3 字符
    for ch in chunks:
        assert len(ch["text"]) <= 60 * 3 + 6
