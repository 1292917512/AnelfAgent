"""后台任务的有界操作证据，按游标查询，不注入稳定上下文。

模型可见：工具参数、结果摘要、错误和耗时；不复制思考正文。
Token 影响：仅按需返回最多 8K 字符；缓存影响：作为工具结果追加。
"""

from __future__ import annotations

import json
import time
from collections import deque
from typing import Any

from core.sanitizer import sanitize_text


class TaskEvidence:
    """保存最近操作的脱敏摘要，显式报告截断和游标过期。"""

    def __init__(self) -> None:
        self._sequence = 0
        self._entries: deque[dict[str, Any]] = deque(maxlen=32)

    def append(self, kind: str, **facts: Any) -> None:
        self._sequence += 1
        entry: dict[str, Any] = {"sequence": self._sequence, "kind": kind, "time": time.time()}
        for key, value in facts.items():
            if value is None:
                continue
            if key == "truncated":
                entry[key] = bool(value) or entry.get(key, False)
                continue
            if isinstance(value, (int, float, bool)):
                entry[key] = value
                continue
            text = sanitize_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str))
            entry[key] = text[:1200]
            if len(text) > 1200:
                entry["truncated"] = True
        self._entries.append(entry)

    def read(self, after_sequence: int = 0) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        used = 0
        cursor = max(0, after_sequence)
        for entry in self._entries:
            if entry["sequence"] <= cursor:
                continue
            size = len(json.dumps(entry, ensure_ascii=False))
            if entries and used + size > 8000:
                break
            entries.append(dict(entry))
            used += size
        return {
            "events": entries,
            "next_sequence": entries[-1]["sequence"] if entries else cursor,
            "has_more": bool(entries and entries[-1]["sequence"] < self._sequence),
            "history_evicted": bool(self._entries and cursor < self._entries[0]["sequence"] - 1),
        }
