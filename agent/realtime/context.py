"""实时通话上下文注入 — 通话进行期间向 volatile 层提醒应答纪律。

无通话时零注入（不占任何 token）；通话中注入一行状态与应答出口提示，
把"说给电话另一头"从 AI 的记忆问题变成系统的每轮提醒。
"""

from __future__ import annotations

from entities._sdk import context_provider

from .engine import get_realtime_engine


@context_provider(
    name="realtime_call", priority=15, max_tokens=120,
    group="voice", inject_key="realtime_context_inject",
)
class RealtimeCallProvider:
    """通话期间注入：会话状态 + "应答用 realtime_reply 说出来"的纪律提醒。"""

    async def provide(self, scope: str):
        engine = get_realtime_engine()
        session = engine.active_session()
        if session is None or session.closed:
            return None
        from core.context_provider import ProviderSnapshot

        return ProviderSnapshot(
            "[实时通话] 进行中——主人正在电话上，耳朵在听、看不到聊天框："
            "本轮应答用 realtime_reply 直接说出来（不要 send_message 发文字）；"
            "轮次外的主动开口用 realtime_say；可用 realtime_status 查看通话状态")
