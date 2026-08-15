"""前缀稳定性运行时守卫 — 分层哈希链校验与缓存断裂归因。

设计动机（对齐 dsh agent-loop/invariant.ts 的"运行时不变式"思想，适配为
轻量观测版）：AnelfAgent 的前缀字节稳定性由声明式变动率分层 + 纪律维护，
缺少一个在发送边界独立校验"前缀是否真的没变"的机制。本模块补上这一环——
在每次 LLM 调用前对前缀层消息逐条计算哈希，与同 scope 上一次调用的哈希链
比对，首个不一致的位置即缓存断裂点。

与 context_snapshot._categorize 的区别：那是层聚合 sha1（整层一个哈希，
只能定位到层），本模块是逐消息前缀链（能定位到层内第几条消息变了）。

架构纪律：
- **仅观测不阻断**（fail-open）：check 只返回归因 dict，绝不修改 messages、
  不抛异常、不拦截请求。任何内部异常静默吞掉返回 None。
- 哈希复用 PromptCacheManager.compute_hash（sha256[:16]），与分层缓存同口径。
- 守卫的层由 prefix_guard_layers 配置驱动，默认稳定前缀三层
  （stable/summary/conversation）；conversation 随对话纯追加，比对逻辑
  对"追加"免疫（只报既有位置的改动），故纳入守卫可覆盖历史层漂移。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from core.config import get_config
from core.log import log

# 默认守卫层：字节稳定前缀（stable/summary）+ 纯追加历史（conversation）。
# 与 prompt_cache.CACHEABLE_PREFIX_LAYERS 的关系：后者是"理论可命中前缀"
# （快照 expected_prefix_tokens 口径），本守卫层是其超集（多 conversation，
# 用于捕捉历史层被意外改写）。
_DEFAULT_GUARDED_LAYERS = ("stable", "summary", "conversation")


def _guarded_layers() -> Tuple[str, ...]:
    """守卫层清单（逗号分隔配置，缺省默认三层）。"""
    raw = get_config("prefix_guard_layers", "") or ""
    layers = tuple(x.strip() for x in raw.split(",") if x.strip())
    return layers or _DEFAULT_GUARDED_LAYERS


def _msg_fingerprint(msg: Dict) -> str:
    """单条消息的规范化指纹串（role + 内容 + 工具配对键）。

    content 可能为 str 或 block 列表（视觉图片），统一 json 序列化保证
    结构变化可被哈希捕捉；extra 字段（_layer 之外的提供商扩展）一并纳入，
    避免 cache_control 等字段的意外漂移逃过校验。
    """
    content = msg.get("content")
    if not isinstance(content, str):
        try:
            content = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            content = str(content)
    # _source 为系统注入来源标记（发送前剥离的元数据），不纳入指纹——
    # 其出现/消失不改变发往 LLM 的字节，不应制造前缀断裂误报
    extra = {k: v for k, v in msg.items() if k not in ("role", "content", "_layer", "_source")}
    try:
        extra_str = json.dumps(extra, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        extra_str = str(sorted(extra.items()))
    return f"{msg.get('role', '')}\x00{content}\x00{extra_str}"


class PrefixGuard:
    """前缀稳定性守卫（单例）：维护 per-scope 哈希链并检测断裂。

    线程安全说明：本项目单事件循环运行，check/update 均在事件循环内调用，
    无需加锁；哈希链按 scope 隔离，跨频道互不干扰。
    """

    def __init__(self) -> None:
        # (scope, kind) → 上一次调用的前缀层哈希链 [(layer, msg_hash), ...]
        # 按调用用途分键：reply/reflect/compress 各自前缀族独立，
        # 避免主对话与辅助调用交替时跨族误报断裂
        self._chains: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
        # 累计断裂次数（供可观测性）
        self.drift_count: int = 0
        self.check_count: int = 0

    @staticmethod
    def _build_chain(messages: List[Dict], layers: Tuple[str, ...]) -> List[Tuple[str, str]]:
        """按消息顺序提取守卫层消息的 (layer, hash) 链。"""
        from agent.mind.prompt_layers import PromptCacheManager
        layer_set = set(layers)
        chain: List[Tuple[str, str]] = []
        for msg in messages:
            layer = msg.get("_layer")
            if layer not in layer_set:
                continue
            msg_hash = PromptCacheManager.compute_hash(_msg_fingerprint(msg))
            chain.append((layer, msg_hash))
        return chain

    def check(self, scope: str, messages: List[Dict], kind: str = "reply") -> Optional[Dict[str, Any]]:
        """校验前缀稳定性并更新基线，返回首个断裂归因（稳定则 None）。

        kind 为调用用途（reply/reflect/compress…），与 scope 共同构成基线键，
        隔离不同前缀族。fail-open：任何异常静默返回 None，绝不影响调用流程。
        """
        try:
            return self._check_impl(scope, messages, kind)
        except Exception as exc:
            log(f"PrefixGuard 校验异常已忽略: {exc}", "DEBUG", tag="缓存")
            return None

    def _check_impl(self, scope: str, messages: List[Dict], kind: str) -> Optional[Dict[str, Any]]:
        self.check_count += 1
        layers = _guarded_layers()
        current = self._build_chain(messages, layers)
        key = (scope, kind)
        previous = self._chains.get(key)
        self._chains[key] = current

        if previous is None:
            return None  # 无基线（该 scope 首次调用）

        # 逐位比对既有前缀：current 应为 previous 的追加扩展
        shared = min(len(previous), len(current))
        for i in range(shared):
            if previous[i] != current[i]:
                self.drift_count += 1
                drift = {
                    "broken_at_index": i,
                    "layer": current[i][0],
                    "prev_hash": previous[i][1],
                    "cur_hash": current[i][1],
                    "prev_len": len(previous),
                    "cur_len": len(current),
                }
                log(
                    f"前缀断裂 [{scope}/{kind}] 层={current[i][0]} 位置={i} "
                    f"({previous[i][1]} -> {current[i][1]})",
                    "DEBUG", tag="缓存",
                )
                return drift

        if len(current) < len(previous):
            # 守卫链收缩 = 既有前缀消息被删除/压缩，同样是断裂
            self.drift_count += 1
            return {
                "broken_at_index": len(current),
                "layer": previous[len(current)][0],
                "prev_hash": previous[len(current)][1],
                "cur_hash": None,
                "reason": "guarded_chain_shrunk",
                "prev_len": len(previous),
                "cur_len": len(current),
            }

        return None  # current 是 previous 的纯追加，前缀稳定

    def reset(self, scope: Optional[str] = None) -> None:
        """清空基线（scope=None 清全部，否则清该 scope 下所有用途的基线）。
        压缩/折叠/人设切换后可主动重置，避免把已知的整体重写误报为断裂。"""
        if scope is None:
            self._chains.clear()
        else:
            for key in [k for k in self._chains if k[0] == scope]:
                self._chains.pop(key, None)

    def stats(self) -> Dict[str, Any]:
        return {
            "check_count": self.check_count,
            "drift_count": self.drift_count,
            "tracked_scopes": len(self._chains),
        }


# 全局单例
prefix_guard = PrefixGuard()
