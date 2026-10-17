"""模型用量观测与动态预算、缓存提示。"""


from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from agent.mind.execution.state import ExecutionContext, RoundState
from core.config import (
    register_configs_safe,  # noqa: E402
)
from core.event_bus import event_bus
from core.stream_events import EVENT_CONTEXT_USAGE

if TYPE_CHECKING:
    from agent.context.compressor import ContextCompressor


async def _emit_context_usage(ctx: ExecutionContext, state: RoundState) -> None:
    """上下文用量快照（usage 锚定：API 真实用量优先；供 webui 状态栏显示）。"""
    mind = ctx.mind
    compressor: Optional["ContextCompressor"] = getattr(mind, "compressor", None)
    if compressor is None:
        return
    try:
        _tokens = state.last_input_tokens or compressor.estimate_tokens(
            ctx.base_messages + ctx.tool_chain)
        _threshold = compressor.threshold_tokens()
        _window = mind.model_invoker.get_context_length()
        from agent.mind.think_session import ContextUsage, current_context_usage
        current_context_usage.set(ContextUsage(context_window=_window, tokens_used=_tokens))
        if _threshold > 0:
            await event_bus.emit(EVENT_CONTEXT_USAGE, {
                "scope": ctx.current_scope,
                "tokens": _tokens,
                "threshold": _threshold,
                "window": _window,
                "percent": round(_tokens / _threshold * 100, 1),
                "cache_read_input_tokens": state.last_cache_read_tokens,
                "cache_creation_input_tokens": state.last_cache_creation_tokens,
                "cache_hit_rate": state.last_cache_hit_rate,
            })
    except Exception:
        pass  # 状态事件失败不影响主流程


_TOKEN_BUDGET_CONFIGS = {
    "mind/token_budget": {
        "token_budget_reminder_enabled": {
            "description": "是否在执行上下文中注入上下文预算提醒（逼近压缩阈值时提示模型收尾或主动压缩）",
            "default": True,
        },
    },
}


register_configs_safe(_TOKEN_BUDGET_CONFIGS)


_TOKEN_BUDGET_URGENT_RATIO = 0.85


def _token_budget_hint(ctx: ExecutionContext, state: RoundState) -> str:
    """计算上下文预算提醒文本（未达提醒水位返回空串）。

    用量取上轮真实 prompt_tokens，缺失时估算；
    百分比以模型窗口为分母，触发以压缩阈值为锚（阈值前留出主动压缩窗口）。
    """
    mind = ctx.mind
    compressor = getattr(mind, "compressor", None)
    if compressor is None:
        return ""
    try:
        from core.config import get_config_bool
        if not get_config_bool("token_budget_reminder_enabled", True):
            return ""
        threshold = compressor.threshold_tokens()
        window = mind.model_invoker.get_context_length()
        if threshold <= 0 or window <= 0:
            return ""
        tokens = state.last_input_tokens or compressor.estimate_tokens(
            ctx.base_messages + ctx.tool_chain)
        if tokens <= 0:
            return ""
        if tokens / threshold >= _TOKEN_BUDGET_URGENT_RATIO:
            pct = int(tokens / window * 100)
            return (
                f"[上下文预算] 上下文窗口已用约 {pct}%，接近自动压缩阈值。"
                "请优先收尾当前任务：不再读取大文件/大段新资料，已完成的工作不要重复；"
                "如仍有大量后续工作，调用 compress_context 并带 focus_topic 保留关键上下文后继续。"
            )
    except Exception:
        pass  # 预算提醒失败不影响主流程
    return ""


_CACHE_STATUS_CONFIGS = {
    "cache/prompt": {
        "cache_status_hint_enabled": {
            "description": "是否在执行上下文中注入上一轮缓存命中状态行（命中率与真实 read/输入 tokens）",
            "default": True,
        },
    },
}


register_configs_safe(_CACHE_STATUS_CONFIGS)


def _cache_status_hint(state: RoundState) -> str:
    """上一轮 LLM 调用的缓存命中状态行（无真实用量或不可观测时返回空串）。

    注入准入：last_input_tokens > 0（首轮与压缩后重置轮天然抑制）且端点
    回报了缓存字段——不可观测时静默缺席而非谎报 0%。输入总量取口径归一后
    的 total_input_tokens（prompt 含/不含缓存两种记账下均为真实分母）。
    """
    try:
        from core.config import get_config_bool
        if not get_config_bool("cache_status_hint_enabled", True):
            return ""
    except Exception:
        return ""
    if state.last_input_tokens <= 0 or not state.last_cache_observable:
        return ""
    usage_text = f"read {state.last_cache_read_tokens:,} / 输入 {state.last_input_tokens:,} tokens"
    if state.last_cache_creation_tokens > 0:
        usage_text += f"，写入 {state.last_cache_creation_tokens:,}"
    return f"[缓存] 上轮命中 {state.last_cache_hit_rate * 100:.1f}%（{usage_text}）"
