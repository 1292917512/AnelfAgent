"""WebUI 频道适配器 — 通过 SSE 向前端推送消息。

通过 discover_channels() 自动发现并注册（deferred_start=true），
在 Web 服务器启动时由 start_web_server() 触发实际启动。
"""

from .adapter import WebUIChannel

__all__ = ["WebUIChannel"]
