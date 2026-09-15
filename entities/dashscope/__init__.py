"""DashScope 实体 — 阿里百炼语音能力接入核心路由的组件包。

四类组件：
- 流式 ASR（fun-asr-realtime / qwen-audio-asr-*-streaming）→ 核心
  asr_stream 注册表（实时通话级联链）；
- 非流式 ASR（qwen-audio-3.0-asr-flash）→ 核心 asr 注册表（音频转写链）；
- 流式 TTS（CosyVoice / Qwen-TTS，PCM 直出）→ 核心 TTS 注册表
  （实时通话播放链与句级预取管线共用）；
- 声音能力组件（一次性合成 + 音色管理：复刻/列表/删除）→ 声音能力路由。

依赖官方 dashscope SDK（可选依赖：未安装或未解析到 api_key 时组件整体
不可用，各链自动沿用其余提供者）。密钥解析顺序：dashscope_api_key
配置 → llm_clients 中 dashscope 兼容提供者的 key → DASHSCOPE_API_KEY
环境变量。配置经统一配置体系（entity/dashscope 组，配置中心可搜可改）。
"""

from core.config import register_configs_safe
from entities._sdk import entity, entity_manifest

entity("dashscope", "阿里百炼 - 流式/非流式语音识别、CosyVoice/Qwen-TTS 流式合成、音色复刻的语音组件包")

entity_manifest(
    display_name="阿里百炼语音",
    icon="cloud",
    description="阿里百炼语音组件：流式识别（实时通话）、音频转写、CosyVoice/Qwen-TTS "
                "流式合成、音色复刻与音色管理",
    version="1.0.0",
    order=32,
    group="dashscope",
)

register_configs_safe({
    "entity/dashscope": {
        "dashscope_api_key": {
            "description": "阿里百炼 API Key（留空自动复用 llm_clients 中 dashscope 兼容提供者的 Key 或环境变量）",
            "default": "",
            "password": True,
        },
        "dashscope_stream_asr_model": {
            "description": "流式识别模型（实时通话）：fun-asr-realtime / qwen-audio-3.0-asr-flash-streaming",
            "default": "fun-asr-realtime",
        },
        "dashscope_stream_asr_priority": {
            "description": "流式识别提供者在 asr_stream 链中的优先级（小值优先；本地 FunASR 默认 10）",
            "default": 20,
            "advanced": True,
        },
        "dashscope_asr_model": {
            "description": "非流式识别模型（音频转写，Recognition 家族）：fun-asr-realtime 等",
            "default": "fun-asr-realtime",
        },
        "dashscope_asr_priority": {
            "description": "非流式识别提供者在 asr 链中的优先级（小值优先；本地 FunASR 默认 10）",
            "default": 20,
            "advanced": True,
        },
        "dashscope_tts_model": {
            "description": "合成模型：qwen-audio-3.0-tts-plus / qwen-audio-3.0-tts-flash / cosyvoice-v3.5-plus 等",
            "default": "qwen-audio-3.0-tts-plus",
        },
        "dashscope_tts_voice": {
            "description": "默认音色（系统音色名或复刻音色 ID；qwen-audio 系如 longanhuan_v3.6）",
            "default": "longanhuan_v3.6",
        },
        "dashscope_tts_priority": {
            "description": "合成提供者在 TTS 链中的优先级（小值优先；MiniMax 默认 10/15）",
            "default": 20,
            "advanced": True,
        },
        "dashscope_clone_upload_url": {
            "description": "音色复刻本地文件的公网上传端点（复刻 API 需可公网访问的音频 URL；"
                           "源本身就是 URL 时无需上传直用）",
            "default": "",
            "advanced": True,
        },
    },
})


def register_components() -> None:
    """向各核心路由注册百炼语音组件（实体导入时调用；同名覆盖幂等）。"""
    from entities._sdk import register_audio_provider, register_sound_provider, register_tts_provider

    from .asr import DashScopeAsrProvider, DashScopeStreamAsrProvider
    from .tts import DashScopeTtsProvider
    from .voice import DashScopeSoundProvider

    register_audio_provider(DashScopeAsrProvider())
    register_audio_provider(DashScopeStreamAsrProvider())
    register_tts_provider(DashScopeTtsProvider())
    register_sound_provider(DashScopeSoundProvider())


register_components()
