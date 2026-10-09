"""MCP transport 构建：stdio/streamable_http/sse 上下文管理器工厂与会话回调。

stdio 子进程默认仅透传白名单环境变量（防止敏感 env 泄露给第三方
MCP server），配置 mcp_stdio_passthrough_env=True 时恢复全量透传。
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from entities.mcp.config import MCPServerConfig

# stdio 子进程环境变量白名单（防止敏感 env 泄露给第三方 MCP server）
_STDIO_ENV_WHITELIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "LANGUAGE", "TERM", "TZ",
    "SYSTEMROOT", "COMSPEC", "TEMP", "TMP",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "HOMEDRIVE", "HOMEPATH", "PATHEXT", "USERNAME", "OS",
})

# Windows stdio 子进程优先级档位 → creationflags（spawn 时一次性生效，
# 之后优先级不再变更）。below_normal/idle 让吃 CPU 的本地服务（如
# minecraft 执行器进服时解析区块）不抢占同机主程序。
_priority_flags_cache: Optional[Dict[str, int]] = None
# spawn 期间的优先级意图：contextvar 按任务隔离，并发连接各自生效
_stdio_priority_flag: ContextVar[int] = ContextVar("anelf_mcp_stdio_priority", default=0)


def _windows_priority_flags() -> Dict[str, int]:
    """惰性构建优先级档位表（仅 Windows 有意义，其他平台返回空表）。"""
    global _priority_flags_cache
    if _priority_flags_cache is None:
        if sys.platform == "win32":
            import subprocess

            _priority_flags_cache = {
                "normal": 0,
                "below_normal": int(subprocess.BELOW_NORMAL_PRIORITY_CLASS),
                "idle": int(subprocess.IDLE_PRIORITY_CLASS),
            }
        else:
            _priority_flags_cache = {}
    return _priority_flags_cache


def _install_stdio_priority_patch() -> None:
    """包装 mcp SDK 的 Windows 进程创建，注入优先级 creationflags。

    SDK 的 create_windows_process 把 creationflags 写死为 CREATE_NO_WINDOW，
    无公开扩展点；此处仅在其内部查找表上包一层，按 spawn 时的 contextvar
    追加优先级标志。未设置优先级时原样透传，行为与未打补丁完全一致。
    """
    from mcp.client import stdio as mcp_stdio

    if getattr(mcp_stdio.create_windows_process, "_anelf_priority_patch", False):
        return
    sdk_create_windows_process = mcp_stdio.create_windows_process

    async def _create_windows_process_with_priority(
        command: str,
        args: list,
        env: Optional[Dict[str, str]] = None,
        errlog: Any = None,
        cwd: Any = None,
    ) -> Any:
        flag = _stdio_priority_flag.get()
        if not flag:
            return await sdk_create_windows_process(command, args, env, errlog, cwd)
        return await _spawn_windows_process_with_priority(
            command, args, env, errlog, cwd, flag
        )

    _create_windows_process_with_priority._anelf_priority_patch = True  # type: ignore[attr-defined]
    mcp_stdio.create_windows_process = _create_windows_process_with_priority


async def _spawn_windows_process_with_priority(
    command: str,
    args: list,
    env: Optional[Dict[str, str]],
    errlog: Any,
    cwd: Any,
    priority_flag: int,
) -> Any:
    """带优先级 creationflags 的 Windows spawn（其余逻辑与 SDK 保持一致）。

    复用 SDK 的 Job Object 助手，保证进程树终止语义不变。
    （助手位于 mcp.os.win32.utilities——SDK 的 create_windows_process
    也从该模块导入；私有符号，随 SDK 升级需核对。）
    """
    import subprocess

    import anyio
    from mcp.os.win32 import utilities as win32_util

    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | priority_flag
    try:
        process = await anyio.open_process(
            [command, *args],
            env=env,
            creationflags=creationflags,
            stderr=errlog,
            cwd=cwd,
        )
    except NotImplementedError:
        # 无 async subprocess 支持的事件循环（SelectorEventLoop）走 SDK 兜底
        process = await win32_util._create_windows_fallback_process(
            command, args, env, errlog, cwd
        )
    job = win32_util._create_job_object()
    win32_util._maybe_assign_process_to_job(process, job)
    return process


@asynccontextmanager
async def _priority_scoped_transport(
    client_cm: Any, priority_flag: int
) -> AsyncIterator[Any]:
    """仅在 stdio_client 进入（spawn 发生）期间设置优先级 contextvar。

    spawn 完成后立即复位：优先级是一次性 spawn 属性，贯穿会话会让
    同任务内后续其他连接的 spawn 误继承。
    """
    token = _stdio_priority_flag.set(priority_flag)
    try:
        streams = await client_cm.__aenter__()
    finally:
        _stdio_priority_flag.reset(token)
    try:
        yield streams
    except BaseException as exc:
        suppress = await client_cm.__aexit__(
            type(exc), exc, exc.__traceback__
        )
        if not suppress:
            raise
    else:
        await client_cm.__aexit__(None, None, None)


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


def _oauth_provider(srv: MCPServerConfig) -> Any:
    """按需构造 OAuth 提供者（SDK 在 401 时自动发起授权流；不适用返回 None）。"""
    from entities.mcp.oauth import make_oauth_provider
    return make_oauth_provider(srv)


def _create_transport(srv: MCPServerConfig) -> Any:
    """根据配置创建传输上下文管理器。"""
    transport = srv.transport or ("stdio" if srv.command else "streamable_http")

    if transport == "stdio":
        from mcp.client.stdio import StdioServerParameters, stdio_client
        stdio_env = _build_stdio_env(srv.env)
        client_cm = stdio_client(StdioServerParameters(
            command=srv.command,
            args=srv.args,
            env=stdio_env,
        ))
        priority_flag = _windows_priority_flags().get(str(srv.priority or "").strip().lower(), 0)
        if priority_flag and sys.platform == "win32":
            _install_stdio_priority_patch()
            return _priority_scoped_transport(client_cm, priority_flag)
        return client_cm

    if transport == "streamable_http":
        # mcp 2.x：headers/timeout/auth 全部经 http_client 传入，且外部
        # 提供的 client 由调用方自持生命周期——组合 CM 确保随传输一起关闭。
        # timeout 映射与 2.x sse_client 内部一致（连接/写/池 = timeout，读 = sse_read_timeout）
        import httpx2
        from mcp.client.streamable_http import streamable_http_client
        http_client = httpx2.AsyncClient(
            headers=srv.headers or None,
            timeout=httpx2.Timeout(srv.timeout, read=srv.sse_read_timeout),
            auth=_oauth_provider(srv),
        )

        @asynccontextmanager
        async def _owned_streamable_http() -> AsyncIterator[Any]:
            async with http_client:
                async with streamable_http_client(url=srv.url, http_client=http_client) as streams:
                    yield streams

        return _owned_streamable_http()

    if transport == "sse":
        from mcp.client.sse import sse_client
        return sse_client(
            srv.url,
            headers=srv.headers or None,
            timeout=srv.timeout,
            sse_read_timeout=srv.sse_read_timeout,
            auth=_oauth_provider(srv),
        )

    raise ValueError(f"不支持的传输类型: {transport}")
