"""同步来源组件自动加载：扫描 sources/ 目录导入全部组件模块。"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path


def load_sources() -> None:
    """导入 sources/ 下全部非下划线模块（模块内 @sync_source 自注册）。"""
    package = __package__
    for module in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if module.name.startswith("_"):
            continue
        importlib.import_module(f"{package}.{module.name}")
