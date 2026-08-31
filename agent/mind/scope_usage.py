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

from contextvars import ContextVar, Token
from typing import Any, Awaitable, Callable, Dict, Optional

from core.async_helper import spawn
from core.config import get_config_int
from core.log import log

# 单 scope 内存累计条目上限（防异常 scope 泄漏；超出丢弃最旧统计但不回滚 DB）
_MAX_SCOPES = 200

# scope -> 累计字段（llm_calls/prompt/completion/total/cache_read/turns + 未落盘计数）
_ScopeAcc = Dict[str, int]

# 用量归属 scope：委托执行链显式绑定父会话 scope（子代理 reflect 的一次性
# scope 不该独立建账，经此 ContextVar 归属到发起委托的会话）
usage_scope_var: ContextVar[str] = ContextVar("usage_scope", default="")


def bind_usage_scope(scope: str) -> Token:
    """绑定用量归属 scope（返回 token 供 reset_usage_scope）。"""
    return usage_scope_var.set(scope)


def reset_usage_scope(token: Token) -> None:
    """复位 bind_usage_scope 的绑定。"""
    usage_scope_var.reset(token)


def current_usage_scope() -> str:
    """读取当前绑定的用量归属 scope（未绑定为空串）。"""
    return usage_scope_var.get()


def _is_ephemeral_scope(scope: str) -> bool:
    """一次性 scope（reflect:{uuid} 等）不建独立统计行。

    mind.reflect 每次生成一次性 scope 并经 think_session 绑定激活上下文，
    llm_invoker 的 scope 兜底会取到它——若放任建行，每个子代理/内部评审
    都会留下孤儿统计行，累积挤爆 _MAX_SCOPES 后新会话的用量被整体静默
    丢弃。一次性调用的正确归宿是经 bind_usage_scope 归属父会话（委托链
    已绑定），无归属的直接丢弃。
    """
    return scope.startswith("reflect:")


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

        kind 为调用用途（reply/reflect/compress）。reflect 类调用的一次性
        scope 不建独立行：委托链经 bind_usage_scope 绑定的父 scope 在
        llm_invoker 侧优先解析到此，无归属的一次性调用直接丢弃。
        """
        if not scope or _is_ephemeral_scope(scope) or usage is None:
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
        if not scope or _is_ephemeral_scope(scope):
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
            asyncio.get_running_loop()  # 无事件循环（测试/关停中）时 spawn 会炸，先探测
        except RuntimeError:
            return
        spawn(self.flush(scope), name="scope_usage.flush")

    async def flush(self, scope: str) -> None:
        """把该 scope 的增量 upsert 到持久层（累加语义）。

        快照即清零：落盘的是"自上次 flush 以来的真实增量"，后续 record
        计入下一增量；落盘失败时把快照加回（增量不丢不重）。在途 flush
        去重：并发触发共享同一份快照，不双写。
        """
        entry = self._acc.get(scope)
        if entry is None or self._flush_callback is None:
            return
        if entry.get("_flushing"):
            return  # 在途 flush 已携带当前快照，本增量随下次触发落盘
        delta = {k: v for k, v in entry.items() if not k.startswith("_")}
        if not any(delta.values()):
            entry["_pending"] = 0
            return
        for k in delta:
            entry[k] = 0
        entry["_pending"] = 0
        entry["_flushing"] = True
        failed = False
        try:
            await self._flush_callback(scope, delta)
        except Exception as exc:
            failed = True
            # 落盘失败：快照加回，待下次 record/turn 触发重试（增量不丢）；
            # 不在此重调度——持久故障下立即重试会自旋
            for k, v in delta.items():
                entry[k] = entry.get(k, 0) + v
            entry["_pending"] = 1
            log(f"会话用量落盘失败（下次重试）: {scope} {exc}", "DEBUG", tag="统计")
        finally:
            entry["_flushing"] = False
            # 成功且在途期间又来了新用量：补一次落盘
            if not failed and any(
                v for k, v in entry.items() if not k.startswith("_")
            ):
                self._schedule_flush(scope)

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
