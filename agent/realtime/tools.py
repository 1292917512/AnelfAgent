"""实时语音 AI 工具面：通话状态查询。

语音的呈现形态归频道：AI 统一经 send_message 发消息，通话中的会话由
出口层自动 TTS 播报（用户说话中不插播，消息仍以文字送达）。本模块
只保留状态查询；通话入口在工作区（频道实时形态）。
"""

from __future__ import annotations

import json
from typing import Any

from core.config import get_config
from entities._sdk import deferred_tool, error_from_exception

from .engine import get_realtime_engine


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


@deferred_tool(group="voice", tags=["always"], concurrency_safe=True)
async def realtime_status() -> str:
    """查看实时语音通话状态：活跃会话/状态（收听/思考/说话）/端点档位/默认音色。

    通话中你发的消息会自动以语音播出（send_message 即可，无需专用工具）。
    """
    try:
        from agent.tts import realtime_voice

        engine = get_realtime_engine()
        session = engine.active_session()
        return _dump({
            **engine.status(),
            "mode": get_config("realtime_mode", "cascade"),
            "barge_in": get_config("realtime_barge_in", True),
            "enabled": get_config("realtime_enabled", True),
            "default_voice": realtime_voice(),
            "last_say_error": session.last_say_error if session else "",
            "note": "通话中的消息自动语音播出；多连接同开通话时状态作用于最早建立的会话",
        })
    except Exception as e:
        return error_from_exception(e, action="查看实时语音状态")
