"""智能家居设备域包 — 目录自动发现（组件以模块形式自治）。

新增设备域 = 新建模块文件并定义 ``@device_domain`` 装饰的 DeviceDomain 子类；
删除文件即整体拔出，无需改动其他任何文件。
"""

from __future__ import annotations

import importlib
import pkgutil

from core.log import log


def load_domains() -> None:
    """扫描导入本包内全部设备域模块（幂等）。"""
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{info.name}")
        except Exception as exc:
            log(f"设备域 {info.name} 加载失败: {exc}", "WARNING", tag="智能家居")
