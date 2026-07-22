"""工作区路径解析 — 文件工具与权限层共用的唯一路径解析点。

抽取自 entities/filesystem/tools.py 的 _safe_path（解析部分），
供两处使用（保证权限匹配的解析结果与执行层完全一致，防绕过）：
- 文件工具执行前的沙箱路径解析
- 权限规则 ``工具名(路径glob)`` 匹配前的参数规范化
"""

from __future__ import annotations

import os


def get_workspace_config() -> str:
    """获取 workspace 根的配置原形（可能是相对路径，如 "workspace"）。"""
    try:
        from core.config import ConfigManager
        return str(ConfigManager.get("workspace_root", "workspace"))
    except Exception:
        return "workspace"


def get_workspace_root() -> str:
    """获取 workspace 根目录（配置驱动，绝对路径）。"""
    return os.path.abspath(get_workspace_config())


def sandbox_enabled() -> bool:
    """沙箱是否启用。"""
    try:
        from core.config import ConfigManager
        return bool(ConfigManager.get("sandbox_enabled", True))
    except Exception:
        return True


def resolve_workspace_path(path: str, workspace_root: str = "") -> str:
    """把工具入参路径解析为规范化绝对路径（不做沙箱检查）。

    - ``~`` 展开为用户目录
    - 相对路径基于 workspace 根解析（剥离重复的 workspace 前缀防双重嵌套，
      同时识别配置原形与目录名两种前缀形式）
    - ``.``/``..``/重复分隔符归一化
    """
    root = workspace_root or get_workspace_root()
    path = os.path.expanduser(path)
    if os.path.isabs(path):
        return os.path.normpath(path)
    norm = os.path.normpath(path)
    prefixes = {
        os.path.normpath(root),
        os.path.normpath(get_workspace_config()),
        os.path.basename(os.path.normpath(root)),
    }
    for prefix in sorted(prefixes, key=len, reverse=True):
        if norm == prefix:
            norm = ""
            break
        if norm.startswith(prefix + os.sep):
            norm = norm[len(prefix):].lstrip(os.sep)
            break
    return os.path.normpath(os.path.join(root, norm))


def check_sandbox(resolved: str, workspace_root: str = "") -> bool:
    """检查解析后的绝对路径是否在 workspace 内（按路径段边界，防 /workspace2 误判）。

    两侧均经 realpath 展开符号链接，防 workspace 内的符号链接逃逸到外部。
    """
    root = os.path.realpath(workspace_root or get_workspace_root())
    real = os.path.realpath(resolved)
    return real == root or real.startswith(root + os.sep)
