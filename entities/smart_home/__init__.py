"""智能家居实体 — 家居设备状态感知与控制的统一接入。

以 Home Assistant 为设备来源（WebSocket 实时同步），双层组件化架构：
连接层平台自治于 ``providers/``（@home_provider 注册），设备类型域组件
自治于 ``domains/``（@device_domain 注册，删除文件即整体拔出）；
context.py 把启用域的设备状态聚合为 [智能家居] 块注入 volatile 层；
Web 面板与 AI 工具共用 framework/manager 的注册表与缓存。

框架各发现机制对齐点：
- @entity: 注册 group（被 discover_entities 扫描 tools.py 时触发）
- entity_manifest: 自报展示信息
- entity_config: 实体配置项（config.json 生命周期托管，详情页配置 tab 展示）
- ConfigManager 监听器: 连接配置变更自动重连
- register_lifecycle: manager 托管（启动连接 / 关停断开）
- router.py: build_router()（挂载到 /api/entity/smart_home）
- panel.tsx: 设备控制面板（被 entity-panels glob 发现）
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.config import ConfigManager
from entities._sdk import entity, entity_config, entity_manifest

from . import framework
from .domains import load_domains
from .manager import get_smart_home_manager
from .providers import all_providers, load_providers
from .providers import config_entries as provider_config_entries

entity("smart_home", "智能家居 - 家居设备状态感知与控制（Home Assistant 接入，灯光/空调/窗帘等设备域组件可插拔）")

entity_manifest(
    display_name="智能家居",
    icon="home",
    description="接入 Home Assistant 的智能家居统一控制（设备状态上下文注入 + AI 控制工具 + 实时控制面板）",
    version="1.0.0",
    order=42,
    group="smart_home",
)

load_domains()
load_providers()

entity_config({
    "entity/smart_home": {
        "smart_home_context_inject": {
            "description": "是否向 AI 上下文注入智能家居设备状态（总开关，关闭后全部设备域停止注入）",
            "default": True,
        },
        **framework.config_entries(),
        **provider_config_entries(),
    },
})


def _handle_config_changed(key: str, _value: Any) -> None:
    """供应商连接配置变更时调度对应 provider 重连（仅在事件循环内生效）。"""
    if not key.startswith("smart_home_"):
        return
    # 配置键形态 smart_home_<provider_key>_<field>，按注册 provider 前缀匹配
    provider_key = ""
    for provider in all_providers():
        if key.startswith(f"smart_home_{provider.key}_"):
            provider_key = provider.key
            break
    if not provider_key:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(get_smart_home_manager().reconnect(provider_key))


# 连接配置（url/token/账号等）被修改（Web/AI/配置中心任一路径）时自动重连
# 对应 provider，域配置项经热读取生效无需处理
ConfigManager.add_listener("smart_home_", _handle_config_changed)


def register_lifecycle() -> None:
    """向 Lifecycle 注册 manager（启动连接 / 关停断开）。"""
    from core.lifecycle import Lifecycle

    manager = get_smart_home_manager()
    Lifecycle.register(
        "smart_home_manager", manager,
        on_start=manager.start, cleanup=manager.close,
    )


from . import context, tools  # noqa: F401, E402  # 注册上下文提供者 + 触发 @tool 注册
