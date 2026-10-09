"""跨平台 Shell 测试命令：用当前解释器代替依赖系统预装的 Unix 工具。"""

import os
import shlex
import subprocess
import sys


def python_command(code: str) -> str:
    args = [sys.executable, "-u", "-c", code]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
