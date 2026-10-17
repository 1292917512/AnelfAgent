"""上下文构建依赖的只读视图与压缩宿主接口，由调用方注入实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent.llm.types import ChatResult


class PFCView(Protocol):
    """上下文可读取的会话短期记忆与群聊发送者。"""

    @property
    def max_temp(self) -> int: ...

    def get_temporary(self, scope: str = "") -> list[dict]: ...

    def get_group_recent_senders(self, scope: str) -> list[tuple[str, str]]: ...


class ToolContextView(Protocol):
    """上下文所需的工具目录版本与当前会话激活态。"""

    @property
    def tools_version(self) -> int: ...

    @property
    def activation_version(self) -> int: ...

    @property
    def tag_activated_tools(self) -> set[str]: ...

    @property
    def discovered_tools(self) -> set[str]: ...

    def get_hot_tool_names(self) -> list[str]: ...

    def active_groups(self) -> dict[str, int]: ...


class CompressionHost(Protocol):
    """压缩所需的模型预算与摘要调用，不承担工具执行。"""

    def get_context_length(self) -> int: ...

    def get_max_output(self) -> int: ...

    async def summarize_text(self, prompt: str) -> str: ...

    async def invoke(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        anything: None = None,
        *,
        tool_choice: str | None = None,
        purpose: str = "reply",
        cache_tail_anchor: bool = True,
    ) -> ChatResult: ...
