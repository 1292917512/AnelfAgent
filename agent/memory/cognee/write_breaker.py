"""进程自身磁盘写入速率熔断：防止 cognee 投影把本机写盘配额打爆。

cognee 图抽取（Kùzu checkpoint 刷盘）在长批次内可持续高频写盘，
曾以 2.1GB/5min 的速率撞爆 macOS 单日磁盘写入配额导致进程卡死。
本模块按滑动窗口采样进程自身累计写入字节（cognee 与 Kùzu 均在本
进程内执行，psutil io_counters 即完整口径），速率超阈值即建议熔断；
由协调器在批次之间执行暂停，无法中断已在跑的管线调用。
"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable, Deque, Optional, Tuple

# 采样器：返回进程累计写入字节数；不可用时抛异常
WriteSampler = Callable[[], int]
# 时钟：单调纳秒，测试可注入
MonotonicClock = Callable[[], int]


def _default_sampler() -> int:
    import psutil

    counters = psutil.Process().io_counters()
    return int(counters.write_bytes)


class WriteBreaker:
    """滑动窗口写入速率判定；平台不支持 io_counters 时自动停用。"""

    def __init__(
        self,
        threshold_mb: float,
        window_seconds: float,
        *,
        sampler: Optional[WriteSampler] = None,
        clock: MonotonicClock = time.monotonic_ns,
    ) -> None:
        self._threshold_bytes = max(0.0, threshold_mb) * 1024 * 1024
        self._window_ns = int(max(1.0, window_seconds) * 1e9)
        self._sampler = sampler or _default_sampler
        self._clock = clock
        self._samples: Deque[Tuple[int, int]] = deque()
        self._available = True

    @property
    def available(self) -> bool:
        """采样是否可用（不可用时熔断判定恒为放行）。"""
        return self._available

    def observe(self) -> None:
        """采样一次累计写入字节，窗口外旧样本出队。"""
        if not self._available:
            return
        try:
            write_bytes = self._sampler()
        except Exception:
            # psutil io_counters 在部分平台/权限下不可用：停用而非反复报错
            self._available = False
            self._samples.clear()
            return
        now_ns = self._clock()
        self._samples.append((now_ns, write_bytes))
        while self._samples and now_ns - self._samples[0][0] > self._window_ns:
            self._samples.popleft()

    def over_threshold(self) -> Tuple[bool, float]:
        """窗口内写入速率是否超阈值，返回 (是否超限, 速率 MB/s)。"""
        if not self._available or len(self._samples) < 2:
            return False, 0.0
        newest_ns, newest_bytes = self._samples[-1]
        oldest_ns, oldest_bytes = self._samples[0]
        elapsed_ns = newest_ns - oldest_ns
        if elapsed_ns <= 0:
            return False, 0.0
        rate_mb_s = (newest_bytes - oldest_bytes) / 1024 / 1024 / (elapsed_ns / 1e9)
        # 速率口径：threshold_mb/window_seconds 折算为每秒上限，
        # 批次内的短时风暴无需等满窗口即可触发
        limit_mb_s = self._threshold_bytes / 1024 / 1024 / (self._window_ns / 1e9)
        return rate_mb_s > limit_mb_s, rate_mb_s
