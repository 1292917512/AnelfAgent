"""实体目录热插拔 — entities/ 的运行时 reconcile 同步器。

启动扫描（``discover_entities()``）之外的目录对账：

- ``sync_entities()`` 扫描 entities/ 与已加载集合做差——新增目录即时注册
  （工具/分组/配置/lifecycle/路由），消失目录完整拆除（注册表/配置组/
  Lifecycle 组件/sys.modules/路由），``reload_existing=True``（手动刷新）
  对存续实体做代码热更（先按归属注销旧工具再 re-import，被删除的工具不再回归）
- 归属凭据：工具按 ``func.__module__`` 前缀扫描（可靠、无需记录）；分组/
  配置组/Lifecycle 组件无模块归属信息，导入前后做快照差集记录
  （与 entities/plugins/activation.py 的插件装卸载同一范式）
- 路由挂载/摘除经事件总线通知 web 层（EVENT_MODULE_ADDED/REMOVED），
  本层不反向依赖 web

Model Experience：仅改变工具集成员（分组/工具的增删与描述重注册），不注入
任何新内容；变更经 ``EntityRegistry.version()`` 版本三元组在下一轮 think_loop
重建工具集（追加式冻结按存在性滤除已注销工具），stable 工具块一次性重建后
冻结，前缀缓存其余层字节不变。token 稳态为零。
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from core.config import ConfigRegistry
from core.entity import EntityRegistry, EntityType
from core.event_bus import EVENT_MODULE_ADDED, EVENT_MODULE_REMOVED
from core.lifecycle import Lifecycle
from core.log import log

_TAG = "热插拔"

# 已加载实体目录名（启动 discover 与运行时 sync 共同维护，只增于加载、收缩于拆除）
_loaded_modules: Set[str] = set()

# 同步单飞标记（asyncio 单线程 check-then-set 无竞态；监听与手动触发共用）
_sync_running = False

# 无模块归属信息的注册产物差集记录（拆除时的回收凭据）
_entity_groups: Dict[str, Set[str]] = {}
_entity_config_groups: Dict[str, Set[str]] = {}
_entity_lifecycles: Dict[str, Set[str]] = {}


def _entities_dir() -> Path:
    return Path(__file__).parent


def scan_entity_dirs() -> Set[str]:
    """扫描 entities/ 下有效实体目录名（有 tools.py 的子目录，跳过 ``_`` 前缀）。"""
    root = _entities_dir()
    result: Set[str] = set()
    if not root.is_dir():
        return result
    for item in root.iterdir():
        if item.is_dir() and not item.name.startswith("_") and (item / "tools.py").exists():
            result.add(item.name)
    return result


def _entity_tool_names(name: str) -> List[str]:
    """按 func.__module__ 前缀扫描该实体当前注册的全部工具名。"""
    prefix = f"entities.{name}."
    return [
        e.name for e in EntityRegistry.get_by_type(EntityType.TOOL)
        if e.func is not None and getattr(e.func, "__module__", "").startswith(prefix)
    ]


def _snapshot() -> Tuple[Set[str], Set[str]]:
    return (set(EntityRegistry.list_declared_groups()), set(ConfigRegistry.get_all_groups()))


def _record_diff(name: str, before: Tuple[Set[str], Set[str]]) -> None:
    groups, cfg_groups = _snapshot()
    _entity_groups.setdefault(name, set()).update(groups - before[0])
    _entity_config_groups.setdefault(name, set()).update(cfg_groups - before[1])


def load_entity(name: str) -> bool:
    """导入单个实体的 tools.py 并记录分组/配置组归属差集，返回是否成功。"""
    before = _snapshot()
    try:
        importlib.import_module(f"entities.{name}.tools")
    except Exception as e:
        log(f"实体加载失败: {name} - {e}", "WARNING", tag=_TAG)
        return False
    _record_diff(name, before)
    return True


async def register_entity_lifecycle(name: str) -> bool:
    """调用实体的 ``register_lifecycle()`` 并记录 Lifecycle 组件差集。

    无该函数则跳过；进程已 start_all 时对新增组件立即补执行 on_start。
    """
    if not (_entities_dir() / name / "__init__.py").exists():
        return False
    try:
        mod = importlib.import_module(f"entities.{name}")
    except Exception as e:
        log(f"实体 lifecycle 模块加载失败: {name} - {e}", "WARNING", tag="实体")
        return False
    register = getattr(mod, "register_lifecycle", None)
    if not callable(register):
        return False
    before = {entry["name"] for entry in Lifecycle.snapshot()}
    try:
        result = register()
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        log(f"实体 lifecycle 注册失败: {name} - {e}", "WARNING", tag="实体")
        return False
    added = {entry["name"] for entry in Lifecycle.snapshot()} - before
    _entity_lifecycles.setdefault(name, set()).update(added)
    if Lifecycle.started():
        for comp in sorted(added):
            await Lifecycle.start_one(comp)
    log(f"实体 lifecycle 已注册: {name}", "DEBUG", tag="实体")
    return True


async def unload_entity(name: str) -> None:
    """完整拆除实体：注册表 → 分组 → 配置组 → Lifecycle 组件 → sys.modules → 路由事件。"""
    removed_tools = 0
    for tool_name in _entity_tool_names(name):
        if EntityRegistry.unregister(tool_name):
            removed_tools += 1

    for group in sorted(_entity_groups.get(name, set())):
        # 仅回收该实体独占的分组（组内已无其他来源工具），共享分组保留
        if not EntityRegistry.list_group_tools(group):
            EntityRegistry.unregister_group(group)
    for cfg_group in sorted(_entity_config_groups.get(name, set())):
        ConfigRegistry.unregister_group(cfg_group)
    for comp in sorted(_entity_lifecycles.get(name, set())):
        await Lifecycle.unregister(comp)

    prefix = f"entities.{name}"
    for mod_name in [m for m in sys.modules if m == prefix or m.startswith(prefix + ".")]:
        sys.modules.pop(mod_name, None)
    _loaded_modules.discard(name)
    _entity_groups.pop(name, None)
    _entity_config_groups.pop(name, None)
    _entity_lifecycles.pop(name, None)

    _emit(EVENT_MODULE_REMOVED, name)
    if removed_tools:
        from entities._sdk import notify_tool_set_changed
        notify_tool_set_changed()
    log(f"实体已热拔除: {name}（工具 {removed_tools} 个）", tag=_TAG)


def reload_entity(name: str) -> bool:
    """代码热更：按归属注销旧工具后 re-import（被源码删除的工具不再回归）。

    兄弟子模块（providers/router 等）一并从 sys.modules 摘除，re-import 时
    全部以新代码加载（包自身与 tools 模块保留以走 importlib.reload 原地刷新，
    避免父包重导入的循环副作用）；路由摘除后由事件通知 web 层重挂载。
    """
    for tool_name in _entity_tool_names(name):
        EntityRegistry.unregister(tool_name)
    prefix = f"entities.{name}."
    keep = {f"entities.{name}", f"entities.{name}.tools"}
    for mod_name in [m for m in sys.modules if m.startswith(prefix) and m not in keep]:
        sys.modules.pop(mod_name, None)
    module_path = f"entities.{name}.tools"
    mod = sys.modules.get(module_path)
    before = _snapshot()
    try:
        if mod is not None:
            importlib.reload(mod)
        else:
            importlib.import_module(module_path)
    except Exception as e:
        log(f"实体热重载失败: {name} - {e}", "WARNING", tag=_TAG)
        return False
    _record_diff(name, before)
    return True


def _emit(event: str, name: str) -> None:
    """广播模块热插拔事件（无运行中事件循环时静默跳过）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    from core.async_helper import spawn
    from core.event_bus import event_bus

    spawn(event_bus.emit(event, {"kind": "entity", "name": name}), name=event)


