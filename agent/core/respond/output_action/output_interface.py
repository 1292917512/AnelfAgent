from __future__ import annotations

from typing import Optional, Protocol, Union, runtime_checkable

from agent.core.messages import Everything


class OutputProtocol(Protocol):
    async def send_group_msg(self, group_id: Union[int, str], message: str, anything: Optional[Everything] = None):
        ...

    async def send_private_msg(self, uid: Union[int, str], message: str, anything: Optional[Everything] = None):
        ...


@runtime_checkable
class StreamOutputProtocol(OutputProtocol, Protocol):
    """支持流式输出的协议扩展。"""

    async def stream_start(self, anything: Optional[Everything] = None) -> None:
        ...

    async def stream_chunk(self, chunk: str, anything: Optional[Everything] = None) -> None:
        ...

    async def stream_end(self, full_text: str, anything: Optional[Everything] = None) -> None:
        ...

