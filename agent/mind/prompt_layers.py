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

# 层名常量
LAYER_STABLE = "stable"
LAYER_STABLE_PERSONA = "stable_persona"
LAYER_STABLE_TOOLS = "stable_tools"
LAYER_CONTEXT = "context"

# 缓存条目上限（LRU 淘汰）
_MAX_CACHED_ENTRIES = 256


@dataclass
class _LayerEntry:
    """单层缓存条目（内容寻址：同一 (layer, hash) 全局面唯一副本）。"""

    content_hash: str = ""
    content: str = ""
    # 引用本条目的 scope 集合（按 scope 失效与观测用）
    scopes: set = field(default_factory=set)


class PromptCacheManager:
    """Prompt 分层缓存管理器（内容寻址 + LRU 上限防内存泄漏）。

    缓存键为 (layer, content_hash) 全局唯一：不同对话 scope 的相同层内容
    （人设/工具目录等高度同质）只存一份，避免按 scope 复制 N 份副本。
    """

    def __init__(self) -> None:
        self._entries: "OrderedDict[tuple[str, str], _LayerEntry]" = OrderedDict()
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

        key = (layer, content_hash)
        entry = self._entries.get(key)
        if entry is not None:
            # LRU: 访问时移到末尾
            self._entries.move_to_end(key)
            entry.scopes.add(scope)
            self.hits += 1
            return entry.content, True

        content = builder()
        self._entries[key] = _LayerEntry(
            content_hash=content_hash, content=content, scopes={scope},
        )
        self._evict_if_needed()
        self.misses += 1
        return content, False

    def invalidate(self, scope: str = "", layer: Optional[str] = None) -> None:
        """使缓存失效。

        Args:
            scope: 对话 scope（空串表示全部）；内容寻址下仅删除该 scope 引用的条目
            layer: 指定层名（None 表示全部层）
        """
        self.invalidations += 1
        if not scope:
            if layer is None:
                self._entries.clear()
            else:
                for key in [k for k in self._entries if k[0] == layer]:
                    self._entries.pop(key, None)
            return
        for key in [k for k, e in self._entries.items()
                    if scope in e.scopes and (layer is None or k[0] == layer)]:
            self._entries.pop(key, None)

    def stats(self) -> Dict[str, int]:
        """缓存统计（命中/未命中/失效次数）。"""
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "invalidations": self.invalidations,
            "hit_rate": round(self.hits / total, 3) if total else 0,
            "cached_entries": len(self._entries),
        }

    def _evict_if_needed(self) -> None:
        """超出上限时淘汰最久未访问的条目。"""
        while len(self._entries) > _MAX_CACHED_ENTRIES:
            self._entries.popitem(last=False)


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

        缓存键包含 loader 标识：同一路径可被不同 loader 构建出不同内容
        （如 stable guide 与 dynamic notes 共用 notes 目录），必须分别缓存。

        Args:
            path: 用于 stat 检测的文件/目录路径。
            loader: 文件变化时调用的构建函数。

        Returns:
            (内容, 是否命中缓存)。
        """
        key = f"{path}::{getattr(loader, '__qualname__', repr(loader))}"
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
            prefix = f"{path}::"
            for key in [k for k in self._entries if k.startswith(prefix)]:
                self._entries.pop(key, None)


def is_prompt_cache_enabled() -> bool:
    """Prompt 分层缓存总开关。"""
    from core.config import get_config_bool
    return get_config_bool("prompt_cache_enabled", True)


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

from agent.llm.reasoning import CANONICAL_EFFORTS  # noqa: E402
from core.config import ConfigValueType, register_configs_safe  # noqa: E402

_PROMPT_CACHE_CONFIGS = {
    "cache/prompt": {
        "max_conversation_size": {
            "description": "对话历史窗口上限（原始窗口最大条数，到达后触发摘要折叠）",
            "default": 30,
            "unit": "条",
        },
        "prompt_cache_enabled": {
            "description": "是否启用 Prompt 分层缓存（stable 层对话内冻结复用）",
            "default": True,
        },
        "prompt_cache_anthropic_breakpoint": {
            "description": "是否为 Anthropic 模型注入 cache_control 缓存断点",
            "default": True,
        },
        "prompt_cache_summary_breakpoint": {
            "description": "是否在对话摘要块上注入缓存断点（摘要块在折叠周期内字节固定，是历史前缀的缓存锚点）",
            "default": True,
        },
        "prompt_cache_tools_breakpoint": {
            "description": "是否在 wire tools 数组末尾注入缓存断点（整个工具 schema 前缀进缓存；消息侧断点满 4 个时自动让位，仅 Anthropic 线生效）",
            "default": True,
        },
        "prompt_cache_anthropic_ttl": {
            "description": "Anthropic 缓存断点 TTL：5m（默认）或 1h（写入费 2x，部分网关不支持，被拒时回退 5m）",
            "default": "5m",
        },
        "anthropic_cache_pool_size": {
            "description": "Anthropic 缓存亲和连接池大小（池小命中稳，超出排队；修改后需重建模型客户端生效）",
            "default": 4,
            "advanced": True,
            "unit": "个",
        },
        "context_tail_injection_enabled": {
            "description": "是否将画像/召回/技能/短期记忆等每会话重建内容移到对话历史之后注入（尾部动态区），使历史进入缓存前缀；关闭恢复旧布局",
            "default": True,
        },
        "conversation_summary_enabled": {
            "description": "是否启用对话摘要窗口（旧消息折叠为固定摘要块 + 最近消息纯追加，保持历史前缀字节稳定以命中缓存）",
            "default": True,
        },
        "conversation_raw_keep_percent": {
            "description": "折叠后保留原文的比例（%）：窗口积满后最近该比例保留原文，更早的并入摘要块",
            "default": 33,
        },
        "conversation_summary_max_chars": {
            "description": "对话摘要块的字符上限",
            "default": 4000,
            "advanced": True,
            "unit": "字符",
        },
        "conversation_fold_drop_on_failure": {
            "description": "折叠失败时是否丢弃该批并推进水位线（缓存前缀稳定，内容仍在 DB 可检索）；关闭则保持滑动直到折叠成功",
            "default": True,
        },
        "conversation_fold_batch_max": {
            "description": "单次折叠批量上限：积压恢复时分批消化（摘要提示词有界、单批失败最多丢这批）；日常批量等于总窗口条数，通常远低于此上限",
            "default": 100,
            "advanced": True,
            "unit": "条",
        },
        "conversation_fold_idle_beats": {
            "description": "空闲自动折叠：某会话连续 N 个心跳无外部新消息（任务/系统写入不计）时，把窗口内消息折进摘要（把缓存断点移到无人时段）。积压阈值随窗口参数自动联动，无需配置",
            "default": 6,
            "advanced": True,
            "unit": "次",
        },
        "conversation_fold_prewarm": {
            "description": "折叠完成后主动预热：用新前缀发一次 1-token 轻调用把缓存写热，消除折叠后的首轮命中低谷（成本 = 下一轮真实调用本应付的全价预读，净零额外开销）",
            "default": True,
        },
        "conversation_summary_model": {
            "description": "折叠/压缩摘要专用模型 ID：为内部摘要任务指定更轻量的已配置模型（失败仍走默认回退链）；空 = 默认主模型",
            "default": "",
        },
        "conversation_summary_reasoning_effort": {
            "description": "摘要专用思考等级：折叠/压缩摘要通常无需深度思考，可指定低档省时省 token（模型不支持思考时自动忽略）；空 = 跟随模型自身配置",
            "default": "",
            "value_type": ConfigValueType.ENUM,
            "options": ["", *CANONICAL_EFFORTS],
        },
        "conversation_summary_llm_timeout": {
            "description": "摘要调用总时长护栏：纯兜底防折叠 scope 锁悬挂（流式通道按空闲判定，思考/输出中不计时，正常远不会触及）",
            "default": 900,
            "advanced": True,
            "min": 60,
            "max": 7200,
            "unit": "秒",
        },
        "task_lean_context": {
            "description": "任务/反思调用使用精简上下文（人设+工具+永久记忆+任务指令）：环境便签/召回/状态对批处理任务是冗余（任务规则要求用 recall/get_conversation 按需取回），且任务每轮都会写便签使其漂移——带上既撑大每轮 prompt 又让下次任务首轮缓存从便签处断裂；关闭恢复完整环境注入",
            "default": True,
        },
        "memory_inject_max_chars": {
            "description": "memory 层（语义召回+画像+技能）注入的总字符预算上限",
            "default": 6000,
            "advanced": True,
            "unit": "字符",
        },
        "tool_order_deterministic": {
            "description": "工具排序确定性模式：与使用计数无关，同一工具集跨会话字节级一致（tools schema 是 prompt 最大头，其稳定性决定前缀缓存命中率上限）；关闭恢复「已使用优先」排序",
            "default": True,
        },
        "tool_order_frozen": {
            "description": "跨回复追加式冻结 tools 数组顺序：首轮建立顺序后新工具只追加尾部、来源成员变化（热召回换血等）不剔除——回复间前缀字节稳定；关闭则每回复按双桶排序键重排（需确定性模式开启）",
            "default": True,
        },
        "tool_dynamic_sticky": {
            "description": "动态工具粘性模式：tag 激活/动态发现的工具在空闲时保留而非清除（进程内工具集只增不减），避免工具集跨会话抖动击穿前缀缓存；关闭则每个会话结束清空",
            "default": True,
        },
        "prefix_guard_layers": {
            "description": "前缀稳定性守卫（PrefixGuard）校验的层清单（逗号分隔）；默认稳定前缀三层 stable,summary,conversation。守卫在每次 LLM 调用前逐条哈希比对同 scope 上一次调用，首个不一致位置即缓存断裂点，归因落盘 records.jsonl 的 prefix_drift 字段（仅观测不阻断）",
            "default": "",
            "advanced": True,
        },
    },
}

register_configs_safe(_PROMPT_CACHE_CONFIGS)
