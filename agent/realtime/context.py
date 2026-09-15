"""实时通话上下文注入 — 通话进行期间向 volatile 层同步频道实时状态。

呈现形态归频道：AI 统一经 send_message 发消息，通话会话自动语音播出。
无通话时零注入（不占任何 token）；通话中注入当前挂载的通话频道信息
（频道/会话/状态）与自动播报说明，让 AI 对"发出去的话会被说出来"
有充分预期。
"""

from __future__ import annotations

from entities._sdk import context_provider

from .engine import get_realtime_engine


@context_provider(
    name="realtime_call", priority=15, max_tokens=120,
    group="voice", inject_key="realtime_context_inject",
)
class RealtimeCallProvider:
    """通话期间注入：挂载的通话频道信息 + 自动语音播报说明。"""

    async def provide(self, scope: str):
        engine = get_realtime_engine()
        session = engine.session_for_scope(scope)
        if session is None or session.closed:
            return None
        state = {
            "listening": "收听中",
            "thinking": "思考中（本轮回复生成中）",
            "speaking": "播报中",
        }.get(session.state.value, session.state.value)
        from core.context_provider import ProviderSnapshot

        return ProviderSnapshot(
            f"[实时通话] 频道={session.delivery.adapter_key} 会话={scope} "
            f"状态={state}——用户正在电话上：你经 send_message 发往该会话的"
            "消息会自动以语音播出（文字同时落聊天记录），正常回复即可；"
            "用户说话中不插播，消息以文字送达。可用 realtime_status 查看通话详情")
