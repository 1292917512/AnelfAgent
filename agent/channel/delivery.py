"""投递事实标签与当前执行链的发送回执。

模型可见：执行上下文与工具结果中的投递方式、状态和目标会话。
Token 影响：每轮一条短状态；未发送就结束时最多追加一次恢复反馈。
缓存影响：状态置于动态尾部或工具结果，不改 stable/summary/历史前缀。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from core.tags import tag_label

DeliveryMode = Literal["internal", "channel", "event", "silent"]
DeliveryStatus = Literal["pending", "sent", "failed", "unknown", "skipped"]


def delivery_tags(mode: DeliveryMode, status: DeliveryStatus, scope: str = "") -> str:
    """编码投递方式、状态与会话归属；标签仅描述事实，不触发发送。"""
    return (tag_label("delivery_mode", mode) + tag_label("delivery_status", status)
            + (tag_label("delivery_scope", scope) if scope else ""))


@dataclass
class _DeliveryCapture:
    scope: str
    count: int = 0
    active: bool = True


_capture: ContextVar[_DeliveryCapture | None] = ContextVar("delivery_capture", default=None)


@contextmanager
def capture_deliveries(scope: str) -> Iterator[None]:
    """隔离一轮执行的回执；嵌套执行与结束后迟到的后台发送不污染父轮。"""
    capture = _DeliveryCapture(scope)
    token = _capture.set(capture)
    try:
        yield
    finally:
        capture.active = False
        _capture.reset(token)


def record_delivery(scope: str) -> None:
    """由发送管道登记已确认送达当前执行会话的消息。"""
    capture = _capture.get()
    if capture is not None and capture.active and scope == capture.scope:
        capture.count += 1


def delivery_count() -> int:
    """当前执行会话已确认的发送次数。"""
    capture = _capture.get()
    return capture.count if capture is not None and capture.active else 0
