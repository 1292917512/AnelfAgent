"""MCP transport 构建：stdio/streamable_http/sse 上下文管理器工厂与会话回调。

stdio 子进程默认仅透传白名单环境变量（防止敏感 env 泄露给第三方
MCP server），配置 mcp_stdio_passthrough_env=True 时恢复全量透传。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from entities.mcp.config import MCPServerConfig

# stdio 子进程环境变量白名单（防止敏感 env 泄露给第三方 MCP server）
_STDIO_ENV_WHITELIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "LANGUAGE", "TERM", "TZ",
    "SYSTEMROOT", "COMSPEC", "TEMP", "TMP",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "HOMEDRIVE", "HOMEPATH", "PATHEXT", "USERNAME", "OS",
})


async def _list_roots_callback(context: Any) -> Any:
    """MCP roots 能力回调：向 server 声明允许写入的根目录。

    chrome-devtools-mcp 等 server 仅允许 filePath 写入 roots 之内
    （客户端未声明 roots 时默认只有 OS 临时目录）。将 workspace
    声明为 root 后，截图/快照等工具可直接保存到工作区。
    """
    from mcp import types

    from core.path import workspace_root

    ws = Path(workspace_root()).resolve()
    return types.ListRootsResult(
        roots=[types.Root(uri=ws.as_uri(), name="workspace")]
    )


def _build_stdio_env(user_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """构建 stdio 子进程环境变量：默认白名单 + 用户显式配置。

    配置 mcp_stdio_passthrough_env=True 时恢复全量透传。
    """
    try:
        from core.config import ConfigManager
        passthrough = bool(ConfigManager.get("mcp_stdio_passthrough_env", False))
    except Exception:
        passthrough = False

    if passthrough:
        env: Dict[str, str] = dict(os.environ)
    else:
        env = {
            k: v for k, v in os.environ.items()
            if k in _STDIO_ENV_WHITELIST or k.startswith("LC_")
        }

    env["ANELF_MCP_STDIO"] = "1"
    env["ANELF_LOG_STREAM"] = "stderr"
    env["PYTHONUNBUFFERED"] = "1"
    if user_env:
        env.update(user_env)
    return env


def _create_transport(srv: MCPServerConfig) -> Any:
    """根据配置创建传输上下文管理器。"""
    transport = srv.transport or ("stdio" if srv.command else "streamable_http")

    if transport == "stdio":
        from mcp.client.stdio import StdioServerParameters, stdio_client
        stdio_env = _build_stdio_env(srv.env)
        return stdio_client(StdioServerParameters(
            command=srv.command,
            args=srv.args,
            env=stdio_env,
        ))

    if transport == "streamable_http":
        from mcp.client.streamable_http import streamablehttp_client
        return streamablehttp_client(
            url=srv.url,
            headers=srv.headers or None,
            timeout=srv.timeout,
        )

    if transport == "sse":
        from mcp.client.sse import sse_client
        return sse_client(
            srv.url,
            headers=srv.headers or None,
            timeout=srv.timeout,
            sse_read_timeout=srv.sse_read_timeout,
        )

    raise ValueError(f"不支持的传输类型: {transport}")
