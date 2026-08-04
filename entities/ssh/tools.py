"""SSH 远程管理实体工具 — 连接管理、命令执行、文件传输。

工具经实体目录两级发现（query_entities → list_entity_methods(group="ssh")）。
所有操作类工具的 name 参数缺省时使用默认连接（ssh_set_default 可随时切换），
实现"一条指令直达"的使用体验。
"""

from __future__ import annotations

import json

from entities._sdk import (
    ErrorCause,
    error_from_exception,
    get_current_scope,
    tool,
    tool_error,
)

from .manager import get_ssh_manager
from .store import get_ssh_store


def _ai_enabled() -> bool:
    """AI 工具总开关（实体配置 ssh_ai_enabled）。"""
    from core.config import get_config_bool
    return get_config_bool("ssh_ai_enabled", True)


def _gate() -> str:
    """开关关闭时返回统一错误，否则返回空串。"""
    if not _ai_enabled():
        return tool_error(
            "SSH 工具已在实体配置中禁用",
            cause=ErrorCause.STATE,
            retryable=False,
            hint="如需使用，请在实体详情页配置中开启 ssh_ai_enabled",
        )
    return ""


@tool(name="ssh_list", group="ssh", concurrency_safe=True)
def ssh_list() -> str:
    """列出所有 SSH 连接配置及其实时状态（含默认连接标记）。

    执行远程操作前建议先调用本工具了解可用连接。
    """
    if error := _gate():
        return error
    manager = get_ssh_manager()
    default_name = get_ssh_store().get_default_name()
    return json.dumps({
        "default": default_name,
        "connections": manager.list_statuses(),
    }, ensure_ascii=False)


@tool(name="ssh_exec", group="ssh", timeout=300)
async def ssh_exec(command: str, name: str = "", timeout: int = 0, work_dir: str = "") -> str:
    """在 SSH 连接上执行命令，返回结构化结果（exit_code/stdout/stderr）。

    连接未建立时自动建连；连接中断会自动重连并重试一次。

    Args:
        command: 要执行的 shell 命令
        name: 连接名称，缺省使用默认连接
        timeout: 命令超时秒数，缺省使用实体配置的默认值（60秒）
        work_dir: 远程工作目录（可选，先 cd 再执行）
    """
    if error := _gate():
        return error
    if not command.strip():
        return tool_error("命令不能为空", cause=ErrorCause.PARAM)
    try:
        result = await get_ssh_manager().execute(
            command, name=name, timeout=float(timeout), work_dir=work_dir,
        )
        return json.dumps(result, ensure_ascii=False)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM)
    except Exception as e:
        return error_from_exception(e, action=f"SSH 执行命令 [{command[:50]}]")


@tool(name="ssh_upload", group="ssh", timeout=600)
async def ssh_upload(local_path: str, remote_path: str, name: str = "") -> str:
    """上传本地文件到远程服务器（SFTP）。

    Args:
        local_path: 本地文件路径
        remote_path: 远程目标路径（含文件名）
        name: 连接名称，缺省使用默认连接
    """
    if error := _gate():
        return error
    try:
        result = await get_ssh_manager().upload(local_path, remote_path, name=name)
        return json.dumps(result, ensure_ascii=False)
    except FileNotFoundError as e:
        return tool_error(str(e), cause=ErrorCause.NOT_FOUND)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM)
    except Exception as e:
        return error_from_exception(e, action="SSH 上传文件")


@tool(name="ssh_download", group="ssh", timeout=600)
async def ssh_download(remote_path: str, local_path: str, name: str = "") -> str:
    """下载远程文件到本地（SFTP）。

    Args:
        remote_path: 远程文件路径
        local_path: 本地保存路径（含文件名）
        name: 连接名称，缺省使用默认连接
    """
    if error := _gate():
        return error
    try:
        result = await get_ssh_manager().download(remote_path, local_path, name=name)
        return json.dumps(result, ensure_ascii=False)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM)
    except Exception as e:
        return error_from_exception(e, action="SSH 下载文件")


@tool(name="ssh_connect", group="ssh", timeout=60)
async def ssh_connect(name: str = "") -> str:
    """建立 SSH 连接（连接池复用，已连接时直接返回）。

    Args:
        name: 连接名称，缺省使用默认连接
    """
    if error := _gate():
        return error
    manager = get_ssh_manager()
    try:
        target = manager.resolve_name(name)
        snapshot = await manager.connect(target)
        return json.dumps({"ok": True, "connection": snapshot}, ensure_ascii=False)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM)
    except Exception as e:
        return error_from_exception(e, action="SSH 连接")


@tool(name="ssh_disconnect", group="ssh")
async def ssh_disconnect(name: str = "") -> str:
    """断开 SSH 连接。

    Args:
        name: 连接名称，缺省使用默认连接
    """
    if error := _gate():
        return error
    manager = get_ssh_manager()
    try:
        target = manager.resolve_name(name)
        await manager.disconnect(target)
        return json.dumps({"ok": True, "disconnected": target}, ensure_ascii=False)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM)


@tool(name="ssh_set_default", group="ssh")
async def ssh_set_default(name: str) -> str:
    """切换默认 SSH 连接（后续 ssh_exec 等工具缺省 name 时使用该连接）。

    Args:
        name: 要设为默认的连接名称（须已存在，可用 ssh_list 查看）
    """
    if error := _gate():
        return error
    try:
        await get_ssh_store().set_default(name)
        return json.dumps({"ok": True, "default": name}, ensure_ascii=False)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.NOT_FOUND)


@tool(name="ssh_add", group="ssh")
async def ssh_add(
    name: str,
    host: str,
    username: str,
    password: str = "",
    port: int = 22,
    key_path: str = "",
    passphrase: str = "",
    description: str = "",
) -> str:
    """添加 SSH 连接配置（密码与私钥至少提供一项）。

    Args:
        name: 连接名称（唯一标识，建议简短易记，如 prod-web）
        host: 主机地址（IP 或域名）
        username: 登录用户名
        password: 登录密码（支持 ${ENV_VAR} 环境变量引用，避免明文存储）
        port: 端口号，默认 22
        key_path: 私钥文件路径（与密码二选一，优先使用私钥）
        passphrase: 私钥口令（可选）
        description: 连接用途描述（可选）
    """
    if error := _gate():
        return error
    scope = get_current_scope()
    try:
        entry = await get_ssh_store().save({
            "name": name, "host": host, "port": port, "username": username,
            "password": password, "key_path": key_path,
            "passphrase": passphrase, "description": description,
        })
        from core.log import log
        log(f"SSH 连接已添加: {name} ({username}@{host}:{port}) scope={scope}", tag="SSH")
        return json.dumps({
            "ok": True,
            "connection": {k: v for k, v in entry.items() if k not in ("password", "passphrase")},
            "hint": "配置已保存，使用 ssh_connect 建立连接",
        }, ensure_ascii=False)
    except ValueError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM)
    except Exception as e:
        return error_from_exception(e, action="SSH 添加连接")


@tool(name="ssh_remove", group="ssh")
async def ssh_remove(name: str) -> str:
    """删除 SSH 连接配置（同时断开对应连接）。

    Args:
        name: 要删除的连接名称
    """
    if error := _gate():
        return error
    try:
        removed = await get_ssh_manager().remove_profile(name)
        if not removed:
            return tool_error(f"连接不存在: {name}", cause=ErrorCause.NOT_FOUND)
        return json.dumps({"ok": True, "removed": name}, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="SSH 删除连接")
