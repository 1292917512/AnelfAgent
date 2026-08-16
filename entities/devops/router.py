"""运维实体的 HTTP 路由（自动挂载到 /api/entity/devops）。

通过 web/server.py 的 _mount_entity_routers 扫描发现，
与 AI 工具（tools.py）共用 service.py 的同一实现。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter

from . import service


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/restart")
    async def restart() -> Dict[str, Any]:
        """优雅重启应用（由外层启动脚本按退出码 42 重新拉起）。"""
        return service.request_restart()

    @router.post("/build-restart")
    async def build_restart() -> Dict[str, Any]:
        """后台构建前端，成功后自动重启；面板轮询 /build-state 跟踪进度。"""
        if not service.start_build_and_restart():
            return {"ok": False, "error": "build_in_progress"}
        return {"ok": True, "building": True}

    @router.get("/build-state")
    async def build_state() -> Dict[str, Any]:
        """查询前端构建状态（building / 最近一次构建结果）。"""
        return service.get_build_state()

    @router.get("/crash-info")
    async def crash_info() -> Dict[str, Any]:
        """查询最近一次进程崩溃信息（守护循环落盘的崩溃状态 + 系统崩溃报告）。"""
        return await asyncio.to_thread(service.get_crash_info)

    @router.post("/update")
    async def update() -> Dict[str, Any]:
        """从远程仓库拉取项目最新代码（git pull --ff-only）。"""
        return await asyncio.to_thread(service.git_pull)

    @router.post("/update-restart")
    async def update_restart() -> Dict[str, Any]:
        """拉取最新代码后后台构建前端并重启；拉取失败/冲突则不进入构建。"""
        pull = await asyncio.to_thread(service.git_pull)
        if not pull["ok"]:
            return pull
        if not service.start_build_and_restart():
            return {"ok": False, "error": "build_in_progress",
                    "pull_result": pull.get("pull_result")}
        return {"ok": True, "building": True, "pull_result": pull.get("pull_result")}

    return router
