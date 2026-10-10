"""文件引用在消息标签和工作区链接之间转换。

模型看到经沙箱解析的 file/dir 路径标签，不预读文件；每条引用仅增加路径 token。
引用随用户消息进入历史追加区，不修改 stable/summary 缓存前缀。
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote, unquote

from core.tags import tag_label
from services.workspace import WorkspaceError, WorkspaceService

_LINK_OR_CODE = re.compile(
    r"(?P<code>"
    r"^ {0,3}(?P<ticks>`{3,})[^\n]*\n[\s\S]*?(?:^ {0,3}(?P=ticks)`*[ \t]*(?:\n|$)|\Z)"
    r"|^ {0,3}(?P<tildes>~{3,})[^\n]*\n[\s\S]*?(?:^ {0,3}(?P=tildes)~*[ \t]*(?:\n|$)|\Z)"
    r"|(?P<inline>`+)(?!`)[\s\S]*?(?<!`)(?P=inline)(?!`))"
    r"|\[(?:\\.|[^\]\\])+\]\(\./(?P<path>[^\s()]+)\)",
    re.MULTILINE,
)


def resolve_reference_path(path: str) -> tuple[Path, bool]:
    """解析工作区引用，返回沙箱内绝对路径与目录标记。"""
    root = "project" if path.startswith("project:") else "workspace"
    if root == "project":
        path = path[len("project:"):]
    is_dir = path.startswith("dir:")
    if is_dir:
        path = path[len("dir:"):]
    if (not path or path.startswith(("/", "\\")) or re.match(r"^[a-zA-Z]:", path)
            or any(ord(char) < 32 for char in path) or ".." in path.replace("\\", "/").split("/")):
        raise ValueError("文件引用必须位于对应的工作区或项目目录内")
    try:
        resolved = Path(WorkspaceService().resolve(path.replace("\\", "/"), root))
    except WorkspaceError as exc:
        raise ValueError(exc.detail) from exc
    return resolved, is_dir or resolved.is_dir()


def expand_file_references(message: str) -> str:
    """将正文中的工作区链接转为可直接调用工具的路径标签，保留代码片段。"""
    def replace(match: re.Match[str]) -> str:
        if match["code"] is not None:
            return match[0]
        resolved, is_dir = resolve_reference_path(unquote(match["path"], errors="strict"))
        return tag_label("dir" if is_dir else "file", resolved.as_posix())

    return _LINK_OR_CODE.sub(replace, message)


def display_reference_tag(key: str, value: str) -> str:
    """将可定位的文件标签还原为链接，其余标签保留可读值。"""
    if key not in {"file", "dir"}:
        return value
    path = Path(value)
    if not path.is_absolute():
        return value
    workspace = WorkspaceService()
    for root in ("workspace", "project"):
        try:
            relative = path.resolve().relative_to(Path(workspace.resolve_root(root)).resolve())
        except (ValueError, OSError):
            continue
        prefix = "project:" if root == "project" else ""
        prefix += "dir:" if key == "dir" else ""
        target = quote(prefix + relative.as_posix(), safe="/")
        name = path.name.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        return f"[{name}](./{target})"
    return value
