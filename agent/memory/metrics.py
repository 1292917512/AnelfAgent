"""记忆系统运行指标：进程内累计计数器，供 memory_stats 工具按需查询。

设计目标：写入路径零成本（字典自增）、单事件循环内天然线程安全、
无外部依赖。计数自进程启动累计，重启归零——用于观察趋势而非精确审计。

指标不进 prompt（心跳状态区块只保留 AI 可行动项），AI 需要时经
memory_stats 工具查询。
"""

from __future__ import annotations

import time
from typing import Dict

_counters: Dict[str, int] = {}
_started_at: float = time.time()


def incr(key: str, n: int = 1) -> None:
    """计数器自增（key 用 "." 分层，如 recall.fts_hits）。"""
    _counters[key] = _counters.get(key, 0) + n


def snapshot() -> Dict[str, int]:
    """当前累计计数快照（memory_stats 工具消费）。"""
    return dict(_counters)
