"""有界原文片段选择，保留命中证据与字符位置。

模型可见：工具和召回结果中的原文窗口及省略标记。
Token：沿用调用方的字符预算，不生成摘要或额外模型请求。
缓存：无状态纯函数，不修改上下文前缀。
"""

from __future__ import annotations

import re
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class TextPassage:
    """带省略标记的片段；start/end 是原文中的半开字符区间。"""

    text: str
    start: int
    end: int


def passage_around(text: str, start: int, end: int, *, max_chars: int) -> TextPassage:
    """在预算内保留指定证据，优先完整行，超长证据明确截断。"""
    if max_chars < 3:
        raise ValueError("max_chars must be at least 3")
    if len(text) <= max_chars:
        return TextPassage(text, 0, len(text))
    width = max_chars - 2
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    before = max(0, (width - (end - start)) // 3)
    lo = max(0, min(start - before, len(text) - width))
    hi = min(len(text), lo + width)
    line_start = text.find("\n", lo, start)
    if line_start >= 0:
        lo = line_start + 1
    line_end = text.rfind("\n", max(lo, end), hi)
    if line_end >= end:
        hi = line_end
    excerpt = ("…" if lo else "") + text[lo:hi] + ("…" if hi < len(text) else "")
    return TextPassage(excerpt, lo, hi)


def select_passage(text: str, terms: Iterable[str], *, max_chars: int = 500) -> TextPassage:
    """优先覆盖不同查询词，平分时保留最早窗口；无字面命中保留开头。"""
    if len(text) <= max_chars:
        return passage_around(text, 0, len(text), max_chars=max_chars)
    words = sorted({term for term in terms if term}, key=lambda word: (-len(word), word))
    if not words:
        return passage_around(text, 0, 0, max_chars=max_chars)
    pattern = re.compile("|".join(re.escape(word) for word in words), re.IGNORECASE)
    window: deque[tuple[int, int, str]] = deque()
    counts: Counter[str] = Counter()
    best_count, start, end = 0, 0, 0
    for match in pattern.finditer(text):
        key = match.group().casefold()
        window.append((match.start(), match.end(), key))
        counts[key] += 1
        while len(window) > 1 and match.end() - window[0][0] > max_chars - 2:
            _, _, removed = window.popleft()
            counts[removed] -= 1
            if not counts[removed]:
                del counts[removed]
        if len(counts) > best_count:
            best_count, start, end = len(counts), window[0][0], match.end()
    return passage_around(text, start, end, max_chars=max_chars)
