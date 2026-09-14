"""声音能力指南：面向 AI 的能力矩阵（工具选型/参数说明/调用示例）。

sound_config(action="capabilities") 合并此静态指南与实时提供者状态输出。
新增能力/工具时在此登记。
"""

from __future__ import annotations

from typing import Any, Dict

SOUND_CAPABILITY_GUIDE: Dict[str, Dict[str, Any]] = {
    "asr": {
        "tools": ["voice_to_text"],
        "summary": "语音/音频文件转写为文字",
        "key_params": {"audio_source": "音频本地路径或 URL（必填）"},
        "example": 'voice_to_text(audio_source="workspace/uploads/voice/xxx.ogg")',
        "notes": "按声音能力链路由（本地组件优先，云端 asr 模型链兜底）",
    },
    "tts": {
        "tools": ["text_to_voice"],
        "summary": "文字转语音，产物落盘 workspace/uploads/audio/",
        "key_params": {
            "text": "待合成文本（必填，>3000 字自动异步）",
            "voice": "预置音色 ID（留空用默认音色，list_voices 可查）",
            "emotion": "情绪（MiniMax 协议）：happy/sad/angry/calm 等",
            "speed": "语速 0.5~2.0，0=默认",
            "pitch": "语调 -12~12（MiniMax 协议）",
            "reference_audio+reference_text": "声音克隆（仅 models 链 OpenAI 风格协议）",
        },
        "example": 'text_to_voice(text="你好", voice="male-qn-qingse", emotion="happy")',
        "notes": "默认音色可用 sound_config(set, default_voice, ...) 修改",
    },
    "voice_mgmt": {
        "tools": ["clone_voice", "design_voice", "list_voices", "delete_voice"],
        "summary": "音色复刻/设计/查询/删除",
        "key_params": {
            "clone_voice": "audio_path（本地或 URL）+ voice_id（自定义）",
            "design_voice": "prompt（音色描述）+ preview_text（可选）",
            "list_voices": "voice_type: system/voice_cloning/voice_generation/all",
        },
        "example": 'design_voice(prompt="低沉磁性的悬疑旁白男声") → sound_config("set", "default_voice", <voice_id>)',
        "notes": "创建音色后可经 sound_config 设为默认音色，完成自助换装",
    },
    "music": {
        "tools": ["generate_music", "generate_lyrics"],
        "summary": "音乐/歌曲生成与歌词创作",
        "key_params": {
            "generate_music": "lyrics（歌曲必填）或 prompt+is_instrumental（纯音乐）",
            "generate_lyrics": "prompt（主题）或 mode=edit + lyrics",
        },
        "example": 'generate_lyrics(prompt="夏日晚风") → generate_music(lyrics=<上一步歌词>, prompt="清新流行")',
        "notes": "歌词与音乐可串联调用，产物落盘 workspace/uploads/music/",
    },
}
