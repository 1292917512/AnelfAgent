"""工作区路径沙箱包装（转发 services.filesystem 的公开实现）。"""

from __future__ import annotations

from services.filesystem import safe_workspace_path

__all__ = ["safe_workspace_path"]
