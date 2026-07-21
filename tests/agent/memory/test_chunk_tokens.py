"""tiktoken 精确切块测试：token 上限、重叠保留、超长行、降级路径。"""

from __future__ import annotations

import tiktoken

from agent.memory import memory_sync
from agent.memory.memory_sync import chunk_markdown

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
    for prev, nxt in zip(chunks, chunks[1:]):
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
