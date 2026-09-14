"""实时语音核心能力 — 全双工语音对话。

模块划分：
- session.py：会话状态机（LISTENING/THINKING/SPEAKING）、轮次令牌、
  barge-in 打断、播放写任务；
- playback.py：边界连续重采样与有序播放队列（audio_chunk/audio_done
  帧对协议）；
- engine.py：会话注册与帧路由、级联管线驱动（端点检测 → 流式 ASR →
  统一入口思维 → 回复增量 TTS → 下行音频帧）；
- native/：提供方原生实时语音客户端（OpenAI Realtime / Gemini Live
  双方言，realtime_mode=native 时启用）。
"""

from agent.realtime.engine import RealtimeEngine, get_realtime_engine
from agent.realtime.session import RealtimeSession, RealtimeSink, SessionState
from core.config import register_configs_safe

__all__ = [
    "RealtimeEngine",
    "RealtimeSession",
    "RealtimeSink",
    "SessionState",
    "get_realtime_engine",
]

# 核心配置项：分组名 realtime，配置中心与声音页签自动可见
register_configs_safe({
    "realtime": {
        "realtime_enabled": {
            "description": "是否启用实时语音对话（全双工语音会话）",
            "default": True,
        },
        "realtime_mode": {
            "description": "实时语音模式：cascade=级联（端点检测+ASR+思维+TTS，"
                           "人格/记忆/工具全量生效）/ native=提供方原生实时语音",
            "default": "cascade",
        },
        "realtime_native_provider": {
            "description": "原生模式的提供方：openai=OpenAI Realtime / gemini=Gemini Live",
            "default": "openai",
        },
        "realtime_barge_in_onset_ms": {
            "description": "打断确认时长：播放/思考中需持续语音这么久才判定真打断"
                           "（防扬声器回声的短促碎响误打断）",
            "default": 250, "unit": "ms", "min": 100, "max": 1500, "advanced": True,
        },
        "realtime_barge_in": {
            "description": "是否允许打断（播放/思考中开口即停当前轮重新收听）",
            "default": True,
        },
        "realtime_speaker_annotate": {
            "description": "语音轮次是否附说话人标注（声纹只读识别，标记谁在说话，"
                           "识别与应对方式由 AI 自行决定）",
            "default": True,
        },
        "realtime_tts_voice": {
            "description": "实时对话音色（留空用 TTS 默认音色）",
            "default": "",
        },
        "realtime_native_instructions": {
            "description": "原生模式的系统指令（注入提供方实时会话的人格简述；留空用默认）",
            "default": "",
        },
        "realtime_playback_rate": {
            "description": "下行播放采样率（客户端播放设备率，TTS 产出自动重采样对齐）",
            "default": 48000, "unit": "Hz", "advanced": True,
        },
    },
})
