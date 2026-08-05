"""媒体库能力指南：面向 AI 的能力矩阵（工具选型/参数说明/调用示例）。

media_config(action="capabilities") 合并此静态指南与实时 provider 状态输出，
让 AI 一次调用即可获知"当前能用哪些媒体能力、该调哪个工具、怎么传参"。
新增能力/工具时在此登记。
"""

from __future__ import annotations

from typing import Any, Dict

CAPABILITY_GUIDE: Dict[str, Dict[str, Any]] = {
    "vision": {
        "tools": ["recognize_image"],
        "summary": "识别/分析图片内容（本地路径或 URL）",
        "key_params": {
            "image_path": "图片路径或 URL（必填）",
            "prompt": "分析指令，如'描述图片中的文字'（可选）",
        },
        "example": 'recognize_image(image_path="workspace/uploads/image/xxx.png", prompt="描述图片")',
        "notes": "auto 链：视觉模型失败自动降级 MiniMax Coding Plan（订阅配额）；provider=minimax 直连不占视觉模型调用",
    },
    "asr": {
        "tools": ["voice_to_text"],
        "summary": "语音/音频转写为文字",
        "key_params": {"audio_source": "音频本地路径或 URL（必填）"},
        "example": 'voice_to_text(audio_source="workspace/uploads/voice/xxx.ogg")',
        "notes": "仅 models 链（asr 类型模型）",
    },
    "tts": {
        "tools": ["text_to_voice"],
        "summary": "文字转语音，产物落盘 workspace/uploads/audio/",
        "key_params": {
            "text": "待合成文本（必填，>3000 字自动异步）",
            "voice": "预置音色 ID（留空用媒体库默认音色，list_voices 可查）",
            "emotion": "情绪（MiniMax 协议）：happy/sad/angry/calm 等",
            "speed": "语速 0.5~2.0，0=默认",
            "pitch": "语调 -12~12（MiniMax 协议）",
            "reference_audio+reference_text": "声音克隆（仅 models 链 OpenAI 风格协议）",
        },
        "example": 'text_to_voice(text="你好", voice="male-qn-qingse", emotion="happy")',
        "notes": "默认音色可用 media_config(set, default_voice, ...) 修改",
    },
    "voice_mgmt": {
        "tools": ["clone_voice", "design_voice", "list_voices", "delete_voice"],
        "summary": "音色复刻/设计/查询/删除（MiniMax 协议）",
        "key_params": {
            "clone_voice": "audio_path（本地或 URL）+ voice_id（自定义）",
            "design_voice": "prompt（音色描述）+ preview_text（可选）",
            "list_voices": "voice_type: system/voice_cloning/voice_generation/all",
        },
        "example": 'design_voice(prompt="低沉磁性的悬疑旁白男声") → media_config("set", "default_voice", <voice_id>)',
        "notes": "创建音色后可经 media_config 设为默认音色，完成自助换装",
    },
    "music": {
        "tools": ["generate_music", "generate_lyrics"],
        "summary": "音乐/歌曲生成与歌词创作（MiniMax 协议）",
        "key_params": {
            "generate_music": "lyrics（歌曲必填）或 prompt+is_instrumental（纯音乐）",
            "generate_lyrics": "prompt（主题）或 mode=edit + lyrics",
        },
        "example": 'generate_lyrics(prompt="夏日晚风") → generate_music(lyrics=<上一步歌词>, prompt="清新流行")',
        "notes": "歌词与音乐可串联调用，产物落盘 workspace/uploads/music/",
    },
    "video": {
        "tools": ["generate_video", "query_video_task", "list_video_tasks", "cancel_video_task"],
        "summary": "文/图生视频与任务管理（MiniMax 协议）",
        "key_params": {
            "generate_video": "prompt + first_frame_image/last_frame_image/subject_reference（可选）+ duration/resolution/ratio",
            "query_video_task": "task_id + download（默认自动下载）",
        },
        "example": 'generate_video(prompt="猫咪在雪地奔跑", duration=6, resolution="768P")',
        "notes": "时长/分辨率留空用媒体库 defaults；产物落盘 workspace/uploads/video/",
    },
    "image_gen": {
        "tools": ["generate_image"],
        "summary": "文生图；reference_image 非空=人物参考图生图（仅 minimax 模块）",
        "key_params": {
            "prompt": "图片描述（必填）",
            "image_size": "像素 1024x1024 或比例 1:1/16:9/9:16（留空用媒体库默认）",
            "n": "数量 1~9（仅 minimax 模块）",
            "reference_image": "人物参考照片路径或 URL",
            "style": "风格预设名（媒体库 style_presets）或自定义描述",
        },
        "example": 'generate_image(prompt="猫耳女仆", style="nekomimi_maid", image_size="1024x1024")',
        "notes": "产物落盘 workspace/uploads/image/",
    },
    "image_edit": {
        "tools": ["edit_image"],
        "summary": "按文字指令编辑已有图片",
        "key_params": {"image_path": "待编辑图片路径或 URL（必填）", "prompt": "编辑指令（必填）"},
        "example": 'edit_image(image_path="workspace/uploads/image/xxx.png", prompt="把背景换成海边")',
        "notes": "仅 models 链（image_edit 类型模型）",
    },
    "rerank": {
        "tools": ["rerank_search"],
        "summary": "按相关性对文档列表重排序",
        "key_params": {"query": "查询语句", "documents": "JSON 字符串数组"},
        "example": 'rerank_search(query="安装教程", documents=\'["文档1","文档2"]\')',
        "notes": "仅 models 链（rerank 类型模型）",
    },
}
