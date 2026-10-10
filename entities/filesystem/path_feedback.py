"""文件定位失败反馈：返回真实解析路径与允许范围内已存在的目录。

Model Experience:
- 模型在失败回执中看到解析路径、已验证的定位起点及列举/搜索指引，不自动替换目标。
- 每次定位失败增加约 100~200 token；仅检查祖先目录，不递归扫描或注入目录树。
- 内容随 tool 结果进入尾部动态区，不改变 stable/summary/conversation 前缀。
"""

from __future__ import annotations

import json
import os
from typing import Literal

from entities._sdk import ErrorCause, tool_error

from .paths import check_sandbox


def shell_path_hint(stderr: str) -> str | None:
    """为 Shell 报告的路径错误提供恢复指引，不猜测或改写命令中的路径。"""
    markers = (
        "no such file or directory", "not a directory",
        "cannot find the path specified", "cannot find the file specified",
        "系统找不到指定的路径", "系统找不到指定的文件",
    )
    if not any(marker in stderr.lower() for marker in markers):
        return None
    return (
        "错误输出涉及不存在的路径或目录类型不符；先核对 cwd 与 shell_cwd，"
        "再从已确认存在的上级目录列举或搜索，使用返回的实际路径。"
        "工作区内优先 list_directory/search_files；区外只读查询按 Shell 允许范围操作。"
        "若缺失的是解释器或依赖，先核对对应环境。不要重复原命令或继续猜路径。"
    )


def path_error(
    requested: str,
    resolved: str,
    workspace: str,
    *,
    sandbox: bool,
    expected: Literal["file", "directory"],
    hint: str | None = None,
    detail: str = "",
    code: int | None = None,
) -> str:
    """区分不存在与类型错误，并提供沙箱范围内已验证的目录作为恢复起点。"""
    if sandbox and not check_sandbox(resolved, workspace):
        return tool_error("目标不在工作区允许范围内", cause=ErrorCause.PERMISSION,
                          retryable=False, resolved=resolved)
    exists = os.path.exists(resolved)
    label = "文件" if expected == "file" else "目录"
    message = f"目标不是{label}: {requested}" if exists else f"{label}不存在: {requested}"
    directory = resolved
    existing_parent: str | None = None
    while not sandbox or check_sandbox(directory, workspace):
        if os.path.isdir(directory):
            existing_parent = directory
            break
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    if hint is None:
        if existing_parent is not None:
            argument = json.dumps(existing_parent, ensure_ascii=False)
            hint = (
                f"先用 list_directory(path={argument}) 列举，或在该目录用 search_files 定位；"
                "使用返回的实际路径，不要继续猜相似路径。"
            )
        else:
            hint = "未找到允许范围内的现存目录；先用 get_workspace_info 核对工作区位置与沙箱状态。"
    return tool_error(
        message + detail,
        cause=ErrorCause.PARAM if exists else ErrorCause.NOT_FOUND,
        retryable=False,
        hint=hint,
        resolved=resolved,
        existing_parent=existing_parent,
        code=code,
    )
