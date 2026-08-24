"""MCP 重连重试预算（稳定窗口复位）。"""

from __future__ import annotations

from core.log import log


class _RetryBudget:
    """重连重试预算：连续失败计数，稳定连接超过窗口后复位。

    对齐 dsh reconnect 的 budget-reset-after-stability：没有复位时，
    长期运行的服务偶发抖动累计 N 次后永久放弃（工具全部注销），
    需要人工 reload 才能恢复——稳定期过后的失败应重新计满预算。
    """

    def __init__(self, max_retries: int, reset_after_sec: float) -> None:
        self._max = max_retries
        self._reset_after = reset_after_sec
        self.attempt = 0

    @property
    def exhausted(self) -> bool:
        return self.attempt >= self._max

    def record_failure(self, stable_seconds: float = 0.0) -> float:
        """记录一次失败，返回退避等待秒数。

        stable_seconds 为该次失败前连接的稳定运行时长；达到复位窗口时
        预算清零重计（本次失败即新预算的第 1 次），退避也从最短档重来。
        """
        if stable_seconds >= self._reset_after:
            self.attempt = 0
            log(
                f"连接稳定运行 {int(stable_seconds)}s 后失败，重连重试预算已复位",
                "DEBUG", tag="MCP",
            )
        self.attempt += 1
        return float(min(2 ** (self.attempt - 1), 60))
