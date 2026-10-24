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


class CaptureSource(Protocol):
    """捕获管线只读的会话材料入口；不暴露运行时、频道适配器或写事务。"""

    async def list_conversation_scope_keys(self) -> list[dict[str, Any]]: ...

    async def fetch_conversation_with_id(
        self, *, scope_type: str, scope_id: str, limit: int = 100,
        before_id: int | None = None, after_id: int | None = None,
    ) -> list[dict[str, Any]]: ...


class RelationHost(Protocol):
    @property
    def memory_store(self) -> MemoryStore | None: ...

    async def reflect(
        self, messages: list[dict[str, Any]], *, options: dict[str, Any] | None = None,
    ) -> str: ...
