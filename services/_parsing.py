"""通用解析工具 -- 跨服务/路由复用的容错类型转换。"""

from __future__ import annotations

from typing import Any


def to_bool(value: Any, *, default: bool = False) -> bool:
    """兼容字符串/数字的布尔值解析。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
