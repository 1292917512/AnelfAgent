"""语音段投递：把成段语音接入消息统一入口（AgentApp → Everything）。

这是 agent/voice 的默认 sink 实现，由组合根 agent/runtime/wiring.py
施绑到 voice_sink_port。语音段以 VOICE MessageSegment 经
AgentApp.send_message 进入统一消息管线（Everything → pipeline → Mind），
与频道收到的语音消息走完全相同的路径：[media_type:voice] 标签入库、
media:voice 工具链（转写/声纹）按既有规则激活——本模块不发明第二条
语音处理路径。
"""

from __future__ import annotations

from agent.channel.schemas import MessageSegment, SegmentType
from agent.voice.session import VoiceUtterance
from core.log import log


async def deliver_utterance(utterance: VoiceUtterance) -> None:
    """把语音段作为 VOICE 媒体段送入其来源会话（统一入口投递）。"""
    from agent.runtime.agent_app import get_agent_app

    delivery = utterance.delivery
    segment = MessageSegment(
        type=SegmentType.VOICE,
        file_path=utterance.file_path,
        file_name=utterance.file_path.rsplit("/", 1)[-1],
        mime_type="audio/wav",
        duration=utterance.duration_ms / 1000,
    )
    await get_agent_app().send_message(
        user_id=delivery.user_id,
        content="",
        user_name=delivery.user_name,
        to_me=True,
        media_segments=[segment],
        adapter_key=delivery.adapter_key,
        session_id=delivery.session_id,
    )
    log(
        f"语音段已投递: {utterance.duration_ms:.0f}ms → "
        f"{delivery.adapter_key}:{delivery.user_id}",
        "DEBUG", tag="语音",
    )
