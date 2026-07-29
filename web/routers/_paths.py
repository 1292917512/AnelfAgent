"""工作区路径沙箱包装。

entities.filesystem 尚未提供公开的路径解析入口，这里集中引用其私有
``_safe_path``；待 entities 暴露公开接口后只需替换本模块实现。
"""

from __future__ import annotations

from entities.filesystem.tools import _safe_path as _fs_safe_path


def safe_workspace_path(path: str) -> str:
    """解析工作区内路径，越界抛 ValueError。"""
    return _fs_safe_path(path)
