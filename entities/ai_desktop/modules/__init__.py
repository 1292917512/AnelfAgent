"""桌面组件包 — 目录自动发现（组件以子包形式自治）。

新增组件 = 新建子包（``<key>/``），其 ``__init__.py`` 导入组件模块使
``@desktop_module`` 装饰器完成注册；删除子包即整体拔出（含组件内嵌
tests/），无需改动其他任何文件。
"""

from __future__ import annotations

import importlib
import pkgutil

from core.log import log


def load_modules() -> None:
    """扫描导入本包内全部组件子包/模块（幂等）。"""
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{info.name}")
        except Exception as exc:
            log(f"桌面组件 {info.name} 加载失败: {exc}", "WARNING", tag="AI桌面")
