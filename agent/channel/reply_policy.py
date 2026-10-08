"""频道声明的回复入口、工具分组和操作规则。

Model Experience: 已连接频道的规则确定性注入，显式工具组直接进入本会话目录。
Token effect: 增加频道声明的短规则，省去发现/激活分组的模型轮次。
Cache effect: 规则放在历史后的 channel_policy 层，工具集合在本频道内保持稳定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.channel.manager import ChannelManager


@dataclass(frozen=True)
class ReplyPolicy:
    """频道回复约定；默认保留普通频道的元决策和工具发现行为。"""

    direct_reply: bool = False
    tool_groups: tuple[str, ...] = ()
    instructions: str = ""


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
