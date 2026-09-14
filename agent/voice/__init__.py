"""语音会话管理 — 独立可插拔的音频接入模块。

职责边界（模块自包含，交付经 LateBinding 端口解耦）：
- 会话租约：同一 owner 同时只允许一条语音连接（MicLease 式所有权，
  新连接发起语音不会掐断、也不会双写——忙时显式拒绝）；
- 端点检测（VAD）：基于 PCM16 帧能量的静音判据，说完自动收束成段，
  有看门狗兜底（最后一帧之后不再有新帧也能按时 finalize）；
- 成段交付：PCM → WAV 落盘 workspace/uploads/voice/，连同投递上下文
  （VoiceDelivery）交给 voice_sink_port——组合根施绑的 deliver_utterance
  经 AgentApp 统一入口转 Everything 进入消息管线，与频道语音消息同路径。

与实时语音的关系：本模块是"接口铺垫"——会话/帧/成段/租约的边界即未来
Realtime 管线的挂点；接 Realtime 后端口改绑流式 ASR，会话与协议层不变。
"""

from agent.voice.session import (
    VoiceDelivery,
    VoiceLeaseBusy,
    VoiceSessionManager,
    VoiceSink,
    VoiceUtterance,
    get_voice_manager,
    voice_sink_port,
)

__all__ = [
    "VoiceDelivery",
    "VoiceLeaseBusy",
    "VoiceSessionManager",
    "VoiceSink",
    "VoiceUtterance",
    "get_voice_manager",
    "voice_sink_port",
]
