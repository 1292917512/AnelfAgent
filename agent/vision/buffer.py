"""视觉帧缓冲：按来源分桶的最新帧 + 环形队列 + 变化检测。

注入的唯一入口是 ``ingest``——本地捕获（watcher/工具）与外部帧
（router 推送 / 未来桌面壳 WS 上行）都经此汇入，缓冲与注入逻辑与
帧来源无关。变化检测按来源独立判定（分块 dHash 占比），只有内容级
变化才更新该源的 latest（静态画面不重复烧图片 token）。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional

from core.config import get_config_float, get_config_int
from core.log import log

from .capture import grid_change_ratio, grid_dhash

_LOG_TAG = "视觉"


@dataclass(slots=True)
class VisionFrame:
    """缓冲中的一帧（已判变）。"""

    path: str
    captured_at: float
    source: str
    """帧来源：screen（本机截屏）/ external:<name>（外部推送）等组件 key。"""
    width: int = 0
    height: int = 0
    cells: list = field(default_factory=list)


class VisionBuffer:
    """视觉帧缓冲（进程内单例，经 get_vision_buffer 获取）。"""

    def __init__(self, ring_size: int = 5) -> None:
        self.latest_by_source: Dict[str, VisionFrame] = {}
        self.rings: Dict[str, Deque[VisionFrame]] = {}
        self.latest: Optional[VisionFrame] = None
        """全局最近变化帧（注入画面的取帧点）。"""
        self.last_change_at: float = 0.0
        self._ring_size = ring_size

    async def ingest(
        self,
        path: str,
        source: str,
        *,
        width: int = 0,
        height: int = 0,
        captured_at: Optional[float] = None,
    ) -> tuple[VisionFrame, bool]:
        """汇入一帧：按来源判变，返回 (帧, 是否内容级变化)。

        哈希计算放工作线程（PIL 解码是 CPU 阻塞）。哈希失败（文件损坏等）
        按"有变化"处理——宁可多注入一帧也不丢画面。
        """
        try:
            cells = await asyncio.to_thread(grid_dhash, path)
        except Exception as exc:
            log(f"帧哈希失败按变化处理: {exc}", "DEBUG", tag=_LOG_TAG)
            cells = []
        frame = VisionFrame(
            path=path, captured_at=captured_at or time.time(), source=source,
            width=width, height=height, cells=cells,
        )
        changed = self._is_changed(source, cells)
        self.rings.setdefault(source, deque(maxlen=self._ring_size)).append(frame)
        if changed:
            self.latest_by_source[source] = frame
            self.latest = frame
            self.last_change_at = frame.captured_at
        return frame, changed

    def _is_changed(self, source: str, cells: list) -> bool:
        latest = self.latest_by_source.get(source)
        if latest is None or not cells or not latest.cells:
            return True
        cell_threshold = get_config_int("vision_change_cell_threshold", 3)
        ratio = grid_change_ratio(latest.cells, cells, cell_threshold=cell_threshold)
        return ratio > get_config_float("vision_change_ratio", 0.1)

    def reset(self) -> None:
        """清空缓冲（测试用）。"""
        self.latest_by_source.clear()
        self.rings.clear()
        self.latest = None
        self.last_change_at = 0.0


_buffer: Optional[VisionBuffer] = None


def get_vision_buffer() -> VisionBuffer:
    """进程内单例。"""
    global _buffer
    if _buffer is None:
        _buffer = VisionBuffer()
    return _buffer
