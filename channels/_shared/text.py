"""频道文本分段 — 按最大长度分段（优先换行边界），超限段数截断并追加省略标记。"""

from __future__ import annotations

from typing import List


def split_text(text: str, max_length: int, *, max_chunks: int = 5) -> List[str]:
    """把长文本拆成不超过 max_chunks 段；末段超限时截断并加省略号。"""
    limit = max(max_length, 50)
    chunks: List[str] = []
    remaining = text
    while len(remaining) > limit and len(chunks) < max_chunks - 1:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    chunks.append(remaining if len(remaining) <= limit else remaining[: limit - 3] + "...")
    return [c for c in chunks if c]
