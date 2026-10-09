"""记忆任务所需的最小宿主接口，由运行时注入实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .embedding import Embedder
    from .memory_store import MemoryStore


class MemoryHost(Protocol):
    @property
    def memory_store(self) -> MemoryStore | None: ...

    @property
    def embedder(self) -> Embedder: ...


class RelationHost(Protocol):
    @property
    def memory_store(self) -> MemoryStore | None: ...

    async def reflect(
        self, messages: list[dict[str, Any]], *, options: dict[str, Any] | None = None,
    ) -> str: ...