def _replay_persisted_states() -> None:
    """回放持久化的工具启停/属性覆盖到新增与重载的实体（幂等，经 _sdk 桥接）。"""
    from entities._sdk import replay_tool_states

    replay_tool_states()


async def sync_entities(reload_existing: bool = False) -> Dict[str, Any]:
    """对账 entities/ 目录与已加载实体：增则热插入，删则热拔除。

    Args:
        reload_existing: True（手动刷新）时存续实体做代码热更；
            目录监听自动触发传 False（仅响应增删，不因文件保存打断运行）。

    Returns:
        {added, removed, reloaded, failed, total} 摘要。
    """
    global _sync_running
    if _sync_running:
        return {
            "added": [], "removed": [], "reloaded": [], "failed": [],
            "total": len(_loaded_modules), "skipped": "in_progress",
        }
    _sync_running = True
    try:
        return await _sync_entities_locked(reload_existing)
    finally:
        _sync_running = False


async def _sync_entities_locked(reload_existing: bool) -> Dict[str, Any]:
    current = scan_entity_dirs()
    added: List[str] = []
    removed: List[str] = []
    reloaded: List[str] = []
    failed: List[str] = []

    for name in sorted(current - _loaded_modules):
        if load_entity(name):
            _loaded_modules.add(name)
            await register_entity_lifecycle(name)
            _emit(EVENT_MODULE_ADDED, name)
            added.append(name)
            log(f"实体已热插入: {name}", tag=_TAG)
        else:
            failed.append(name)

    for name in sorted(_loaded_modules - current):
        try:
            await unload_entity(name)
            removed.append(name)
        except Exception as e:
            failed.append(name)
            log(f"实体热拔除失败: {name} - {e}", "ERROR", tag=_TAG)

    if reload_existing:
        for name in sorted(current & _loaded_modules):
            if reload_entity(name):
                reloaded.append(name)
                _emit(EVENT_MODULE_ADDED, name)
            else:
                failed.append(name)

    if added or reloaded:
        try:
            _replay_persisted_states()
        except Exception as e:
            log(f"持久化状态回放失败: {e}", "WARNING", tag=_TAG)
    if added or removed or reloaded:
        from entities._sdk import notify_tool_set_changed
        notify_tool_set_changed()
        log(f"实体同步完成: +{len(added)} -{len(removed)} ~{len(reloaded)}", tag=_TAG)

    return {
        "added": added,
        "removed": removed,
        "reloaded": reloaded,
        "failed": failed,
        "total": len(_loaded_modules),
    }
