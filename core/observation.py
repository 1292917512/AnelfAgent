"""执行区间的耗时与异常事实；不调度任务，不修改返回值或模型上下文。"""

from __future__ import annotations

import asyncio
import time
from types import TracebackType


class ExecutionObservation:
    """以单调时钟测量一个执行区间，退出时冻结耗时并原样传播异常。"""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._finished: float | None = None
        self.error: BaseException | None = None

    @property
    def elapsed_ms(self) -> float:
        end = self._finished if self._finished is not None else time.monotonic()
        return max(0.0, (end - self._started) * 1000)

    @property
    def cancelled(self) -> bool:
        return isinstance(self.error, asyncio.CancelledError)

    def __enter__(self) -> ExecutionObservation:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.error = exc
        self._finished = time.monotonic()
