"""恢复策略 — 把错误分类结果变成具体的回退/跳过决策。

think_loop 负责"对话级"恢复（压缩后重试已在其循环内闭环），
本模块负责"候选链级"恢复：chat_with_fallback 在候选间推进时，
避免用同一份超限消息逐个撞所有回退模型。

核心判断：
- 上下文超限错误在候选链中快速失败（fail-fast），由调用方压缩后重试；
- 但上下文窗口更大的候选仍值得尝试 —— 它能直接装下当前消息，
  省掉一次压缩带来的信息损失。
"""
from __future__ import annotations

from typing import Any, Optional

from agent.llm.resilience.classifier import classify_llm_error


def is_overflow_error(exc: BaseException) -> bool:
    """异常是否为上下文超限（需要压缩或更大窗口，而非简单重试）。"""
    return classify_llm_error(exc).should_compress


def _candidate_context_window(client: Any) -> int:
    """取候选客户端的上下文窗口（tokens），未知返回 0。"""
    try:
        from agent.llm.llm_client import LLMClient
        info = LLMClient.get_model_info(client.config.litellm_model)
        window = info.get("max_input_tokens") or info.get("max_tokens") or 0
        if not window:
            window = client.config.context_window or 0
        return int(window)
    except Exception:
        return 0


def should_try_fallback_candidate(
        exc: BaseException,
        failed_client: Any,
        candidate: Any,
) -> bool:
    """上一候选失败的情况下，是否值得尝试下一个候选。

    仅对上下文超限做窗口比较：窗口不大于失败者的候选必然同样溢出，
    跳过它以节省一次必然失败的 API 调用（费用 + 延迟）。
    非超限错误（限流/服务异常/鉴权）不做窗口判断，一律继续尝试。
    """
    if not is_overflow_error(exc):
        return True
    failed_window = _candidate_context_window(failed_client)
    candidate_window = _candidate_context_window(candidate)
    # 窗口未知时保守放行（宁可多试一次，不漏掉可能成功的候选）
    if failed_window <= 0 or candidate_window <= 0:
        return True
    return candidate_window > failed_window


def next_fallback_index(
        exc: BaseException,
        failed_client: Any,
        candidates: list,
        start: int,
) -> Optional[int]:
    """从 start 起找第一个值得尝试的候选下标，没有则返回 None。"""
    for i in range(start, len(candidates)):
        if should_try_fallback_candidate(exc, failed_client, candidates[i]):
            return i
    return None
