"""系统信息服务 -- 系统、Python 环境、开发工具检测（不依赖 Qt）。"""

from __future__ import annotations

import os
import platform
import sys
from typing import Any, Dict, List

from core.command import which_tool, get_tool_version


def get_system_info() -> Dict[str, Any]:
    """获取操作系统和硬件信息。"""
    info: Dict[str, Any] = {
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.architecture()[0],
        "processor": platform.processor() or platform.machine(),
        "user": os.getenv("USER") or os.getenv("USERNAME") or "未知",
        "home": os.path.expanduser("~"),
    }

    shell = os.getenv("SHELL", "")
    if not shell and platform.system() == "Windows":
        shell = "PowerShell"
    info["shell"] = os.path.basename(shell) if shell else "未知"

    try:
        import psutil
        info["cpu_physical"] = psutil.cpu_count(logical=False)
        info["cpu_logical"] = psutil.cpu_count(logical=True)
        mem = psutil.virtual_memory()
        info["memory_total_gb"] = round(mem.total / (1024 ** 3), 1)
        info["memory_used_gb"] = round(mem.used / (1024 ** 3), 1)
        try:
            disk = psutil.disk_usage("C:" if platform.system() == "Windows" else "/")
            info["disk_total_gb"] = round(disk.total / (1024 ** 3), 1)
            info["disk_used_gb"] = round(disk.used / (1024 ** 3), 1)
        except Exception:
            pass
    except ImportError:
        pass

    return info


def get_python_info() -> Dict[str, Any]:
    """获取 Python 环境信息。"""
    in_venv = hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )
    return {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
        "prefix": sys.prefix,
        "in_venv": in_venv,
        "venv_path": sys.prefix if in_venv else None,
        "sys_path": sys.path[:10],
    }


_DEV_TOOLS = {
    "Git": "git",
    "Python": "python",
    "pip": "pip",
    "Node.js": "node",
    "npm": "npm",
    "yarn": "yarn",
    "Docker": "docker",
    "curl": "curl",
    "wget": "wget",
    "VSCode": "code",
    "vim": "vim",
    "zsh": "zsh",
    "conda": "conda",
    "pipx": "pipx",
    "uv": "uv",
}


def get_dev_tools() -> List[Dict[str, Any]]:
    """检测系统上已安装的开发工具。"""
    results: List[Dict[str, Any]] = []
    for name, command in _DEV_TOOLS.items():
        entry: Dict[str, Any] = {"name": name, "command": command, "installed": False}
        try:
            path = which_tool(command)
            if path:
                entry["installed"] = True
                entry["path"] = path
                version = get_tool_version(command)
                if version:
                    entry["version"] = version.split("\n")[0][:60]
        except Exception:
            pass
        results.append(entry)
    return results
