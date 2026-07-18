"""安全防护层：会话令牌、威胁扫描、敏感信息脱敏。

- session_token: 一次性会话令牌，防 prompt 注入伪造历史
- threat_scanner: 已知注入模式扫描（上下文文件 / 记忆写入 / 工具结果）
- 脱敏核心在 core.sanitizer（供 core.log 等底层模块共用）
"""

from agent.security.session_token import (
    bind_token,
    current_token,
    detect_leak,
    generate_token,
    reset_token,
    wrap_history_content,
)
from agent.security.threat_scanner import first_threat_message, scan_for_threats

__all__ = [
    "bind_token",
    "current_token",
    "detect_leak",
    "generate_token",
    "reset_token",
    "wrap_history_content",
    "first_threat_message",
    "scan_for_threats",
]
