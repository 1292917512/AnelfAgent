"""会话级用量统计 — per-scope 累计 LLM 用量与回复轮数（对齐 dsh sessionStats 投影）。

与 cache_stats（CacheUsageTracker）的分工：那是**单次调用环形缓冲**服务于
缓存命中率诊断；本模块是 **per-scope 累计账本**服务于成本追踪——"这个会话/
这个月花了多少 token"是个人助理的刚需观测面。

设计（对齐 dsh "全日志投影，翻页压缩改变不了全图数字"的语义，适配为
内存累计 + 周期落盘）：
- ``record(scope, kind, usage)``：每次 LLM 调用后在 llm_invoker 记录点直采
  （fail-open，异常绝不影响调用主流程）；
- 内存累计 per scope，每 ``usage_stats_flush_every``（默认 20）次调用或
  ``flush(scope)``（complete_reply 每 reply 一次）时经注入的回调增量 upsert
  到主库 ``scope_usage`` 表（累加语义，重启最多丢一个 flush 窗口——统计
  非账本，可接受）；
- ``turn()``：REPLY 完成时 turns+1（与 dsh 计闭合 step 而非装配消息同理：
  计 reply 完成而非 LLM 调用次数）。

分层纪律：本模块不直接依赖 storage 层——flush 回调由 Mind 接线时注入
（``sqlite_backend.upsert_scope_usage``），保持 agent/mind 的独立性。

Model Experience：纯后台统计，不向模型上下文注入任何内容，零缓存影响。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from core.config import get_config_int
from core.log import log

# 单 scope 内存累计条目上限（防异常 scope 泄漏；超出丢弃最旧统计但不回滚 DB）
_MAX_SCOPES = 200

# scope -> 累计字段（llm_calls/prompt/completion/total/cache_read/turns + 未落盘计数）
_ScopeAcc = Dict[str, int]


class ScopeUsageStats:
    """per-scope 用量累计器（Mind 持有，单事件循环内调用无需加锁）。"""

    def __init__(self, flush_callback: Optional[Callable[[str, _ScopeAcc], Awaitable[None]]] = None) -> None:
        self._acc: Dict[str, _ScopeAcc] = {}
        self._flush_callback = flush_callback

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------

    def record(self, scope: str, kind: str, usage: Any) -> None:
        """记录一次 LLM 调用的用量（fail-open：异常吞掉只记日志）。

        kind 为调用用途（reply/reflect/compress），当前统一计入 scope 总量；
        reflect 类调用 scope 可能为空（临时 scope），空 scope 直接跳过——
        临时会话的成本不归属任何持久对话。
        """
        if not scope or usage is None:
            return
        try:
            entry = self._acc.get(scope)
            if entry is None:
                if len(self._acc) >= _MAX_SCOPES:
                    return  # 超上限保护：新 scope 不再累计（既有统计不受影响）
                entry = {
                    "llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0, "cache_read_tokens": 0, "turns": 0,
                    "_pending": 0,
                }
                self._acc[scope] = entry
            entry["llm_calls"] += 1
            entry["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
            entry["completion_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
            entry["total_tokens"] += int(getattr(usage, "total_tokens", 0) or 0)
            entry["cache_read_tokens"] += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            entry["_pending"] += 1
            if entry["_pending"] >= max(1, get_config_int("usage_stats_flush_every", 20)):
                self._schedule_flush(scope)
        except Exception as exc:
            log(f"会话用量统计记录失败（已忽略）: {exc}", "DEBUG", tag="统计")

    def turn(self, scope: str) -> None:
        """一次 REPLY 完成（complete_reply 调用）：turns+1 并调度落盘。"""
        if not scope:
            return
        entry = self._acc.get(scope)
        if entry is None:
            if len(self._acc) >= _MAX_SCOPES:
                return
            entry = {
                "llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens": 0, "cache_read_tokens": 0, "turns": 0,
                "_pending": 0,
            }
            self._acc[scope] = entry
        entry["turns"] += 1
        self._schedule_flush(scope)

    # ------------------------------------------------------------------
    # 落盘与读取
    # ------------------------------------------------------------------

    def _schedule_flush(self, scope: str) -> None:
        """同步触发 flush（回调为 async 时排入事件循环；无回调则仅内存累计）。"""
        if self._flush_callback is None:
            return
        entry = self._acc.get(scope)
        if entry is None or entry.get("_pending", 0) <= 0:
            return
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 无事件循环（测试/关停中）：留待下次触发
        loop.create_task(self.flush(scope))

    async def flush(self, scope: str) -> None:
        """把该 scope 的增量 upsert 到持久层（累加语义），清零未落盘计数。"""
        entry = self._acc.get(scope)
        if entry is None or self._flush_callback is None:
            return
        delta = {k: v for k, v in entry.items() if not k.startswith("_")}
        if not any(delta.values()):
            return
        entry["_pending"] = 0
        try:
            await self._flush_callback(scope, delta)
        except Exception as exc:
            # 落盘失败：计数回滚待下次重试（增量不丢）
            entry["_pending"] = 1
            log(f"会话用量落盘失败（下次重试）: {scope} {exc}", "DEBUG", tag="统计")

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        """内存累计视图（API 兜底用；权威值在 DB）。"""
        return {
            scope: {k: v for k, v in entry.items() if not k.startswith("_")}
            for scope, entry in self._acc.items()
        }


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_SCOPE_USAGE_CONFIGS = {
    "mind/usage": {
        "usage_stats_flush_every": {
            "description": "会话用量统计的落盘频率：每 N 次 LLM 调用增量写入主库"
                           "（回复完成时也会落盘；重启最多丢一个窗口的统计）",
            "default": 20,
            "advanced": True,
            "unit": "次",
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_SCOPE_USAGE_CONFIGS)


# 全局单例（Mind 接线前可用；Mind 初始化时经 wire_scope_usage 注入落盘回调）
scope_usage_stats = ScopeUsageStats()


def wire_scope_usage(flush_callback: Callable[[str, _ScopeAcc], Awaitable[None]]) -> None:
    """Mind 初始化时接线落盘回调（bootstrap 组装阶段调用一次）。"""
    global scope_usage_stats
    scope_usage_stats = ScopeUsageStats(flush_callback=flush_callback)
