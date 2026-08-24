"""NoneBot 桥接服务的 Web 服务层入口（转发 channels.nonebot_bridge 实现）。

channels 层持有 NoneBot 桥接的全部业务实现，本模块仅作分层桥接，
使 web 层不直接依赖 channels。
"""

from __future__ import annotations

from channels.nonebot_bridge.service import NoneBotService
from channels.nonebot_bridge.tools import register_nonebot_tools

__all__ = ["NoneBotService", "register_nonebot_tools"]
