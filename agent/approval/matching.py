"""工具参数与工作区路径的权限模式匹配。"""

from __future__ import annotations

import fnmatch
import json
import ntpath
import os
from typing import Any, Dict, List, Optional

from agent.utils.workspace import workspace_paths_port

# 各工具的"关键参数"提取器：参数模式匹配的比对对象
_ARG_KEYS: Dict[str, tuple] = {
    "run_shell_command": ("command",),
    "python_exec": ("code",),
    "read_file": ("file_path",),
    "write_file": ("file_path",),
    "edit_file": ("file_path",),
    "append_file": ("path",),
    "delete_file": ("path",),
    "move_file": ("src", "dst"),
    "copy_file": ("src", "dst"),
    "mkdir": ("path",),
    "web_fetch": ("url",),
    "web_request": ("url",),
}

# 路径类工具：匹配前先做与执行层一致的路径规范化（防 ./、../、~ 绕过）
_PATH_TOOLS = frozenset({
    "read_file", "write_file", "edit_file", "append_file",
    "delete_file", "move_file", "copy_file", "mkdir",
})


def match_path_pattern(value: str, pattern: str) -> bool:
    """路径段感知 glob 匹配（pattern 含 ``/`` 时使用，替代 fnmatch）。

    语义：``*``/``?``/``[...]`` 仅在单一路径段内生效，**不跨** ``/``；
    独立的 ``**`` 段匹配零或多层目录。防止 ``config/*`` 这类规则被
    ``config/../../etc`` 之类的多层路径意外命中。
    """
    if os.name == "nt" or ntpath.splitdrive(value)[0]:
        value, pattern = value.casefold(), pattern.casefold()
    return _match_path_segments(value.replace("\\", "/").split("/"),
                                pattern.replace("\\", "/").split("/"))


def _match_path_segments(value_segs: List[str], pattern_segs: List[str]) -> bool:
    if not pattern_segs:
        return not value_segs
    head = pattern_segs[0]
    if head == "**":
        return any(
            _match_path_segments(value_segs[i:], pattern_segs[1:])
            for i in range(len(value_segs) + 1)
        )
    return (
        bool(value_segs)
        and fnmatch.fnmatchcase(value_segs[0], head)
        and _match_path_segments(value_segs[1:], pattern_segs[1:])
    )


def extract_matchable_arg(tool_name: str, tool_args: Dict[str, Any]) -> str:
    """提取工具的关键参数用于 ``工具名(参数glob)`` 匹配。

    已知工具取其关键参数（命令/路径/URL），多值以空格连接；
    路径类参数先做与执行层一致的规范化
    （防止 ``./config/x``、``config/../config/x``、``~/x`` 绕过路径规则）；
    未知工具退化为参数的紧凑 JSON。
    """
    keys = _ARG_KEYS.get(tool_name)
    if keys:
        values = [str(tool_args.get(k, "")) for k in keys]
        if tool_name in _PATH_TOOLS:
            values = [_normalize_path_arg(v) for v in values]
        return " ".join(values).strip()
    return json.dumps(tool_args, ensure_ascii=False, sort_keys=True)


def matchable_arg_candidates(tool_name: str, tool_args: Optional[Dict[str, Any]]) -> List[str]:
    """参数模式匹配的候选文本：规范化绝对路径 + workspace 相对形式。

    规则既可写绝对 glob（``/data/**``）也可写相对 glob（``config/**``），
    两种写法等价生效，且都无法用 ``./``、``../``、``~`` 绕过。
    """
    if tool_args is None:
        return []
    return [candidate for group in _arg_candidate_groups(tool_name, tool_args) for candidate in group]


def _arg_candidate_groups(tool_name: str, tool_args: Dict[str, Any]) -> List[List[str]]:
    """每个路径独立保留绝对/相对候选，空格不是路径分隔符。"""
    if tool_name not in _PATH_TOOLS:
        return [[extract_matchable_arg(tool_name, tool_args)]]
    groups: List[List[str]] = []
    for key in _ARG_KEYS[tool_name]:
        value = _normalize_path_arg(str(tool_args.get(key, "")))
        candidates = [value]
        if workspace_paths_port.bound and value:
            root = workspace_paths_port.get().get_root()
            try:
                if os.path.commonpath((value, root)) == os.path.normpath(root):
                    candidates.append(os.path.relpath(value, root))
            except ValueError:
                pass  # 不同磁盘没有 workspace 相对形式
        groups.append(candidates)
    return groups


def matches_arg_pattern(tool_name: str, tool_args: Dict[str, Any], pattern: str,
                        *, require_all: bool = False) -> bool:
    """拒绝/审批匹配任一路径，放行必须覆盖所有路径（含移动/复制的目标）。"""
    matcher = match_path_pattern if "/" in pattern or "\\" in pattern else fnmatch.fnmatch
    matches = [any(matcher(value, pattern) for value in group)
               for group in _arg_candidate_groups(tool_name, tool_args)]
    return all(matches) if require_all else any(matches)


def _normalize_path_arg(path: str) -> str:
    """与文件工具执行层（_safe_path）完全一致的路径解析。"""
    if not path:
        return path
    try:
        if workspace_paths_port.bound:
            return workspace_paths_port.get().resolve(path)
    except Exception:
        pass  # 路径解析失败时按原文匹配（正常控制流，非异常）
    return path
