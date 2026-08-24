"""系统工具服务 -- 系统信息、Python 环境、Git 配置（封装 entities.system）。

entities.system 内部串行起大量 subprocess，均为同步阻塞实现，
本服务的异步方法一律经 asyncio.to_thread 移出事件循环。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List


class SystemService:
    """系统工具服务（Web 侧入口）。"""

    # ------------------------------------------------------------------
    # 系统信息
    # ------------------------------------------------------------------

    @staticmethod
    async def get_info() -> Dict[str, Any]:
        """聚合系统信息 / Python 信息 / 开发工具探测。"""
        from entities.system.info_service import (
            get_dev_tools,
            get_python_info,
            get_system_info,
        )
        system, python, tools = await asyncio.gather(
            asyncio.to_thread(get_system_info),
            asyncio.to_thread(get_python_info),
            asyncio.to_thread(get_dev_tools),
        )
        return {
            "system": system,
            "python": python,
            "tools": tools,
        }

    # ------------------------------------------------------------------
    # Python 环境
    # ------------------------------------------------------------------

    @staticmethod
    async def get_python_status() -> Dict[str, Any]:
        """返回 Python 环境状态。"""
        from entities.system.python_service import get_python_status
        return await asyncio.to_thread(get_python_status)

    @staticmethod
    async def get_installed_packages() -> List[Dict[str, str]]:
        """返回已安装包列表。"""
        from entities.system.python_service import get_installed_packages
        return await asyncio.to_thread(get_installed_packages)

    @staticmethod
    async def set_pip_mirror(mirror_name: str) -> Dict[str, Any]:
        """设置 pip 镜像源。"""
        from entities.system.python_service import set_pip_mirror
        result = await asyncio.to_thread(set_pip_mirror, mirror_name)
        return {"success": result.ok, "output": result.stdout or result.stderr}

    @staticmethod
    async def get_pip_config() -> Dict[str, Any]:
        """返回 pip 配置。"""
        from entities.system.python_service import get_pip_config
        return await asyncio.to_thread(get_pip_config)

    # ------------------------------------------------------------------
    # Git 配置
    # ------------------------------------------------------------------

    @staticmethod
    async def get_git_config() -> Dict[str, str]:
        """返回 Git 用户配置。"""
        from entities.system.git_service import get_user_config
        return await asyncio.to_thread(get_user_config)

    @staticmethod
    async def set_git_config(key: str, value: str) -> Dict[str, Any]:
        """设置 Git 配置项。"""
        from entities.system.git_service import git_config_set
        ok, msg = await asyncio.to_thread(git_config_set, key, value)
        return {"ok": ok, "message": msg}

    @staticmethod
    async def set_git_proxy(http_proxy: str, https_proxy: str) -> Dict[str, Any]:
        """设置 Git 代理。"""
        from entities.system.git_service import set_proxy
        ok, msg = await asyncio.to_thread(set_proxy, http_proxy, https_proxy)
        return {"ok": ok, "message": msg}

    @staticmethod
    async def unset_git_proxy() -> Dict[str, Any]:
        """移除 Git 代理。"""
        from entities.system.git_service import unset_proxy
        ok, msg = await asyncio.to_thread(unset_proxy)
        return {"ok": ok, "message": msg}

    @staticmethod
    async def test_github_connectivity() -> Dict[str, Any]:
        """测试 GitHub 连通性。"""
        from entities.system.git_service import test_github_connectivity
        return await asyncio.to_thread(test_github_connectivity)
