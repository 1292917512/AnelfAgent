"""Shell 沙箱预检 — 启发式拦截漂出 workspace 的写操作（P5-B6）。

定位：应用层防御纵深（非 seatbelt），与统一权限管线互补：
- 权限管线管"要不要问用户"（ask/deny 规则匹配命令文本）
- 本模块管"沙箱开启时绝对不能做"（向 workspace 外写文件）

设计要点：
- 只拦写操作（写动词参数 / 输出重定向目标），读操作放行
- /tmp、/dev/null 等良性路径放行（工具自身的 pwd 捕获也用临时目录）
- 命中时返回可操作的中文错误（模型可见 → 用户可知），绝无静默失败
"""

from __future__ import annotations

import os
import re
import shlex
from typing import List, Optional

# 写操作动词（其绝对路径参数受检）
_WRITE_VERBS = frozenset({
    "rm", "mv", "cp", "dd", "mkfs", "chmod", "chown", "chgrp",
    "ln", "tee", "touch", "mkdir", "install", "rsync", "rmdir",
    "truncate", "shred", "unlink",
})

# 良性绝对路径前缀（不受检）
_BENIGN_PREFIXES = ("/dev/null", "/dev/zero", "/tmp/", "/var/tmp/", "/dev/pts/")


def _is_benign(path: str, workspace: str, tmpdir: str) -> bool:
    if path.startswith(os.path.abspath(workspace) + os.sep):
        return True
    if any(path.startswith(p) for p in _BENIGN_PREFIXES):
        return True
    if tmpdir and path.startswith(os.path.abspath(tmpdir) + os.sep):
        return True
    return False


def _extract_redirect_targets(command: str) -> List[str]:
    """提取输出重定向目标（> >> 的目标路径）。"""
    targets: List[str] = []
    # 匹配 > 或 >> 后跟的路径（可选引号），排除 >& 文件描述符复制
    for m in re.finditer(r'(?<!&)>{1,2}(?!&)\s*([^\s;|&]+)', command):
        targets.append(m.group(1).strip('"\''))
    return targets


def check_command_safety(command: str, workspace: str) -> Optional[str]:
    """检查命令是否包含漂出 workspace 的写操作。

    Returns:
        None 表示通过；违规描述字符串表示应拦截。
    """
    workspace = os.path.abspath(workspace)
    import tempfile
    tmpdir = tempfile.gettempdir()

    # 1. 重定向目标
    for target in _extract_redirect_targets(command):
        if os.path.isabs(target) and not _is_benign(target, workspace, tmpdir):
            return f"输出重定向到 workspace 外的路径: {target}"

    # 2. 写动词参数
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    prev_verb = ""
    for token in tokens:
        verb = os.path.basename(token)
        if verb in _WRITE_VERBS:
            prev_verb = verb
            continue
        if prev_verb and token.startswith("/") and not _is_benign(token, workspace, tmpdir):
            return f"{prev_verb} 操作涉及 workspace 外的路径: {token}"
        # 遇到管道/分隔符重置动词上下文（shlex 会保留 | 等 token）
        if token in ("|", ";", "&&", "||"):
            prev_verb = ""
    return None
