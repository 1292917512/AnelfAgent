"""wcauto 频道状态 — last_seq 续传水位持久化 + 消息去重。

水位落频道数据目录（<data_dir>/channels/wcauto/），原子写防崩溃半截文件。
"""

from __future__ import annotations

import json
import os
import time

from agent.channel.dedup import MessageDedup
from channels._shared.state import channel_data_dir
from core.log import log


class WcautoStateStore:
    """频道侧状态：桥事件 seq 水位（断线重连续传用）。"""

    def __init__(self) -> None:
        self._path = os.path.join(channel_data_dir("wcauto"), "transport_state.json")
        self._seq = self._read()
        self._last_save = 0.0

    def _read(self) -> int:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            return max(int(data.get("last_seq") or 0), 0)
        except Exception:
            return 0

    def load_seq(self) -> int:
        return self._seq

    def save_seq(self, seq: int) -> None:
        """推进水位（单调递增；节流 1s 落盘，stop 时强制落盘）。"""
        seq = int(seq)
        if seq <= self._seq:
            return
        self._seq = seq
        now = time.time()
        if now - self._last_save < 1.0:
            return
        self._last_save = now
        self.flush()

    def flush(self) -> None:
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"last_seq": self._seq, "saved_at": time.time()}, f)
            os.replace(tmp, self._path)
        except OSError as exc:
            log(f"wcauto: 水位落盘失败 -> {exc}", "WARNING", tag="通道")


def make_dedup() -> MessageDedup:
    """入站消息去重器（5 分钟窗口 + 容量淘汰）。"""
    return MessageDedup(ttl_seconds=300, max_size=3000)
