"""Shell 会话状态 — 每 scope 的 cwd 持久化与超限输出落盘。

移植自 Claude Code Bash 工具语义（``src/utils/Shell.ts``、``bashProvider.ts``）：
- 每条命令在独立进程中执行（无持久 shell），但工作目录跨命令持久
- cwd 持久通过命令尾部追加 ``pwd -P`` 捕获实现
- cwd 漂出 workspace 或被删除时自动重置并附注
- 模型可见输出有上限，超限完整输出落盘，返回预览 + 路径
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import uuid
from typing import Dict, Optional

from entities._sdk import get_current_scope

# 模型可见输出上限（对齐 Claude Code BASH_MAX_OUTPUT_LENGTH 默认 30000）
MAX_OUTPUT_CHARS = 30000
# 落盘预览大小（对齐 Claude Code persisted-output 预览 2KB）
PREVIEW_CHARS = 2048

_cwds: Dict[str, str] = {}
_lock = threading.Lock()


def get_cwd(workspace_root: str, scope: str = "", sandbox: bool = True) -> str:
    """获取当前 scope 的 shell 工作目录（默认 workspace 根）。

    sandbox 开启时，目录失效或漂出 workspace 自动重置；关闭时仅检查目录存在。
    """
    scope = scope or get_current_scope()
    root = os.path.abspath(workspace_root)
    with _lock:
        cwd = _cwds.get(scope, root)
    drifted = (cwd != root and not cwd.startswith(root + os.sep)) if sandbox else False
    if drifted or not os.path.isdir(cwd):
        cwd = root if os.path.isdir(root) else os.path.expanduser("~")
        with _lock:
            _cwds[scope] = cwd
    return cwd


def set_cwd(cwd: str, workspace_root: str, scope: str = "", sandbox: bool = True) -> bool:
    """记录命令执行后的 cwd。漂出约束范围时重置，返回是否被重置。"""
    scope = scope or get_current_scope()
    root = os.path.abspath(workspace_root)
    cwd = os.path.abspath(cwd)
    drifted = (cwd != root and not cwd.startswith(root + os.sep)) if sandbox else False
    if drifted or not os.path.isdir(cwd):
        fallback = root if os.path.isdir(root) else os.path.expanduser("~")
        with _lock:
            _cwds[scope] = fallback
        return True
    with _lock:
        _cwds[scope] = cwd
    return False


def wrap_command_capture_pwd(command: str) -> "tuple[str, str]":
    """在命令尾部追加 pwd 捕获，返回 (包装后的命令, pwd 临时文件路径)。

    保留原命令的退出码。仅用于 POSIX shell。
    """
    fd, pwd_file = tempfile.mkstemp(prefix="anelf_pwd_", suffix=".txt")
    os.close(fd)
    wrapped = (
        f"{command}\n"
        f"__anelf_ec=$?\n"
        f'pwd -P > "{pwd_file}"\n'
        f"exit $__anelf_ec"
    )
    return wrapped, pwd_file


def read_captured_pwd(pwd_file: str) -> Optional[str]:
    """读取捕获的 pwd 并清理临时文件。"""
    try:
        with open(pwd_file, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None
    finally:
        try:
            os.unlink(pwd_file)
        except OSError:
            pass


def persist_output(output: str, workspace_root: str) -> str:
    """把超限输出落盘到 workspace/.tool-results/，返回文件路径。"""
    out_dir = os.path.join(os.path.abspath(workspace_root), ".tool-results")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"shell-{int(time.time())}-{uuid.uuid4().hex[:8]}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(output)
    return path


def truncate_or_persist(output: str, workspace_root: str) -> "tuple[str, Optional[str]]":
    """输出超上限时落盘，返回 (模型可见文本, 落盘路径或 None)。"""
    if len(output) <= MAX_OUTPUT_CHARS:
        return output, None
    path = persist_output(output, workspace_root)
    preview = output[:PREVIEW_CHARS]
    visible = (
        f"<persisted-output>\n"
        f"输出过大（{len(output)} 字符），完整输出已保存到: {path}\n"
        f"预览（前 {PREVIEW_CHARS} 字符）:\n{preview}\n"
        f"</persisted-output>\n"
        f"可使用 read_file 配合 offset/limit 查看完整内容。"
    )
    return visible, path
