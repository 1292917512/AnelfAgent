"""执行取消上下文：信号随任务传播，资源收束后可携带部分完成回执。

模型可见：取消回执保留已完成步骤和未知结果，禁止以取消推断操作未生效。
Token：仅取消时增加一条有界工具回执，不增加常驻注入。
缓存：只影响工具结果尾部，不改变稳定前缀。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class ExecutionSignal:
    """一次执行的协作式停止信号；旧任务保留旧信号，不跨轮次复用。"""

    def __init__(self, scope: str = "", run_id: str = "") -> None:
        self.scope = scope
        self.run_id = run_id
        self.event = asyncio.Event()
        self.reason = ""

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    async def wait(self) -> None:
        await self.event.wait()

    def checkpoint(self) -> None:
        """副作用开始前检查；已停止则不再启动后续操作。"""
        if self.cancelled:
            raise asyncio.CancelledError(self.reason)

    def request(self, reason: str) -> None:
        """由执行控制器置位；信号一旦停止不能复位。"""
        self.reason = reason
        self.event.set()


class ToolCancelled(asyncio.CancelledError):
    """工具已收束资源后的取消；result 为同普通工具结果格式的 JSON。"""

    def __init__(self, result: str) -> None:
        super().__init__("工具执行已停止")
        self.result = result


_signal: ContextVar[ExecutionSignal | None] = ContextVar("execution_signal", default=None)


def get_execution_signal() -> ExecutionSignal | None:
    """读取当前执行信号；非执行上下文返回 None，不隐式选取其他会话。"""
    return _signal.get()


@contextmanager
def execution_signal(signal: ExecutionSignal) -> Iterator[None]:
    """组合根将一次执行绑定到异步上下文，子任务自动继承。"""
    token = _signal.set(signal)
    try:
        yield
    finally:
        _signal.reset(token)
