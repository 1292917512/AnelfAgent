"""投递事实标签与当前执行链的发送回执。

模型可见：执行上下文与工具结果中的投递方式、状态和目标会话。
Token 影响：每轮一条短状态；审核与发送结果作为有界动态事实追加。
缓存影响：状态置于动态尾部或工具结果，不改 stable/summary/历史前缀。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from core.log import log
from core.tags import tag_label

DeliveryMode = Literal["internal", "channel", "auto", "event", "silent"]
DeliveryStatus = Literal["pending", "sent", "failed", "unknown", "skipped"]


def delivery_tags(mode: DeliveryMode, status: DeliveryStatus, scope: str = "") -> str:
    """编码投递方式、状态与会话归属；标签仅描述事实，不触发发送。"""
    return (tag_label("delivery_mode", mode) + tag_label("delivery_status", status)
            + (tag_label("delivery_scope", scope) if scope else ""))


@dataclass
class _DeliveryCapture:
    scope: str
    acknowledge: Callable[[str, int], object] | None = None
    count: int = 0
    active: bool = True


_capture: ContextVar[_DeliveryCapture | None] = ContextVar("delivery_capture", default=None)
_observed_inputs: ContextVar[dict[str, int] | None] = ContextVar("delivery_inputs", default=None)


@contextmanager
def capture_deliveries(scope: str, *, acknowledge: Callable[[str, int], object] | None = None) -> Iterator[None]:
    """隔离一轮执行的回执；嵌套执行与结束后迟到的后台发送不污染父轮。"""
    capture = _DeliveryCapture(scope, acknowledge=acknowledge)
    token = _capture.set(capture)
    input_token = _observed_inputs.set({})
    try:
        yield
    finally:
        capture.active = False
        _observed_inputs.reset(input_token)
        _capture.reset(token)


def observe_delivery_context(messages: Sequence[Mapping[str, object]]) -> None:
    """记录本次模型输入中跨会话用户消息的水位，不消费待回复任务。"""
    capture = _capture.get()
    if capture is None or not capture.active:
        return
    observed: dict[str, int] = {}
    for message in messages:
        source = message.get("_source")
        if not isinstance(source, dict) or source.get("origin") != "cross_channel_narrative":
            continue
        scope, timestamp = source.get("scope"), source.get("ts_ns")
        if source.get("role") == "user" and isinstance(scope, str) and isinstance(timestamp, int) and timestamp > 0:
            observed[scope] = max(observed.get(scope, 0), timestamp)
    _observed_inputs.set(observed)


def delivery_watermark(scope: str) -> int | None:
    """取得本轮已感知消息的水位，发送前冻结以保留发送期间的新消息。"""
    capture = _capture.get()
    observed = _observed_inputs.get()
    return observed.get(scope) if capture is not None and capture.active and observed is not None else None


def record_delivery(scope: str, *, through_ts: int | None = None) -> None:
    """登记成功投递，并收束已感知且已回复的跨会话待办。"""
    capture = _capture.get()
    if capture is None or not capture.active:
        return
    if scope == capture.scope:
        capture.count += 1
    elif through_ts is not None and capture.acknowledge is not None:
        try:
            capture.acknowledge(scope, through_ts)
        except Exception as exc:
            log(f"已投递但待回复状态收束失败 ({scope}): {exc}", "WARNING", tag="通道")


def delivery_count() -> int:
    """当前执行会话已确认的发送次数。"""
    capture = _capture.get()
    return capture.count if capture is not None and capture.active else 0
