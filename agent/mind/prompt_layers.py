"""Prompt 分层缓存（参考 hermes-agent system_prompt 三层架构）。

系统提示按变更频率分三层构建，保证 stable 层在对话内字节级不变，
从而命中 LLM 供应商的 Prompt Caching 前缀复用（Anthropic 缓存前缀 90% 折扣）：

- stable:   人设 + 工具使用规则 + 工具目录 + 媒体规则 + 模型摘要 + 静态指南
            （对话内不变；仅工具激活/人设变更/压缩时重建）
- context:  动态便签（当前状态/教导/规则等）+ 文件索引
- volatile: 短期记忆、语义召回、溢出提示、安全标记等每轮可变内容
            （始终放在 stable/context 之后，不破坏前缀缓存）

PromptCacheManager 按对话 scope 缓存 stable/context 层的构建结果，
以输入内容哈希校验一致性：哈希一致时返回冻结副本（保证字节稳定），
不一致时重建并记录统计。

FileLayerCache 为文件型层提供 mtime+size 的 O(1) 快检：
文件未变时跳过 I/O 和哈希计算，直接返回缓存内容。
"""
from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from core.log import log

# 层名常量
LAYER_STABLE = "stable"
LAYER_CONTEXT = "context"

# scope 缓存上限（LRU 淘汰）
_MAX_CACHED_SCOPES = 64


@dataclass
class _LayerEntry:
    """单层缓存条目。"""

    content_hash: str = ""
    content: str = ""


@dataclass
class _ScopeCache:
    """单个对话 scope 的分层缓存。"""

    layers: Dict[str, _LayerEntry] = field(default_factory=dict)


class PromptCacheManager:
    """Prompt 分层缓存管理器（LRU 上限防内存泄漏）。"""

    def __init__(self) -> None:
        self._scopes: OrderedDict[str, _ScopeCache] = OrderedDict()
        # 统计
        self.hits: int = 0
        self.misses: int = 0
        self.invalidations: int = 0

    @staticmethod
    def compute_hash(*parts: str) -> str:
        """计算输入内容哈希（任一输入变化即触发重建）。"""
        h = hashlib.sha256()
        for part in parts:
            h.update(part.encode("utf-8", errors="replace"))
            h.update(b"\x00")
        return h.hexdigest()[:16]

    def get_or_build(
            self,
            scope: str,
            layer: str,
            content_hash: str,
            builder: Callable[[], str],
    ) -> Tuple[str, bool]:
        """获取缓存层内容或重建。

        Returns:
            (内容, 是否命中缓存)。哈希一致时返回冻结副本，保证字节级稳定。
        """
        if not is_prompt_cache_enabled():
            return builder(), False

        cache = self._scopes.get(scope)
        if cache is None:
            cache = _ScopeCache()
            self._scopes[scope] = cache
            self._evict_if_needed()
        else:
            # LRU: 访问时移到末尾
            self._scopes.move_to_end(scope)

        entry = cache.layers.get(layer)
        if entry is not None and entry.content_hash == content_hash:
            self.hits += 1
            return entry.content, True

        content = builder()
        cache.layers[layer] = _LayerEntry(content_hash=content_hash, content=content)
        self.misses += 1
        return content, False

    def invalidate(self, scope: str = "", layer: Optional[str] = None) -> None:
        """使缓存失效。

        Args:
            scope: 对话 scope（空串表示全部）
            layer: 指定层名（None 表示该 scope 的全部层）
        """
        self.invalidations += 1
        if not scope:
            self._scopes.clear()
            return
        if layer is None:
            self._scopes.pop(scope, None)
        else:
            cache = self._scopes.get(scope)
            if cache:
                cache.layers.pop(layer, None)

    def stats(self) -> Dict[str, int]:
        """缓存统计（命中/未命中/失效次数）。"""
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "invalidations": self.invalidations,
            "hit_rate": round(self.hits / total, 3) if total else 0,
            "cached_scopes": len(self._scopes),
        }

    def _evict_if_needed(self) -> None:
        """超出上限时淘汰最久未访问的 scope。"""
        while len(self._scopes) > _MAX_CACHED_SCOPES:
            self._scopes.popitem(last=False)


# 全局单例
prompt_cache_manager = PromptCacheManager()


# ------------------------------------------------------------------
# 文件型层 mtime 快检缓存
# ------------------------------------------------------------------


@dataclass
class _FileCacheEntry:
    """文件缓存条目：mtime_ns + size 做 O(1) 变更检测。"""

    mtime_ns: int = 0
    size: int = -1  # -1 表示文件不存在
    content: str = ""


class FileLayerCache:
    """基于文件 mtime+size 的 O(1) 快检缓存。

    文件未变时跳过 I/O，直接返回缓存内容；
    文件变化或首次访问时调用 loader 重新构建。
    """

    def __init__(self) -> None:
        self._entries: Dict[str, _FileCacheEntry] = {}

    def get_or_load(self, path: Path, loader: Callable[[], str]) -> Tuple[str, bool]:
        """获取文件型层内容，mtime+size 未变时直接命中。

        Args:
            path: 用于 stat 检测的文件/目录路径。
            loader: 文件变化时调用的构建函数。

        Returns:
            (内容, 是否命中缓存)。
        """
        key = str(path)
        try:
            st = os.stat(path)
            mtime_ns, size = st.st_mtime_ns, st.st_size
        except OSError:
            mtime_ns, size = 0, -1

        entry = self._entries.get(key)
        if entry is not None and entry.mtime_ns == mtime_ns and entry.size == size:
            return entry.content, True

        content = loader()
        self._entries[key] = _FileCacheEntry(mtime_ns=mtime_ns, size=size, content=content)
        return content, False

    def invalidate(self, path: Optional[Path] = None) -> None:
        """使指定路径（或全部）的缓存失效。"""
        if path is None:
            self._entries.clear()
        else:
            self._entries.pop(str(path), None)


def is_prompt_cache_enabled() -> bool:
    """Prompt 分层缓存总开关。"""
    from core.config import get_config_bool
    return get_config_bool("prompt_cache_enabled", True)


def is_anthropic_breakpoint_enabled() -> bool:
    """Anthropic cache_control 断点注入开关。"""
    from core.config import get_config_bool
    return get_config_bool("prompt_cache_anthropic_breakpoint", True)


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_PROMPT_CACHE_CONFIGS = {
    "Prompt缓存": {
        "prompt_cache_enabled": {
            "description": "是否启用 Prompt 分层缓存（stable 层对话内冻结复用）",
            "default": True,
        },
        "prompt_cache_anthropic_breakpoint": {
            "description": "是否为 Anthropic 模型注入 cache_control 缓存断点",
            "default": True,
        },
        "memory_inject_max_chars": {
            "description": "memory 层（语义召回+画像+技能）注入的总字符预算上限",
            "default": 6000,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_PROMPT_CACHE_CONFIGS)
