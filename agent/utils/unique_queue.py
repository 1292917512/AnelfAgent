"""去重队列 — 同一元素未消费前只入队一次。"""

from __future__ import annotations

from collections import deque
from typing import Deque, Generic, TypeVar

T = TypeVar("T")


class UniqueQueue(Generic[T]):
    """去重队列：同一元素未消费前只入队一次。"""

    def __init__(self) -> None:
        self.queue: Deque[T] = deque()
        self.seen: set[T] = set()

    def append(self, item: T) -> None:
        if item not in self.seen:
            self.seen.add(item)
            self.queue.append(item)

    def popleft(self) -> T:
        if not self.queue:
            raise IndexError("队列为空")
        item = self.queue.popleft()
        self.seen.remove(item)
        return item

    def __len__(self) -> int:
        return len(self.queue)

    def is_empty(self) -> bool:
        return len(self.queue) == 0
