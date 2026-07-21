"""频道通用工具集。

提供跨频道复用的媒体处理与文本格式化能力，避免每个频道重复实现。
"""

from . import formatter, media

__all__ = ["formatter", "media"]
