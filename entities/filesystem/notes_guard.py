"""记忆便签键空间守卫 — shell / filesystem 工具与 notes 组工具的边界执行。

便签树（memory/*.md）归 notes 组工具专用，两套键空间互不相通：

- shell / filesystem 工具锚定 workspace（相对路径基于工具 cwd）
- 便签索引键锚定数据目录（memory/xxx.md 相对 ConfigPaths.MEMORY_DIR 父目录）

本模块是键空间事实的单一出处：便签根解析、键形态判定、命令 token 越界
识别与各工具的拦截/归因文案。防线分两层：

- 事前拦截：shell 命令中的便签键误用（注定 ENOENT 失败）与绕过 notes
  工具的便签树直接路径访问，在执行前拒绝并返回 notes 工具路由指引
- 事后归因：键形态但两处都不存在的命令放行自然失败，结果 notes 附键空间
  事实陈述（一次性教学，不拦截对 workspace 内真实文件的合法猜测）

token 级检测是尽力而为的行为护栏（无法区分路径用法与字符串提及），
不是安全边界；python_exec 的代码字符串不做 token 分析，由铁律约束。
"""

from __future__ import annotations

import os
import re
import shlex
from typing import List, NamedTuple, Optional

from entities._sdk import ErrorCause, tool_error

# 便签索引键形态（memory/*.md）：recall 结果 file 来源的标注形态
KEY_RE = re.compile(r"^memory/[\w./\-]+\.md$")

# 命中越界时给 AI 的工具路由指引（读取能力描述与 notes 组工具 schema 保持一致）
_NOTES_ROUTE_HINT = (
    "记忆便签一律用 notes 组工具：读取 read_memory_file（支持 offset/limit/tail_lines 分段）、"
    "列表 list_memory_files、追加 append_memory_file、修改 patch_memory_file / edit_memory_lines"
)


def notes_root() -> str:
    """便签树根目录（realpath；随 ANELF_DATA_DIR / data_root 指派变化，与 notes 工具同源）。"""
    from core.path import ConfigPaths

    return os.path.realpath(ConfigPaths.MEMORY_DIR)


def in_notes_root(path: str) -> bool:
    """解析后的绝对路径是否落在便签树内（含根目录本身）。"""
    root = notes_root()
    return path == root or path.startswith(root + os.sep)


def _split_tokens(command: str) -> List[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def find_key_token(command: str) -> Optional[str]:
    """命令中第一个便签键形态（memory/*.md）的 token；无命中返回 None。"""
    for token in _split_tokens(command):
        if KEY_RE.match(token):
            return token
    return None


class ShellViolation(NamedTuple):
    """shell 命令触碰便签键空间的越界事实。"""

    token: str
    # key_misuse = 便签索引键误作 shell 相对路径（注定 ENOENT）；
    # notes_path = 路径解析后落进便签树（绕过 notes 工具纪律的直接访问）
    kind: str


def find_shell_violation(command: str, cwd: str) -> Optional[ShellViolation]:
    """执行前检查命令 token 是否触碰便签树（注定失败或绕过 notes 工具）。

    逐 token 判定：
    - 解析后落进便签树 → notes_path（绝对路径或 cwd 漂移后的相对路径同罪）
    - 便签键形态且 cwd 下存在 → 合法 workspace 文件，放行
    - 便签键形态且便签树内存在对应文件 → key_misuse
    - 便签键形态但两处都不存在 → 放行自然失败，事后 key_space_note 归因
    """
    key_base = os.path.dirname(notes_root())
    for token in _split_tokens(command):
        resolved = token if os.path.isabs(token) else os.path.join(cwd, token)
        if in_notes_root(os.path.realpath(resolved)):
            return ShellViolation(token, "notes_path")
        if KEY_RE.match(token):
            if os.path.exists(os.path.join(cwd, token)):
                continue
            if os.path.exists(os.path.join(key_base, token)):
                return ShellViolation(token, "key_misuse")
    return None


def shell_violation_error(violation: ShellViolation) -> str:
    """shell 越界的结构化拒绝（guard 归因 + notes 工具路由指引）。"""
    if violation.kind == "key_misuse":
        message = (
            f"{violation.token} 是记忆便签索引键（锚定数据目录），不是 shell 相对路径"
            "（shell 工作目录是 workspace，该路径下不存在此文件）"
        )
    else:
        message = f"{violation.token} 指向记忆便签树，shell 不允许直接访问便签文件"
    return tool_error(
        message,
        cause=ErrorCause.PARAM,
        retryable=False,
        hint=_NOTES_ROUTE_HINT,
        guard="memory_notes",
        token=violation.token,
    )


def key_space_note(key: str) -> str:
    """键形态但两处都不存在的失败归因（键空间事实陈述，随失败结果 notes 附带）。"""
    return (
        f"注意: {key} 形如记忆便签索引键（锚定数据目录，非 shell 相对路径）；"
        "便签读写请用 notes 组工具（read_memory_file / list_memory_files）"
    )


def memory_note_hint(file_path: str, resolved: Optional[str] = None) -> Optional[str]:
    """识别指向记忆便签文件的路径，返回改用 notes 组工具的引导；未命中返回 None。

    精确命中才提示（不打扰 workspace 普通文件与其他路径）：
    - 绝对/解析后路径落在便签树内的 .md 文件；或
    - 相对路径形如便签索引键（memory/*.md）且对应便签真实存在。
    """
    root = notes_root()
    key_base = os.path.dirname(root)
    for p in (resolved, file_path):
        if not p or not os.path.isabs(p):
            continue
        real = os.path.realpath(p)
        if real.startswith(root + os.sep) and real.endswith(".md"):
            key = os.path.relpath(real, key_base)
            return (
                f"{file_path} 指向记忆便签文件（键 {key}），filesystem 组工具锚定 workspace 无法访问；"
                f"请改用 notes 组工具（read_memory_file / patch_memory_file 等）"
            )
    key = file_path.replace("\\", "/")
    if KEY_RE.match(key) and os.path.isfile(os.path.join(key_base, key)):
        return (
            f"{key} 是记忆便签索引键（文件在便签树内），非 workspace 相对路径；"
            "请改用 notes 组工具（read_memory_file / patch_memory_file 等）"
        )
    return None


def key_create_guard(file_path: str) -> Optional[str]:
    """拦截以便签索引键形态（memory/*.md）经 filesystem 工具新建文件的误用。

    便签键空间锚定数据目录，filesystem 工具锚定 workspace——该形态的新建几乎必然
    是想用 notes 组工具写便签，直接创建会在 workspace 下产生错位的 memory/ 目录。
    命中返回错误 JSON，未命中返回 None。
    """
    key = file_path.replace("\\", "/")
    if not KEY_RE.match(key):
        return None
    return tool_error(
        f"{key} 是记忆便签索引键形态，filesystem 组工具锚定 workspace，在此创建会与便签错位。",
        cause=ErrorCause.PARAM,
        retryable=False,
        hint="新建/修改记忆便签请用 notes 组工具（write_memory_file / append_memory_file）",
    )
