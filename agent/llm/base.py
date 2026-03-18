from __future__ import annotations

from typing import Any, AsyncGenerator, Optional, Protocol, runtime_checkable

from .types import ChatResult, ChatStreamDelta


@runtime_checkable
class ChatModel(Protocol):
    async def chat(
        self,
        messages: list[dict],
        *,
        options: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[Any] = None,
    ) -> ChatResult:
        ...

    def chat_stream(
        self,
        messages: list[dict],
        *,
        options: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[Any] = None,
    ) -> AsyncGenerator[ChatStreamDelta, None]:
        ...

