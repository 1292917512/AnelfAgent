"""实时语音核心能力 — 全双工语音对话。

模块划分：
- session.py：会话状态机（LISTENING/THINKING/SPEAKING）、轮次令牌、
  barge-in 打断、播放写任务；
- playback.py：边界连续重采样与有序播放队列（audio_chunk/audio_done
  帧对协议）；
- engine.py：会话注册与帧路由、级联管线驱动（端点检测 → 流式 ASR →
  统一入口思维 → 回复增量 TTS → 下行音频帧）；
- native/：提供方原生实时语音客户端（OpenAI Realtime / Gemini Live /
  千问实时语音三方言，realtime_mode=native 时启用）。
"""

from agent.realtime.engine import RealtimeEngine, get_realtime_engine
from agent.realtime.session import CallMode, RealtimeSession, RealtimeSink, SessionState
from core.config import register_configs_safe

__all__ = [
    "CallMode",
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
            "description": "原生模式的提供方：openai=OpenAI Realtime / "
                           "gemini=Gemini Live / qwen=千问实时语音对话（百炼，"
                           "凭据用组件凭据 dashscope）",
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
        "realtime_qwen_asr_enabled": {
            "description": "级联模式的流式 ASR 优先走千问实时转写（"
                           "qwen3-asr-flash-realtime WS 长连接）；关闭或端点凭据"
                           "缺失时回退 FunASR 滚动窗",
            "default": True,
        },
        "realtime_qwen_asr_ws_base": {
            "description": "千问实时转写 WS 端点（默认百炼官方；token-plan 等套餐"
                           "端点填 wss://<host>/api-ws/v1/realtime，凭据自动匹配 "
                           "llm_clients 同 host 供应商的 key）",
            "default": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
            "advanced": True,
        },
        "realtime_speaker_annotate": {
            "description": "语音轮次是否附说话人标注（声纹只读识别，标记谁在说话，"
                           "识别与应对方式由 AI 自行决定）",
            "default": True,
        },
        "realtime_echo_filter_enabled": {
            "description": "回声内容过滤：用户轮转写被近期 AI 播报文本高度覆盖时判定为"
                           "扬声器回声丢弃（防 AI 被自己的声音激发出回声轮）",
            "default": True,
        },
        "realtime_echo_filter_seconds": {
            "description": "回声比对的播报回溯窗口（只与该窗口内播出的文本比对）",
            "default": 45.0, "unit": "秒", "min": 5.0, "max": 180.0, "advanced": True,
        },
        "realtime_echo_filter_threshold": {
            "description": "回声覆盖度阈值（转写被播报文本覆盖的比例，容错少量转写错字；"
                           "调低更激进、调高更保守）",
            "default": 0.8, "min": 0.5, "max": 1.0, "advanced": True,
        },
        "realtime_keep_recordings": {
            "description": "是否留存轮次录音（workspace/uploads/realtime/ 按日分目录；"
                           "留存后实时片段在音源库可回听/订正/重转写）",
            "default": True,
        },
        "realtime_recording_retention_days": {
            "description": "轮次录音保留天数（到期随下次留存惰性清理，0 = 不清理）",
            "default": 7, "unit": "天", "min": 0, "max": 365, "advanced": True,
        },
        "realtime_context_inject": {
            "description": "通话期间向 AI 注入通话状态与应答节奏训诫（先应声再干活）",
            "default": True,
            "advanced": True,
        },
        "realtime_native_instructions": {
            "description": "原生模式的系统指令（注入提供方实时会话的人格简述；留空用默认）",
            "default": "",
        },
        "realtime_playback_rate": {
            "description": "下行播放采样率（客户端播放设备率，TTS 产出自动重采样对齐）",
            "default": 48000, "unit": "Hz", "advanced": True,
        },
        "realtime_reconnect_grace_seconds": {
            "description": "连接断开后的会话保留窗口：同一用户在此窗口内重新"
                           "voice_start 即重挂续命（轮次/播放/挂起回复保留），"
                           "超窗自动收线；0 = 断开立即收线",
            "default": 5.0, "unit": "秒", "min": 0.0, "max": 60.0, "advanced": True,
        },
    },
})
