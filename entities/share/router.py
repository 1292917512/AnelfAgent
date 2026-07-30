"""文件分享实体的 HTTP 路由（自动挂载到 /api/entity/share）。

通过 web/server.py 的 _mount_entity_routers 扫描发现，
build_router() 返回的 APIRouter 自动挂载到 /api/entity/share。
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from core.log import log

from .schemas import (
    CreateShareRequest,
    DownloadLogListResult,
    ShareLinkListResult,
    ShareLinkOut,
    ShareStats,
)
from .store import build_download_url, get_public_base_url, get_share_store


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/links", response_model=ShareLinkListResult)
    async def list_links(
        status: str = Query("active", pattern="^(active|expired|revoked|all)$"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        query: str = Query(""),
    ) -> ShareLinkListResult:
        """列出分享链接（分页 + 状态/关键词过滤）。"""
        store = get_share_store()
        result = await store.list(status=status, page=page, page_size=page_size, query=query)
        return ShareLinkListResult(**result)

    @router.post("/links", response_model=ShareLinkOut)
    async def create_link(req: CreateShareRequest, request: Request) -> ShareLinkOut:
        """创建分享链接（WebUI 手动创建）。"""
        store = get_share_store()
        try:
            entry = await store.create(
                file_path=req.path,
                description=req.description,
                expires_in=req.expires_in,
                created_by="manual:webui",
                max_downloads=req.max_downloads,
            )
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e

        # 优先用配置的公网基址拼接完整 URL，未配置则回退 Request.url_for（多 host / 反代场景正确）
        base_url = get_public_base_url().strip()
        if base_url:
            entry["url"] = build_download_url(entry["token"], base_url)
        else:
            entry["url"] = str(request.url_for("download_share_file", token=entry["token"]))
        return ShareLinkOut(**entry)

    @router.delete("/links/{token}")
    async def revoke_link(token: str) -> dict:
        """撤销分享链接。"""
        store = get_share_store()
        entry = await store.revoke(token)
        if not entry:
            raise HTTPException(status_code=404, detail="链接不存在或已失效")
        return {"status": "ok", "token": token}

    @router.get("/stats", response_model=ShareStats)
    async def stats() -> ShareStats:
        """获取分享统计总览。"""
        store = get_share_store()
        return ShareStats(**(await store.stats()))

    @router.get("/logs", response_model=DownloadLogListResult)
    async def get_logs(
        token: str = Query(""),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ) -> DownloadLogListResult:
        """获取下载审计日志。"""
        store = get_share_store()
        result = await store.get_download_logs(
            token=token or None, page=page, page_size=page_size,
        )
        return DownloadLogListResult(**result)

    @router.get("/d/{token}", name="download_share_file")
    async def download(token: str, request: Request) -> FileResponse:
        """下载分享文件（token 鉴权，过期/撤销/次数耗尽返回 404）。"""
        store = get_share_store()
        entry = await store.get_by_token(token)
        if not entry or entry["status"] != "active":
            raise HTTPException(status_code=404, detail="链接不存在或已失效")
        if store.is_expired(entry):
            await store.mark_expired(token)
            raise HTTPException(status_code=404, detail="链接已过期")
        if store.is_exhausted(entry):
            await store.mark_exhausted(token)
            raise HTTPException(status_code=404, detail="链接下载次数已达上限")

        # 沙箱校验：复用 entities/filesystem 的统一路径解析
        from entities.filesystem.tools import _safe_path
        try:
            fp = _safe_path(entry["file_path"])
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        if not os.path.isfile(fp):
            raise HTTPException(status_code=404, detail="文件不存在")

        # 记录审计日志
        client_ip = request.client.host if request.client else ""
        user_agent = request.headers.get("user-agent", "")
        await store.log_download(
            token,
            ip=client_ip,
            user_agent=user_agent,
            file_name=entry["file_name"],
            file_size=entry["file_size"],
        )

        await store.touch_download(token)
        log(f"分享文件下载: {entry['file_name']} (token={token[:8]}..., ip={client_ip})", tag="分享")
        return FileResponse(
            fp,
            filename=entry["file_name"],
            content_disposition_type="attachment",
        )

    return router
