"""语音合成核心能力 — 提供者接口、朗读文本工程、句级流式管线。

划分纪律（核心由 Agent 集成，业务由实体组件承担）：
- providers.py：流式 TTS 接口与注册表（优先级链 + 运行时失败降级），
  具体实现（MiniMax/OpenAI 风格/edge-tts）以组件形式从实体经
  entities._sdk.register_tts_provider 注册；
- sentences.py：流式断句（CJK 标点/软切/短尾合并）与朗读清洗
  （markdown/旁白剥离、CJK 空格规范化）；
- pipeline.py：句级流式管线（文本增量 → 断句 → 预取合成 → 有序 PCM）；
- presets.py：音色预设注册表（AI 与 Web 共用的音色库 + 场景指派）；
- voice.py：音色解析（全部合成入口的单一音色决策链）；
- decode.py：压缩音频流 → PCM16 流（ffmpeg 管道，组件共用）。
"""

from agent.tts.pipeline import TtsPipeline
from agent.tts.presets import VoicePreset, assign_voice, list_presets
from agent.tts.providers import TtsProvider, TtsStream, get_tts_registry, synthesize_sentence
from agent.tts.sentences import SentenceSplitter, strip_for_speech
from agent.tts.voice import (
    default_preset,
    default_voice,
    realtime_preset,
    realtime_voice,
    resolve_voice,
)
from core.config import register_configs_safe

__all__ = [
    "SentenceSplitter",
    "TtsPipeline",
    "TtsProvider",
    "TtsStream",
    "VoicePreset",
    "assign_voice",
    "default_preset",
    "default_voice",
    "get_tts_registry",
    "list_presets",
    "realtime_preset",
    "realtime_voice",
    "resolve_voice",
    "strip_for_speech",
    "synthesize_sentence",
]

# 核心配置项：分组名 tts，配置中心与声音页签自动可见
register_configs_safe({
    "tts": {
        "tts_sentence_max_chars": {
            "description": "流式断句的单句字数上限（超出在逗号/顿号等软切点截断）",
            "default": 120, "unit": "字", "min": 20, "max": 500, "advanced": True,
        },
        "tts_prefetch_sentences": {
            "description": "TTS 预取句数（当前句播放时并行合成的后续句数，越大首屏越快越费配额）",
            "default": 2, "unit": "句", "min": 1, "max": 6, "advanced": True,
        },
        "tts_sentence_timeout_s": {
            "description": "单句合成超时（提供者/解码挂起时跳过该句继续后续句）",
            "default": 20, "unit": "秒", "min": 5, "max": 120, "advanced": True,
        },
        "tts_strip_narration": {
            "description": "朗读时剥离旁白（星号/括号包裹的动作神态描写不朗读）",
            "default": True,
        },
    },
})
