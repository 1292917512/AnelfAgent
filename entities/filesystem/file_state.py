"""按会话记录文件正文与已展示行范围，提供完整读取和内容冲突检查。

同一正文的分页读取合并覆盖范围；内容变化后重新累计。写入者传入本次实际
读到的规范化正文进行比较，不依赖 mtime，也不把 Shell 操作视为读取凭据。
缓存只持有写入基线和现场恢复索引，不决定模型是否仍能看到旧工具结果。
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass

from entities._sdk import get_current_scope

MAX_ENTRIES = 100
MAX_CACHE_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class FileState:
    """规范化正文与已展示的闭区间行范围；相邻区间合并。"""

    content: str
    read_ranges: tuple[tuple[int, int], ...]

    @property
    def is_complete(self) -> bool:
        return self.read_ranges == ((1, self.content.count("\n") + 1),)


class FileStateCache:
    """线程安全的 LRU 读取基线，键为规范化绝对路径。"""

    def __init__(self, max_entries: int = MAX_ENTRIES, max_bytes: int = MAX_CACHE_BYTES) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._items: OrderedDict[str, FileState] = OrderedDict()
        self._total_bytes = 0
        self._lock = threading.RLock()

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    def get(self, path: str) -> FileState | None:
        with self._lock:
            key = self._key(path)
            state = self._items.get(key)
            if state is not None:
                self._items.move_to_end(key)
            return state

    def set(self, path: str, state: FileState) -> None:
        with self._lock:
            key = self._key(path)
            old = self._items.pop(key, None)
            if old is not None:
                self._total_bytes -= len(old.content.encode("utf-8"))
            self._items[key] = state
            self._total_bytes += len(state.content.encode("utf-8"))
            while self._items and (
                len(self._items) > self._max_entries or self._total_bytes > self._max_bytes
            ):
                _, evicted = self._items.popitem(last=False)
                self._total_bytes -= len(evicted.content.encode("utf-8"))

    def record_read(self, path: str, content: str, read_ranges: tuple[tuple[int, int], ...]) -> None:
        """原子合并同一正文的实际展示范围，内容变化时丢弃旧范围。"""
        total_lines = content.count("\n") + 1
        if not read_ranges:
            return
        if any(not 1 <= start <= end <= total_lines for start, end in read_ranges):
            raise ValueError("读取范围超出文件正文")
        with self._lock:
            previous = self.get(path)
            ranges = previous.read_ranges if previous is not None and previous.content == content else ()
            merged: list[tuple[int, int]] = []
            for start, end in sorted((*ranges, *read_ranges)):
                if merged and start <= merged[-1][1] + 1:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            self.set(path, FileState(content, tuple(merged)))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._total_bytes = 0

    def recent_entries(self, n: int) -> list[tuple[str, FileState]]:
        """最近使用的 n 条（新→旧），供压缩后现场恢复使用。"""
        with self._lock:
            return list(reversed(list(self._items.items())[-n:]))


_caches: dict[str, FileStateCache] = {}
_caches_lock = threading.Lock()


def get_cache(scope: str = "") -> FileStateCache:
    """获取指定（或当前）scope 的文件状态缓存。"""
    scope = scope or get_current_scope()
    with _caches_lock:
        cache = _caches.get(scope)
        if cache is None:
            cache = FileStateCache()
            _caches[scope] = cache
        return cache


def clear_scope(scope: str) -> None:
    """清除指定会话的读取基线。"""
    with _caches_lock:
        cache = _caches.pop(scope, None)
    if cache is not None:
        cache.clear()


def record_read(path: str, content: str, *, read_ranges: tuple[tuple[int, int], ...], scope: str = "") -> None:
    """登记本次实际展示的完整行范围，不根据调用参数推测完整性。"""
    get_cache(scope).record_read(path, content, read_ranges)


def record_write(path: str, content: str, scope: str = "") -> None:
    """写入成功后刷新完整基线，后续编辑无需重新读取。"""
    content = content.replace("\r\n", "\n")
    get_cache(scope).set(path, FileState(content, ((1, content.count("\n") + 1),)))


def check_writable(path: str, content: str, scope: str = "") -> tuple[bool, str]:
    """校验写入者实际读到的正文与完整读取基线一致。"""
    state = get_cache(scope).get(path)
    if state is None:
        return False, "文件尚未读取过。请先用 read_file 完整读取；Shell 读取或创建不会建立读取基线。"
    if state.content != content:
        return False, (
            "文件在读取后已被修改（包括通过 Shell 修改）。"
            "请用 read_file 重新完整读取当前内容，再根据最新内容修改。"
        )
    if not state.is_complete:
        next_line = 1
        for start, end in state.read_ranges:
            if start > next_line:
                break
            next_line = end + 1
        return False, (
            f"文件只被部分读取过。请用 read_file(offset={next_line}) 从首个未读行继续读完当前内容；"
            "已读页面会累计；若结果被截断，请减小 limit 继续读取原文件。"
        )
    return True, ""
