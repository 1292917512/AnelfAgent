"""屏幕源（Screen）实体 — 本机屏幕画面接入核心视觉框架的组件。

屏幕只是核心视觉能力（agent.vision）的一个来源组件：本实体把 mss 截屏
封装为 ScreenSource 注册进核心视觉源注册表，画面监视/判变/上下文注入/
AI 工具/Web 页签全部由核心层提供。其他视觉来源（摄像头、外部推送桥）
以同样方式组件化接入。

框架各发现机制对齐点：
- @entity: 注册 group（被 discover_entities 扫描时触发）
- entity_manifest: 自报展示信息（必传 group + order）
- register_configs_safe: 实体配置项（entity/screen 组，实体详情页配置 tab 展示）
"""

from core.config import register_configs_safe
from entities._sdk import entity, entity_manifest, register_vision_source

entity("screen", "屏幕源 - 本机屏幕画面接入核心视觉框架（截屏/盯屏的视觉源组件）")

entity_manifest(
    display_name="屏幕源",
    icon="monitor",
    description="本机屏幕画面组件：mss 截屏注册为核心视觉源，监视/注入/工具由核心视觉能力提供",
    version="1.0.0",
    order=47,
    group="screen",
)

register_configs_safe({
    "entity/screen": {
        "screen_monitor": {
            "description": "截屏显示器序号（0=全部合并，1=主屏）",
            "default": 1, "min": 0, "max": 8,
        },
    },
})

from .source import ScreenSource  # noqa: E402

register_vision_source(ScreenSource())
