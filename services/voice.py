"""语音服务 — web 层与 agent/voice、agent/realtime 模块之间的门面。

两条语音路径共用连接上下文（用户/会话/落点频道封装的 VoiceDelivery）：
- 成段语音（utterance）：WS 帧 → VAD 成段 → sink 端口 → AgentApp 统一入口
  （wiring 施绑 deliver_utterance）；
- 实时对话（realtime）：WS 帧 → 端点检测/流式 ASR → 统一入口思维 →
  TTS → 下行音频帧（sink 由 WS 连接层提供）。

VoiceLeaseBusy 经本模块再导出（web/routers 不得直接依赖 agent，经 services 收口）。
"""

from __future__ import annotations

from typing import Any, Optional

from agent.realtime import RealtimeSink, get_realtime_engine
from agent.voice import VoiceDelivery, VoiceLeaseBusy, get_voice_manager
from core.audio_frames import AudioFrame

__all__ = ["RealtimeSink", "VoiceLeaseBusy", "VoiceService", "get_voice_service"]


class VoiceService:
    """语音会话的 services 层门面（WS 路由只与本类交互）。"""

    async def start(
        self,
        connection_id: str,
        *,
        sample_rate: int,
        user_id: str,
        user_name: str = "用户",
        chat_id: str = "",
        adapter_key: str = "webui",
    ) -> None:
        """开启语音会话（租约冲突抛 VoiceLeaseBusy，路由层转错误帧）。

        麦克风单会话纪律：实时会话进行中同样拒绝（两条路径共用一路麦克风）。
        """
        if get_realtime_engine().owns(connection_id):
            raise VoiceLeaseBusy("实时语音会话进行中（先结束通话再开始录音）")
        get_voice_manager().start_session(
            owner=connection_id,
            connection_id=connection_id,
            sample_rate=sample_rate,
            delivery=VoiceDelivery(
                user_id=user_id, user_name=user_name,
                session_id=chat_id, adapter_key=adapter_key,
            ),
        )

    async def accept_frame(self, connection_id: str, frame: AudioFrame) -> None:
        """转发一条音频帧（实时会话优先；无租约时 manager 内静默丢弃）。"""
        if get_realtime_engine().owns(connection_id):
            await get_realtime_engine().accept_pcm(connection_id, frame.pcm)
            return
        await get_voice_manager().accept_frame(connection_id, connection_id, frame)

    async def end(self, connection_id: str) -> None:
        """结束语音会话（实时会话优先收尾；成段会话缓冲成段交付）。"""
        if get_realtime_engine().owns(connection_id):
            await get_realtime_engine().stop(connection_id)
            return
        await get_voice_manager().end_session(connection_id, connection_id)

    async def drop_connection(self, connection_id: str) -> None:
        """连接断开清理：实时会话进宽限窗口（同用户重连即重挂续命，超窗
        收线），成段路径立即收尾（已录语音不丢）。"""
        await get_realtime_engine().handle_disconnect(connection_id)
        await get_voice_manager().drop_connection(connection_id)

    # ------------------------------------------------------------------
    # 实时对话
    # ------------------------------------------------------------------

    async def start_realtime(
        self,
        connection_id: str,
        *,
        sink: Any,
        sample_rate: int,
        user_id: str,
        user_name: str = "用户",
        chat_id: str = "",
        adapter_key: str = "webui",
    ) -> None:
        """开启实时语音会话（租约冲突抛 VoiceLeaseBusy；sink 由连接层提供）。

        麦克风单会话纪律：成段录音进行中同样拒绝。
        """
        if get_voice_manager().owns(connection_id, connection_id):
            raise VoiceLeaseBusy("语音录音进行中（先结束录音再开始通话）")
        await get_realtime_engine().start(
            owner=connection_id,
            delivery=VoiceDelivery(
                user_id=user_id, user_name=user_name,
                session_id=chat_id, adapter_key=adapter_key,
            ),
            sink=sink,
            sample_rate=sample_rate,
        )


_voice_service: Optional[VoiceService] = None


def get_voice_service() -> VoiceService:
    """进程内单例。"""
    global _voice_service
    if _voice_service is None:
        _voice_service = VoiceService()
    return _voice_service
