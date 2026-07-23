"""环境信息实体 — 系统信息、Python 环境、Git 管理、日志查询。

自检策略（模块加载时执行，极低开销）：
- Git 工具：检测到 git 命令才注册，避免在无 git 环境下污染工具池
"""

from __future__ import annotations

import json
import os
import platform
import shutil

from entities._sdk import tool, entity

_GIT_AVAILABLE = shutil.which("git") is not None

entity("environment", "环境信息 - 系统信息、工作区路径、Python 环境、Git 配置、日志查询")


# ── 系统信息 ─────────────────────────────────────────────────────────

@tool(name="get_workspace_info", group="environment")
def get_workspace_info() -> str:
    """获取工作区路径信息：工作区根目录绝对路径、Shell 当前工作目录、平台与沙箱状态。

    文件工具的相对路径与 Shell 的初始工作目录均基于工作区根目录；
    执行文件/命令操作前如不确定当前位置，可先调用本工具确认，避免猜测系统路径。
    """
    try:
        from core.path import workspace_root

        root = os.path.abspath(workspace_root())
        shell_cwd = root
        sandbox = True
        try:
            from entities.filesystem import shell_state
            from entities.filesystem import tools as fs_tools

            fs_tools._load_config()
            sandbox = fs_tools._SANDBOX
            shell_cwd = shell_state.get_cwd(root, sandbox=sandbox)
        except Exception:
            pass
        return json.dumps({
            "workspace_root": root,
            "shell_cwd": shell_cwd,
            "platform": f"{platform.system().lower()} ({platform.machine()})",
            "sandbox_enabled": sandbox,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="get_system_info", group="environment")
def get_system_info() -> str:
    """获取当前操作系统的基本信息，包括系统类型、版本、架构、主机名等。"""
    try:
        info = {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "hostname": platform.node(),
            "python_version": platform.python_version(),
            "cwd": os.getcwd(),
        }
        return json.dumps(info, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ── Python 环境 ──────────────────────────────────────────────────────

@tool(name="get_python_status", group="environment")
def get_python_status() -> str:
    """获取当前系统的 Python 环境状态，包括版本、虚拟环境、包管理器（uv/pip/conda）等。

    若结果中 managed_by 为 "uv"（uv 管理的 venv 默认不含 pip，属正常状态），
    安装依赖必须使用 uv add（写入 pyproject.toml/uv.lock）或 uv pip install，
    禁止 pip install / ensurepip——pip 安装的包不受 uv.lock 追踪，会被 uv sync 清除。
    """
    try:
        from entities.system.python_service import get_python_status as _get
        return json.dumps(_get(), ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="list_python_packages", group="environment")
def list_python_packages() -> str:
    """列出当前 Python 环境中已安装的所有包及其版本（pip 环境走 pip list，uv 环境自动回退 uv pip list）。"""
    try:
        from entities.system.python_service import get_installed_packages
        packages = get_installed_packages()
        if len(packages) > 100:
            return json.dumps({
                "packages": packages[:100],
                "total": len(packages),
                "note": "仅显示前 100 个",
            }, ensure_ascii=False)
        return json.dumps({"packages": packages, "total": len(packages)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="get_pip_mirror_info", group="environment")
def get_pip_mirror_info() -> str:
    """获取当前 pip 镜像源配置信息（仅适用于 pip 管理的环境；uv 管理的环境镜像走 uv 索引配置）。"""
    try:
        from entities.system.python_service import get_pip_config
        return json.dumps(get_pip_config(), ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ── Git 管理（仅在检测到 git 命令时注册）────────────────────────────

if _GIT_AVAILABLE:
    @tool(name="get_git_config", group="environment")
    def get_git_config() -> str:
        """获取 Git 全局配置，包括用户名、邮箱、代理设置等。"""
        try:
            from entities.system.git_service import get_user_config
            return json.dumps(get_user_config(), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool(name="set_git_config", group="environment")
    def set_git_config(key: str, value: str) -> str:
        """设置 Git 全局配置项。

        Args:
            key: 配置键名，如 user.name、user.email、http.proxy
            value: 配置值
        """
        try:
            from entities.system.git_service import git_config_set
            ok, msg = git_config_set(key, value)
            return json.dumps({"ok": ok, "message": msg}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool(name="unset_git_config", group="environment")
    def unset_git_config(key: str) -> str:
        """删除 Git 全局配置项。

        Args:
            key: 要删除的配置键名
        """
        try:
            from entities.system.git_service import git_config_unset
            ok, msg = git_config_unset(key)
            return json.dumps({"ok": ok, "message": msg}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool(name="set_git_proxy", group="environment")
    def set_git_proxy(http_proxy: str = "", https_proxy: str = "") -> str:
        """设置 Git 代理。

        Args:
            http_proxy: HTTP 代理地址
            https_proxy: HTTPS 代理地址，留空则与 http_proxy 相同
        """
        try:
            from entities.system.git_service import set_proxy
            ok, msg = set_proxy(http_proxy=http_proxy, https_proxy=https_proxy or http_proxy)
            return json.dumps({"ok": ok, "message": msg}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool(name="unset_git_proxy", group="environment")
    def unset_git_proxy() -> str:
        """清除 Git 的 HTTP 和 HTTPS 代理配置。"""
        try:
            from entities.system.git_service import unset_proxy
            ok, msg = unset_proxy()
            return json.dumps({"ok": ok, "message": msg}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool(name="test_github_connection", group="environment")
    def test_github_connection() -> str:
        """测试当前网络到 GitHub 的连通性。"""
        try:
            from entities.system.git_service import test_github_connectivity
            return json.dumps(test_github_connectivity(), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


# ── 日志查询 ─────────────────────────────────────────────────────────

@tool(name="query_logs", group="environment")
def query_logs(keyword: str = "", tag: str = "", level: str = "", limit: int = 20) -> str:
    """查询系统近期日志（内存缓冲区）。可按关键词、标签、级别过滤。

    Args:
        keyword: 日志内容关键词过滤
        tag: 标签过滤（如 mind、media、llm）
        level: 级别过滤（DEBUG/INFO/WARNING/ERROR）
        limit: 返回条数上限，默认 20
    """
    try:
        from core.log import query_log_buffer
        logs = query_log_buffer(
            level=level or None,
            tag=tag or None,
            keyword=keyword or None,
            limit=limit,
        )
        return json.dumps({"count": len(logs), "logs": logs}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
