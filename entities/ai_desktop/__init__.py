"""AI 桌面实体 — 面向 AI 的动态环境信息上下文注入。

以「桌面组件」形式向 AI 的 volatile 层实时注入常用环境信息（当前时间/
节日、配置地区天气等），供 AI 在每轮推理中自然感知，无需主动调用工具。

架构：组件以子包形式自治于 ``modules/<key>/``（数据层/服务层/组件类/内嵌
tests/ 同目录，删除子包即整体拔出零残留），``@desktop_module`` 装饰器注册、
``modules/`` 目录自动发现；framework 统一生成配置项（entity/ai_desktop 组，
配置中心/实体详情/AI 配置工具自动可见）与调度刷新；context.py 聚合为单一
context_provider 注入；Web 面板与 AI 工具共用 framework 的组件注册表。

框架各发现机制对齐点：
- @entity: 注册 group（被 discover_entities 扫描 tools.py 时触发）
- entity_manifest: 自报展示信息
- register_configs_safe: 组件配置项（实体详情页配置 tab + 配置中心自动展示）
- ConfigManager 监听器: 组件配置变更即时触发重采集（上下文秒级跟随配置）
- router.py: build_router()（挂载到 /api/entity/ai_desktop）
- panel.tsx: 组件管理面板（被 entity-panels glob 发现）
"""

from core.config import ConfigManager, register_configs_safe
from entities._sdk import entity, entity_manifest

from . import framework
from .modules import load_modules

entity("ai_desktop", "AI 桌面 - 动态环境信息上下文注入（时间/节日/天气等可插拔组件）")

entity_manifest(
    display_name="AI 桌面",
    icon="monitor",
    description="向 AI 动态上下文注入实时环境信息的可插拔组件平台（时间/节日/天气）",
    version="1.0.0",
    order=25,
    group="ai_desktop",
)

load_modules()

register_configs_safe({
    "entity/ai_desktop": {
        "ai_desktop_context_inject": {
            "description": "是否向 AI 上下文注入桌面环境信息（总开关，关闭后全部组件停止注入）",
            "default": True,
        },
        **framework.config_entries(),
    },
})

# 配置变更即时生效：组件配置被修改（Web/AI/配置中心任一路径）时清零刷新计时，
# 后台调度循环下一拍即重新采集，注入内容秒级跟随配置变化
ConfigManager.add_listener("ai_desktop_", framework.handle_config_changed)

from . import context, tools  # noqa: F401, E402  # 注册上下文提供者 + 触发 @tool 注册
