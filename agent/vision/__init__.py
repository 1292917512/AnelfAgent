"""视觉核心能力 — 可插拔视觉源框架、帧缓冲、监视循环、画面上下文注入。

划分纪律（核心由 Agent 集成，来源由实体组件承担）：
- framework.py：视觉源组件基类与注册表——具体来源（屏幕、摄像头、外部
  推送桥等）以组件形式从实体经 entities._sdk.register_vision_source 注册；
- buffer.py / watcher.py：帧缓冲（分源判变）与轮询监视循环；
- context.py / tools.py：AI 工具组（deferred，bootstrap 激活）与
  变化驱动的画面上下文注入；
- capture.py：帧数据契约与分块 dHash 变化检测算法。
"""

from agent.vision.buffer import VisionBuffer, VisionFrame, get_vision_buffer
from agent.vision.capture import CapturedFrame
from agent.vision.framework import VisualSource, all_sources, get_source, register_source
from agent.vision.watcher import VisionWatcher, get_vision_watcher
from core.config import register_configs_safe

__all__ = [
    "CapturedFrame",
    "VisionBuffer",
    "VisionFrame",
    "VisionWatcher",
    "VisualSource",
    "all_sources",
    "get_source",
    "get_vision_buffer",
    "get_vision_watcher",
    "register_source",
]

# 核心配置项：分组名 vision，配置中心与视觉页签配置 tab 自动可见
register_configs_safe({
    "vision": {
        "vision_context_inject": {
            "description": "是否向 AI 上下文注入视觉源状态与最新画面（变化驱动）",
            "default": True,
        },
        "vision_watch_interval_s": {
            "description": "视觉监视捕获间隔",
            "default": 5, "unit": "s", "min": 1, "max": 60,
        },
        "vision_change_cell_threshold": {
            "description": "画面变化判定：单格 dHash 汉明距离阈值（低于视为噪声）",
            "default": 3, "min": 0, "max": 64, "advanced": True,
        },
        "vision_change_ratio": {
            "description": "画面变化判定：变化格占比阈值（4x4 网格，默认 0.1=超 10% 格子变化才算内容变化，光标级单格 6.25% 属噪声）",
            "default": 0.1, "min": 0.0, "max": 1.0, "advanced": True,
        },
        "vision_disabled_sources": {
            "description": "停用的视觉源（逗号分隔 key；停用后监视停止、AI 与 Web 均不可取帧）",
            "default": "",
        },
        "vision_provider_priority": {
            "description": "视觉能力提供者优先级链（JSON 字典 {能力: [提供者]}，能力: understand/image_gen/image_edit/video；留空自动）",
            "default": {},
            "value_type": "json",
            "advanced": True,
        },
        "vision_default_image_size": {
            "description": "图片生成默认尺寸（像素 1024x1024 或比例 1:1/16:9/9:16）",
            "default": "1024x1024",
        },
        "vision_default_video_resolution": {
            "description": "视频生成默认分辨率（留空用模型默认；如 2K/768P/1080P）",
            "default": "",
        },
        "vision_default_video_duration": {
            "description": "视频生成默认时长（0=用模型默认）",
            "default": 0,
            "unit": "s", "min": 0, "max": 60,
        },
        "vision_style_presets": {
            "description": "图像/视频生成风格预设（JSON 字典 {预设名: 风格描述}，生成工具 style 参数引用）",
            "default": {},
            "value_type": "json",
        },
    },
})
