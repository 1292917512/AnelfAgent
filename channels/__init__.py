"""
频道系统 — 自动发现并加载所有频道适配器。

每个频道是一个子目录，包含：
- ``adapter.py`` — 频道类（继承 BaseChannel）
- ``config.py`` — 频道配置声明（标准符号 ``CONFIG_MODEL``，ChannelConfig 子类；
  schema 注册与值读写统一走 core.config，见 agent/channel/config.py）

使用 ``discover_channels()`` 扫描并实例化已启用的频道。
"""

from __future__ import annotations

import importlib
import traceback
from pathlib import Path
from typing import List, Optional, Type

from core.config import get_config_bool
from core.log import log


def discover_channels() -> List:
    """扫描 channels/ 下所有子目录，实例化已启用的频道。"""
    from agent.channel.base import BaseChannel
    from agent.channel.config import config_key

    channel_dir = Path(__file__).parent
    loaded: list = []
    skipped: list[str] = []
    failed: list[str] = []

    for item in sorted(channel_dir.iterdir()):
        if not item.is_dir() or item.name.startswith("_"):
            continue
        adapter_file = item / "adapter.py"
        if not adapter_file.exists():
            continue

        module_path = f"channels.{item.name}.adapter"
        try:
            mod = importlib.import_module(module_path)
        except Exception as e:
            failed.append(item.name)
            log(f"频道模块加载失败: {item.name} - {e}", "WARNING")
            continue

        channel_cls: Optional[Type[BaseChannel]] = getattr(mod, "CHANNEL_CLASS", None)
        if channel_cls is None:
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, type) and issubclass(attr, BaseChannel) and attr is not BaseChannel:
                    channel_cls = attr
                    break

        if channel_cls is None:
            skipped.append(item.name)
            continue

        # 检查是否启用（统一配置 <id>_enabled，缺省 False）
        if not get_config_bool(config_key(item.name, "enabled"), False):
            skipped.append(item.name)
            continue

        # 实例化（配置在 __init__ 中从 ConfigManager 物化）
        try:
            instance = channel_cls()
            instance._deferred_start = get_config_bool(
                config_key(item.name, "deferred_start"), False,
            )
            loaded.append(instance)
        except Exception as e:
            failed.append(item.name)
            log(f"频道实例化失败: {item.name} - {e}\n{traceback.format_exc()}", "WARNING")

    if loaded:
        names = [getattr(ch, "display_name", "?") for ch in loaded]
        log(f"频道已加载: {', '.join(names)} ({len(loaded)} 个)")
    if skipped:
        log(f"频道已跳过（未启用）: {', '.join(skipped)}", "DEBUG")
    if failed:
        log(f"频道加载失败: {', '.join(failed)}", "WARNING")

    return loaded
