"""当前会话频道上下文。

思维循环在解析出当前回复目标频道（adapter_key）后绑定到本 ContextVar，
通用频道工具（跨频道共享、需路由到具体频道的工具）据此确定默认目标频道：
显式 channel_id 参数 > 本上下文 > 返回错误。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

_current_channel: ContextVar[Optional[str]] = ContextVar(
    "channel_current_adapter_key", default=None
)


def bind_current_channel(adapter_key: str) -> Token:
    """绑定当前会话频道，返回用于复位的 Token。"""
    return _current_channel.set(adapter_key)


def get_current_channel() -> Optional[str]:
    """获取当前会话频道（未绑定时为 None）。"""
    return _current_channel.get()


def reset_current_channel(token: Token) -> None:
    """按 Token 复位上下文。"""
    _current_channel.reset(token)
