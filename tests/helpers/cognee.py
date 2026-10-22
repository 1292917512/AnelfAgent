"""不接触文件或子进程的投影后端事务替身。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class FakeIndexTransactions:
    publication_status: dict[str, Any] = {}

    @asynccontextmanager
    async def write_transaction(self) -> AsyncIterator[None]:
        yield

    @asynccontextmanager
    async def idle_snapshot(self) -> AsyncIterator[None]:
        yield

    def sample_write_bytes(self) -> int:
        return 0

    def reset_fault(self) -> None:
        pass

    async def verify_graph_documents(self, dataset_id: str, data_ids: list[str]) -> None:
        pass
