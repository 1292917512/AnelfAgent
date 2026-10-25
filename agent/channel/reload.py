"""在频道操作锁内替换私有模块与实例，失败时恢复原版本。

模型看到什么：频道工具通过原注册表重建，不增加模型消息或新通道。
token 影响：不额外注入 token；工具声明的增减由频道自身定义。
缓存影响：EntityRegistry 版本变更使工具 schema 缓存失效，不修改上下文前缀。
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.util import cache_from_source
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import ConfigManager
from core.entity import EntityRegistry
from core.lifecycle import Lifecycle
from core.log import log
from core.registration import belongs_to, registration_transaction

from .config import channel_schema_registration, channels_dir
from .tool_bridge import unregister_channel_tools

if TYPE_CHECKING:
    from .manager import ChannelManager


async def release_channel_resources(channel_id: str) -> None:
    """由生命周期宿主逆序回收频道附属资源，失败时继续保有其所有权。"""
    for entry in reversed(Lifecycle.snapshot()):
        if belongs_to(entry["module"], f"channels.{channel_id}"):
            if not await Lifecycle.unregister(entry["name"], per_timeout=10):
                raise RuntimeError(f"频道 {channel_id} 资源关闭失败: {entry['name']}")


def pop_channel_modules(channel_id: str) -> None:
    """清理频道私有模块及配置回退模块，不影响公共 SDK 与其他频道。"""
    prefix = f"channels.{channel_id}"
    for name in list(sys.modules):
        if belongs_to(name, prefix) or name == f"_channel_cfg_{channel_id}":
            sys.modules.pop(name, None)
    parent = sys.modules.get("channels")
    if parent is not None:
        vars(parent).pop(channel_id, None)


def _prepare_sources(channel_id: str) -> None:
    """停止连接前预检源码与字节码路径，避免时间戳缓存掩盖最新更改。"""
    root = (channels_dir() / channel_id).resolve()
    sources: list[Path] = []
    excluded = {"tests", "frontend", "__pycache__", "node_modules", ".venv", "venv", ".git"}
    for directory, children, files in os.walk(root):
        children[:] = [name for name in children if name not in excluded]
        sources.extend(Path(directory) / name for name in files if name.endswith(".py"))
    for path in sources:
        if not path.resolve().is_relative_to(root):
            raise ImportError(f"频道源码越出模块目录: {path.name}")
        compile(path.read_bytes(), str(path), "exec")
    for path in sources:
        cache_path = Path(cache_from_source(str(path)))
        if not cache_path.resolve().is_relative_to(root):
            raise ImportError(f"频道字节码越出模块目录: {path.name}")
        cache_path.unlink(missing_ok=True)
    importlib.invalidate_caches()


@contextmanager
def _fresh_modules(channel_id: str) -> Iterator[None]:
    """在旧实例完成关闭后换入新模块；失败恢复原模块与包属性。"""
    prefix = f"channels.{channel_id}"
    previous = {name: module for name, module in sys.modules.items()
                if belongs_to(name, prefix) or name == f"_channel_cfg_{channel_id}"}
    parent = sys.modules.get("channels")
    previous_attr = vars(parent).get(channel_id) if parent else None
    pop_channel_modules(channel_id)
    try:
        yield
    except BaseException:
        pop_channel_modules(channel_id)
        sys.modules.update(previous)
        if parent is not None and previous_attr is not None:
            setattr(parent, channel_id, previous_attr)
        raise


async def reload_channel(manager: ChannelManager, channel_id: str) -> None:
    """锁内预检、停止、重建；清理失败保留资源句柄，禁止启动第二份连接。"""
    from .manager import load_channel_class

    previous = manager.get(channel_id)
    metadata = EntityRegistry.get(previous.get_entity_name()) if previous else None
    entity_enabled = metadata.enabled if metadata else False
    detached = False
    stranded: BaseException | None = None
    _prepare_sources(channel_id)
    await release_channel_resources(channel_id)
    if previous is not None:
        if not await asyncio.wait_for(manager._stop_channel(channel_id), 10):
            raise RuntimeError(f"频道 {channel_id} 停止失败，保留原实例")
    try:
        with _fresh_modules(channel_id), channel_schema_registration(channel_id):
            channel_cls = load_channel_class(channel_id)
            if channel_cls is None:
                raise RuntimeError(f"频道 {channel_id} 适配器加载失败")
            if previous is not None:
                manager.unregister(channel_id, replacing=True)
                detached = True
            replacement = None
            try:
                if previous is not None or manager._desired_running[channel_id]:
                    with registration_transaction(f"channels.{channel_id}"):
                        replacement = channel_cls()
                    manager.register(replacement)
                    if manager._desired_running[channel_id]:
                        if not await asyncio.wait_for(manager._start_channel(channel_id), 15):
                            raise RuntimeError(f"频道 {channel_id} 新版本启动失败")
                    else:
                        unregister_channel_tools(channel_id)
            except BaseException as exc:
                if replacement is not None:
                    try:
                        cleaned = await asyncio.wait_for(manager._stop_channel(channel_id), 5)
                        if cleaned:
                            await release_channel_resources(channel_id)
                    except (Exception, asyncio.CancelledError):
                        cleaned = False
                    if not cleaned:
                        # 持有尚未关闭的实例及其配置，后续重试仍能安全清理。
                        unregister_channel_tools(channel_id)
                        stranded = exc
                    else:
                        manager.unregister(channel_id, replacing=True)
                if stranded is None:
                    raise
    except BaseException:
        if previous is not None:
            if detached:
                if metadata is not None:
                    EntityRegistry.register(metadata)
                    EntityRegistry.activate_entity(metadata.name)
                    EntityRegistry.set_enabled(metadata.name, entity_enabled, persist=False)
                ConfigManager.add_listener(previous._config_key_prefix, previous._on_config_changed)
                manager.register(previous)
            if manager._desired_running[channel_id]:
                try:
                    restored = await asyncio.wait_for(manager._start_channel(channel_id), 15)
                    if not restored:
                        log(f"频道旧版本恢复失败: {channel_id}", "ERROR", tag="热插拔")
                except (Exception, asyncio.CancelledError) as exc:
                    log(f"频道旧版本恢复中断: {channel_id}: {type(exc).__name__}", "ERROR", tag="热插拔")
            else:
                unregister_channel_tools(channel_id)
        raise
    if stranded is not None:
        message = f"频道 {channel_id} 启动失败且资源未完全关闭，保留实例供重试"
        if not isinstance(stranded, Exception):
            log(message, "ERROR", tag="热插拔")
            raise stranded
        raise RuntimeError(message) from stranded
