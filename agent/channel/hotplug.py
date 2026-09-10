"""频道目录热插拔 — channels/ 的运行时 reconcile 同步器。

启动扫描（``discover_channels()``）之外的目录对账：

- ``sync_channels()`` 扫描 channels/ 与已知集合做差——新增目录即时接入
  （配置 schema/store/watcher 注册，enabled 则激活启动，路由挂载）；
  消失目录完整拆除（停止连接 → ChannelManager 注销 → 配置 schema/store/
  watcher 回收 → sys.modules 清理 → 路由摘除），看门狗遍历 list_channels()
  故注销后天然不再干预
- ``reload_existing=True``（手动刷新）对存续频道做代码热更：停止 → 注销 →
  清理 sys.modules → 重新注册 schema → 重新激活（连接会断开重连一次）
- 路由挂载/摘除经事件总线通知 web 层（EVENT_MODULE_ADDED/REMOVED），
  本层不反向依赖 web

"已知集合" = ChannelManager 已注册频道 ∪ 本模块此前同步过的目录
（含 enabled=false 未实例化的频道——其 schema 已在同步时注册）。
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict, List, Optional, Set

from core.config import ConfigRegistry, get_config_bool
from core.event_bus import EVENT_MODULE_ADDED, EVENT_MODULE_REMOVED
from core.log import log

from .config import (
    channels_dir,
    config_key,
    register_channel_schema,
    unregister_channel_schema,
)
from .manager import get_channel_manager

_TAG = "热插拔"

# 此前同步过的频道目录名（含未实例化的）；None 表示尚未做首次同步
_synced_channels: Optional[Set[str]] = None

# 同步单飞标记（asyncio 单线程 check-then-set 无竞态；监听与手动触发共用）
_sync_running = False


def scan_channel_dirs() -> Set[str]:
    """扫描 channels/ 下有效频道目录名（有 adapter.py 的子目录，跳过 ``_`` 前缀）。"""
    root = channels_dir()
    result: Set[str] = set()
    if not root.is_dir():
        return result
    for item in root.iterdir():
        if item.is_dir() and not item.name.startswith("_") and (item / "adapter.py").exists():
            result.add(item.name)
    return result


def _known_channels() -> Set[str]:
    """已知频道集合：已注册实例 ∪ 已注册配置 schema 的目录 ∪ 历次同步记录。"""
    global _synced_channels
    known = set(get_channel_manager().list_channels())
    if _synced_channels is not None:
        known |= _synced_channels
    # 启动期 register_channel_schemas 注册过 schema 但未实例化的频道
    known |= {
        g.split("/", 1)[1] for g in ConfigRegistry.get_all_groups()
        if g.startswith("adapter/")
    }
    return known


def _pop_channel_modules(channel_id: str) -> None:
    prefix = f"channels.{channel_id}"
    for mod_name in [m for m in sys.modules if m == prefix or m.startswith(prefix + ".")]:
        sys.modules.pop(mod_name, None)


def _emit(event: str, channel_id: str) -> None:
    """广播模块热插拔事件（无运行中事件循环时静默跳过）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    from core.async_helper import spawn
    from core.event_bus import event_bus

    spawn(event_bus.emit(event, {"kind": "channel", "name": channel_id}), name=event)


async def _add_channel(channel_id: str) -> None:
    """热插入频道目录：注册配置 schema，enabled 则激活启动，通知路由挂载。"""
    mgr = get_channel_manager()
    register_channel_schema(channel_id)
    if get_config_bool(config_key(channel_id, "enabled"), False):
        await mgr.activate_channel(channel_id)
    _emit(EVENT_MODULE_ADDED, channel_id)
    log(f"频道已热插入: {channel_id}", tag=_TAG)


async def _remove_channel(channel_id: str) -> None:
    """热拔除频道目录：停止并注销实例 → 回收配置 schema → 清模块 → 通知路由摘除。"""
    mgr = get_channel_manager()
    if channel_id in mgr.list_channels():
        await mgr.stop_channel(channel_id)
        mgr.unregister(channel_id)
    unregister_channel_schema(channel_id)
    _pop_channel_modules(channel_id)
    _emit(EVENT_MODULE_REMOVED, channel_id)
    log(f"频道已热拔除: {channel_id}", tag=_TAG)


async def _reload_channel(channel_id: str) -> None:
    """代码热更：停止注销后以全新模块重建（仅 enabled 频道重新启动）。"""
    mgr = get_channel_manager()
    was_enabled = get_config_bool(config_key(channel_id, "enabled"), False)
    if channel_id in mgr.list_channels():
        await mgr.stop_channel(channel_id)
        mgr.unregister(channel_id)
    _pop_channel_modules(channel_id)
    register_channel_schema(channel_id)
    if was_enabled:
        await mgr.activate_channel(channel_id)
    _emit(EVENT_MODULE_ADDED, channel_id)
    log(f"频道已热重载: {channel_id}", tag=_TAG)


async def sync_channels(reload_existing: bool = False) -> Dict[str, Any]:
    """对账 channels/ 目录与已知频道：增则热插入，删则热拔除。

    Args:
        reload_existing: True（手动刷新）时存续频道做代码热更（连接重连一次）；
            目录监听自动触发传 False（仅响应增删）。

    Returns:
        {added, removed, reloaded, failed, total} 摘要。
    """
    global _sync_running, _synced_channels
    if _sync_running:
        return {
            "added": [], "removed": [], "reloaded": [], "failed": [],
            "total": len(scan_channel_dirs()), "skipped": "in_progress",
        }
    _sync_running = True
    try:
        return await _sync_channels_locked(reload_existing)
    finally:
        _sync_running = False


async def _sync_channels_locked(reload_existing: bool) -> Dict[str, Any]:
    global _synced_channels
    current = scan_channel_dirs()
    known = _known_channels()
    added: List[str] = []
    removed: List[str] = []
    reloaded: List[str] = []
    failed: List[str] = []

    for cid in sorted(current - known):
        try:
            await _add_channel(cid)
            added.append(cid)
        except Exception as e:
            failed.append(cid)
            log(f"频道热插入失败: {cid} - {e}", "ERROR", tag=_TAG)

    for cid in sorted(known - current):
        try:
            await _remove_channel(cid)
            removed.append(cid)
        except Exception as e:
            failed.append(cid)
            log(f"频道热拔除失败: {cid} - {e}", "ERROR", tag=_TAG)

    if reload_existing:
        # 覆盖全部已知存续频道（含未实例化的：刷新 schema 代码；enabled 的经激活重建）
        for cid in sorted(current & known):
            try:
                await _reload_channel(cid)
                reloaded.append(cid)
            except Exception as e:
                failed.append(cid)
                log(f"频道热重载失败: {cid} - {e}", "ERROR", tag=_TAG)

    # 失败的目录不记入已知集合，下次同步自动重试
    _synced_channels = current - set(failed)
    if added or removed or reloaded:
        log(f"频道同步完成: +{len(added)} -{len(removed)} ~{len(reloaded)}", tag=_TAG)
    return {
        "added": added,
        "removed": removed,
        "reloaded": reloaded,
        "failed": failed,
        "total": len(current),
    }
