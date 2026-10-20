"""
实体系统 — 自动发现并加载所有实体模块。

每个实体是一个子目录，包含 ``tools.py`` 文件，
使用 ``@tool`` 装饰器和 ``entity()`` 声明注册到 ``EntityRegistry``。
导入本模块的 ``discover_entities()`` 即可触发全部实体注册。
运行时的目录增删与代码热更由 ``entities/hotplug.py`` 的 ``sync_entities()``
对账完成（Web 刷新入口 / 目录监听自动触发）。

实体自治规范（Entity Autonomy）：
- ``entities/<name>/router.py:build_router()`` → 自动挂载到 ``/api/entity/<name>``
- ``entities/<name>/__init__.py:register_lifecycle()`` → bootstrap 自动调用
- ``entities/<name>/panel.tsx`` → 前端代码生成接入（web/frontend/scripts/module-links.mjs 扫描生成 src/generated 接入表，@entities 别名直引）
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.log import log

from . import hotplug as _hotplug

# 兼容历史导出（归属记录与回收实现已迁移至 entities/hotplug.py）
_loaded_modules = _hotplug._loaded_modules


def discover_entities() -> List[str]:
    """扫描 entities/ 下所有子目录，导入 tools.py 触发 @tool 注册。

    返回成功加载的实体模块名列表。
    """
    loaded: List[str] = []
    failed: List[str] = []

    for name in sorted(_hotplug.scan_entity_dirs()):
        if _hotplug.load_entity(name):
            _hotplug._loaded_modules.add(name)
            loaded.append(name)
        else:
            failed.append(name)

    if loaded:
        log(f"entities loaded: {', '.join(loaded)} ({len(loaded)})")
    if failed:
        log(f"entities failed: {', '.join(failed)} ({len(failed)})", "WARNING")

    # 已安装插件的负载激活（技能/工具/MCP server）——在内置实体扫描后进行，
    # 保证插件管理实体自身已注册，单个插件失败不阻断启动
    try:
        from entities.plugins.tools import activate_installed
        activate_installed()
    except Exception as e:
        log(f"插件激活阶段异常: {e}", "WARNING")
    return loaded


async def discover_entity_lifecycles() -> int:
    """扫描并调用所有实体的 ``register_lifecycle()``，返回注册成功的实体数。

    基础存储就绪后、消费存储的 worker 注册前调用，
    保证宿主逆序关停时先停止消费，再关闭资源。
    实体没有该函数则跳过；调用失败仅 WARNING 不中断启动。
    """
    registered = 0
    entity_dir = _hotplug.scan_entity_dirs() & _hotplug._loaded_modules
    for name in sorted(entity_dir):
        if await _hotplug.register_entity_lifecycle(name):
            registered += 1
    if registered:
        log(f"实体 lifecycle 注册完成: {registered} 个", tag="实体")
    return registered


async def reload_entities(reload_existing: bool = True) -> Dict[str, Any]:
    """热同步实体：对账 entities/ 目录，新增热插入、消失热拔除、存续热重载。"""
    return await _hotplug.sync_entities(reload_existing=reload_existing)
