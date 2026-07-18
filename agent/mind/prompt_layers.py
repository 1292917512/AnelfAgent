"""Prompt 分层缓存（参考 hermes-agent system_prompt 三层架构）。

系统提示按变更频率分三层构建，保证 stable 层在对话内字节级不变，
从而命中 LLM 供应商的 Prompt Caching 前缀复用（Anthropic 缓存前缀 90% 折扣）：

- stable:   人设 + 工具使用规则 + 工具目录 + 媒体规则 + 模型摘要
            （对话内不变；仅工具激活/人设变更/压缩时重建）
- context:  便签（memory.md）等低频变更内容
- volatile: 短期记忆、语义召回、溢出提示、安全标记等每轮可变内容
            （始终放在 stable/context 之后，不破坏前缀缓存）

PromptCacheManager 按对话 scope 缓存 stable/context 层的构建结果，
以输入内容哈希校验一致性：哈希一致时返回冻结副本（保证字节稳定），
不一致时重建并记录统计。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from core.log import log

# 层名常量
LAYER_STABLE = "stable"
LAYER_CONTEXT = "context"


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
    """Prompt 分层缓存管理器。"""

    def __init__(self) -> None:
        self._scopes: Dict[str, _ScopeCache] = {}
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

        cache = self._scopes.setdefault(scope, _ScopeCache())
        entry = cache.layers.get(layer)
        if entry is not None and entry.content_hash == content_hash:
            self.hits += 1
            log(f"Prompt 层缓存命中: [{layer}] scope={scope}", "DEBUG", tag="缓存")
            return entry.content, True

        content = builder()
        cache.layers[layer] = _LayerEntry(content_hash=content_hash, content=content)
        self.misses += 1
        log(f"Prompt 层缓存重建: [{layer}] scope={scope}", "DEBUG", tag="缓存")
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
            log("Prompt 层缓存全部失效", "DEBUG", tag="缓存")
            return
        if layer is None:
            self._scopes.pop(scope, None)
        else:
            cache = self._scopes.get(scope)
            if cache:
                cache.layers.pop(layer, None)
        log(f"Prompt 层缓存失效: scope={scope} layer={layer or '全部'}", "DEBUG", tag="缓存")

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


# 全局单例
prompt_cache_manager = PromptCacheManager()


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
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_PROMPT_CACHE_CONFIGS)
