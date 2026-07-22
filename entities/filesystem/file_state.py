"""文件读取状态缓存 — read-before-write 与过期检查的基础设施。

移植自 Claude Code ``src/utils/fileStateCache.ts`` 与 FileEditTool 的校验语义：
- 写入类工具（edit_file/write_file）要求目标文件在本 scope 内被完整读取过
- mtime 晚于读取时间且内容不一致 → 判定为"读取后被外部修改"，拒绝写入
- mtime 变化但内容逐字节一致（云同步/杀软触碰）→ 刷新时间戳后放行

缓存按对话 scope 隔离（多用户/群聊互不串扰），scope 经 ``entities._sdk``
桥接从思维会话的 contextvars 解析，会话外落入 "_global" 桶。
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from entities._sdk import get_current_scope

# 单 scope 最大缓存文件数（对齐 Claude Code READ_FILE_STATE_CACHE_SIZE）
MAX_ENTRIES = 100
# 单 scope 缓存内容总字节上限（对齐 Claude Code 25MB）
MAX_CACHE_BYTES = 25 * 1024 * 1024


@dataclass
class FileState:
    """一次文件读取的快照。"""

    content: str
    mtime: float
    timestamp: float
    offset: Optional[int] = None
    limit: Optional[int] = None
    # 部分读取（offset/limit）不授权写入
    is_partial_view: bool = False


class FileStateCache:
    """LRU 文件状态缓存，键为规范化绝对路径。"""

    def __init__(self, max_entries: int = MAX_ENTRIES, max_bytes: int = MAX_CACHE_BYTES) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._items: "OrderedDict[str, FileState]" = OrderedDict()
        self._total_bytes = 0

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normpath(path)

    def get(self, path: str) -> Optional[FileState]:
        key = self._key(path)
        state = self._items.get(key)
        if state is not None:
            self._items.move_to_end(key)
        return state

    def set(self, path: str, state: FileState) -> None:
        key = self._key(path)
        old = self._items.pop(key, None)
        if old is not None:
            self._total_bytes -= len(old.content)
        self._items[key] = state
        self._total_bytes += len(state.content)
        # 先按条数、再按字节驱逐最久未用条目
        while len(self._items) > self._max_entries or (
                self._total_bytes > self._max_bytes and self._items):
            _, evicted = self._items.popitem(last=False)
            self._total_bytes -= len(evicted.content)

    def delete(self, path: str) -> None:
        key = self._key(path)
        old = self._items.pop(key, None)
        if old is not None:
            self._total_bytes -= len(old.content)

    def clear(self) -> None:
        self._items.clear()
        self._total_bytes = 0


_caches: Dict[str, FileStateCache] = {}
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
    """清空指定 scope 的缓存（压缩后强制重新读取，对齐 CC compact 语义）。"""
    with _caches_lock:
        cache = _caches.pop(scope, None)
    if cache is not None:
        cache.clear()


def record_read(path: str, content: str, mtime: float,
                offset: Optional[int] = None, limit: Optional[int] = None,
                scope: str = "") -> None:
    """记录一次读取。offset/limit 任一非空即视为部分读取。"""
    is_partial = offset is not None or limit is not None
    state = FileState(
        content=content,
        mtime=mtime,
        timestamp=mtime,
        offset=offset,
        limit=limit,
        is_partial_view=is_partial,
    )
    get_cache(scope).set(path, state)


def record_write(path: str, content: str, mtime: float, scope: str = "") -> None:
    """写入成功后刷新缓存，后续编辑无需重新读取。"""
    state = FileState(content=content, mtime=mtime, timestamp=mtime)
    get_cache(scope).set(path, state)


def check_writable(path: str, scope: str = "") -> Tuple[bool, str]:
    """校验目标文件是否允许写入。

    Returns:
        (True, "") 允许写入；(False, 错误消息) 拒绝并附可操作提示。
    """
    state = get_cache(scope).get(path)
    if state is None:
        return False, "文件尚未读取过。请先用 read_file 读取该文件，再进行修改。"
    if state.is_partial_view:
        return False, "文件只被部分读取过（offset/limit）。请先完整读取该文件，再进行修改。"

    try:
        current_mtime = os.path.getmtime(path)
    except OSError:
        # 文件在读取后被删除：按新建文件放行
        return True, ""

    if current_mtime > state.mtime:
        # mtime 变晚：内容逐字节一致则视为误报（云同步/杀软触碰），刷新后放行
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                current_content = f.read().replace("\r\n", "\n")
        except OSError:
            return True, ""
        if current_content == state.content:
            state.mtime = current_mtime
            state.timestamp = current_mtime
            return True, ""
        return False, (
            "文件在读取后已被修改（可能被用户或其他程序改动）。"
            "请重新用 read_file 读取最新内容后再修改。"
        )
    return True, ""
