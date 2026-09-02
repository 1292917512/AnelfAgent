"""SillyTavern 酒馆实体：把本机酒馆服务纳管为 AI 可管理的"实体"。

- 生命周期：start/stop/restart/status（进程组管理 + state.json 持久化）
- 源码管理：嵌套 git 仓库的更新（pull）与二次开发提交（commit+push）
- 酒馆桥接：角色卡增删改查、模型配置读写、聊天记录读取（HTTP API）
- 动态注入：酒馆运行时向 AI 上下文注入状态卡片，关闭时零注入

目录名 / group / 路由名统一为 sillytavern；酒馆官方源码嵌套在
./SillyTavern/（独立 git 仓库，已被 .gitignore 忽略）。
"""

from __future__ import annotations

from entities._sdk import entity, entity_manifest

entity(
    "sillytavern",
    "SillyTavern 酒馆管理 - 启动/停止/重启、源码更新与二次开发提交、"
    "角色卡管理、模型配置、聊天记录；运行状态动态注入 AI 上下文",
)

entity_manifest(
    display_name="酒馆 SillyTavern",
    icon="castle",
    description="把本机 SillyTavern 纳管为实体：进程生命周期、git 更新/提交、"
                "角色卡与模型配置桥接、运行状态动态上下文注入",
    version="1.0.0",
    order=30,
    nav={"path": "/entities/sillytavern", "label": "sillytavern", "nav_group": "group_ability"},
    group="sillytavern",
)

from . import (
    context,  # noqa: E402,F401  注册动态上下文提供者
    tools,  # noqa: E402,F401  触发 @tool 注册
)


def register_lifecycle() -> None:
    """bootstrap 就绪后调用：auto_start 开启时后台拉起酒馆。"""
    from . import service
    service.autostart_if_enabled()
