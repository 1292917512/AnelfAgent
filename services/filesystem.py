"""文件系统服务 -- 工作区路径沙箱解析（封装 entities.filesystem）。"""

from __future__ import annotations


def safe_workspace_path(path: str) -> str:
    """解析工作区内路径（沙箱检查），越界抛 ValueError。"""
    from entities.filesystem.tools import safe_path
    return safe_path(path)
