"""视觉能力指南：面向 AI 的能力矩阵（工具选型/参数说明/调用示例）。

vision_config(action="capabilities") 合并此静态指南与实时提供者状态输出，
让 AI 一次调用即可获知"当前能用哪些视觉能力、该调哪个工具、怎么传参"。
新增能力/工具时在此登记。
"""

from __future__ import annotations

from typing import Any, Dict

VISUAL_CAPABILITY_GUIDE: Dict[str, Dict[str, Any]] = {
    "understand": {
        "tools": ["recognize_image", "recognize_video"],
        "summary": "识别/分析图片或视频内容（本地路径或 URL）",
        "key_params": {
            "image_path/video_path": "图片/视频路径或 URL（必填）",
            "prompt": "分析指令，如'描述图片中的文字'（可选）",
        },
        "example": 'recognize_image(image_path="workspace/uploads/image/xxx.png", prompt="描述图片")',
        "notes": "主模型有视觉时本地图片直接回注原图（不走识别链，更快）；无视觉时按视觉能力链识别。"
                 "视频始终走识别链，仅投送声明 supports_video 的模型",
    },
    "image_gen": {
        "tools": ["generate_image"],
        "summary": "文生图；reference_image 非空=人物参考图生图（仅支持的组件提供者）",
        "key_params": {
            "prompt": "图片描述（必填）",
            "image_size": "像素 1024x1024 或比例 1:1/16:9/9:16（留空用默认配置）",
            "n": "数量 1~9（仅支持的组件提供者生效）",
            "reference_image": "人物参考照片路径或 URL",
            "style": "风格预设名（vision_style_presets 配置）或自定义描述",
        },
        "example": 'generate_image(prompt="猫耳女仆", style="nekomimi_maid", image_size="1024x1024")',
        "notes": "产物落盘 workspace/uploads/image/",
    },
    "image_edit": {
        "tools": ["edit_image"],
        "summary": "按文字指令编辑已有图片",
        "key_params": {"image_path": "待编辑图片路径或 URL（必填）", "prompt": "编辑指令（必填）"},
        "example": 'edit_image(image_path="workspace/uploads/image/xxx.png", prompt="把背景换成海边")',
        "notes": "按视觉能力链路由到 image_edit 类型模型",
    },
    "video": {
        "tools": ["generate_video", "query_video_task", "list_video_tasks", "cancel_video_task"],
        "summary": "文/图生视频与任务管理",
        "key_params": {
            "generate_video": "prompt + first_frame_image/last_frame_image/subject_reference（可选）+ duration/resolution/ratio",
            "query_video_task": "task_id + download（默认自动下载）",
        },
        "example": 'generate_video(prompt="猫咪在雪地奔跑", duration=6, resolution="768P")',
        "notes": "时长/分辨率留空用默认配置；产物落盘 workspace/uploads/video/",
    },
}
