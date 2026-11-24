"""按当前问题从记忆与规划材料中选择原文。

模型看到匹配位置附近的原文。调用方指定字符预算，选段不调用模型。
该查询只在动态材料的渲染期间使用，不写入缓存前缀。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from core.sanitizer import sanitize_for_context, truncate_middle
from core.tags import strip_functional_tags, strip_message_meta_tags
from core.text_passage import select_matching_passage


@dataclass(frozen=True, slots=True)
class PassageQuery:
    """排名与摘录使用同一组完整查询词和相同匹配边界。"""

    pattern: re.Pattern[str] | None

    @classmethod
    def from_text(cls, text: str) -> PassageQuery:
        clean = strip_functional_tags(strip_message_meta_tags(text)).strip()
        if not clean:
            return cls(None)
        import jieba

        focus = truncate_middle(clean, 1000, marker=" ")
        words = {
            word for word in jieba.cut(focus)
            if (len(word) > 1 or word == focus) and any(char.isalnum() for char in word)
        }
        patterns = [
            rf"(?<![a-zA-Z0-9]){re.escape(word)}(?![a-zA-Z0-9])" if word.isascii() else re.escape(word)
            for word in sorted(words, key=lambda word: (-len(word), word))
        ]
        return cls(re.compile("|".join(patterns), re.IGNORECASE) if patterns else None)

    def _matches(self, text: str) -> Iterator[re.Match[str]]:
        return self.pattern.finditer(text) if self.pattern else iter(())

    def score(self, text: str) -> int:
        return len({match[0].casefold() for match in self._matches(text)})

    def excerpt(self, text: str, limit: int) -> str:
        clean = sanitize_for_context(text, max_chars=len(text))
        return select_matching_passage(clean, self._matches(clean), max_chars=limit).text
