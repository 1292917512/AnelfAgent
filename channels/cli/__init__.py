"""CLI 频道 — 本地终端交互式调试用。

CLI 频道不通过 discover_channels() 自动发现，
需要手动通过 ``python -m channels.cli`` 启动。
"""

from .adapter import CLIChannel

__all__ = ["CLIChannel"]
