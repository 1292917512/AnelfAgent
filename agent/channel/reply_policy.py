"""频道声明的回复入口、工具分组和操作规则。

Model Experience: 运行中频道声明入口工具与规则，可为已执行操作补交事实回执。
Token effect: 精简目录省去无关 schema；事实回执不增加模型调用。
Cache effect: 规则放在历史后的 channel_policy 层，精简目录仅随本 scope 显式发现扩展。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.channel.manager import ChannelManager


@dataclass(frozen=True)
class ReplyToolResult:
    """本轮已返回的工具事实；不包含模型独白或结束备注。"""

    name: str
    payload: object


@dataclass(frozen=True)
class ReplyPolicy:
    """频道回复约定；默认保留普通频道的元决策和工具发现行为。"""

    direct_reply: bool = False
    tool_groups: tuple[str, ...] = ()
    instructions: str = ""
    # None 沿用全局目录；显式名单只自动注入这些工具，仍允许本会话发现/激活。
    initial_tools: tuple[str, ...] | None = None
    result_receipt: Callable[[Sequence[ReplyToolResult]], str] | None = None


def get_reply_policy(adapter_key: str, manager: ChannelManager | None = None) -> ReplyPolicy:
    """只读取运行中频道的声明，未知或停用频道不注入规则。"""
    from agent.channel.channel_types import ChannelStatus

    if not adapter_key:
        return ReplyPolicy()
    if manager is None:
        from agent.channel.manager import get_channel_manager

        manager = get_channel_manager()
    channel = manager.get(adapter_key)
    if channel is None or getattr(channel, "status", None) != ChannelStatus.RUNNING:
        return ReplyPolicy()
    policy = getattr(channel, "reply_policy", None)
    return policy if isinstance(policy, ReplyPolicy) else ReplyPolicy()
