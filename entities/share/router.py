"""分享实体的 HTTP 路由（自动挂载到 /api/entity/share）。

通过 web/server.py 的 _mount_entity_routers 扫描发现，
build_router() 返回的 APIRouter 自动挂载到 /api/entity/share。

对外访问端点（无需登录）：
- GET /d/{token}: 文件下载（attachment 强制下载）
- GET /v/{token}: 预览页（media 内嵌渲染 / link 落地页 / file 重定向下载）
- GET /raw/{token}: 媒体原始字节（inline + Range，供预览页内嵌引用）
"""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from core.log import log

from .schemas import (
    CreateShareRequest,
    DownloadLogListResult,
    ShareLinkListResult,
    ShareLinkOut,
    ShareStats,
)
from .store import (
    SHARE_TYPE_FILE,
    SHARE_TYPE_LINK,
    SHARE_TYPE_MEDIA,
    build_download_url,
    build_view_url,
    get_public_base_url,
    get_share_store,
)
from .view_page import render_link_page, render_media_page, render_unavailable_page


def _attach_urls(entry: Dict[str, Any], base_url: str) -> Dict[str, Any]:
    """按分享类型填充主链接 url 与 download_url。

    file → 主链接即下载链接；media/link → 主链接为预览页，media 另带下载链接。
    """
    token = entry["token"]
    share_type = entry.get("share_type", SHARE_TYPE_FILE)
    if share_type == SHARE_TYPE_FILE:
        entry["url"] = build_download_url(token, base_url)
        entry["download_url"] = entry["url"]
    else:
        entry["url"] = build_view_url(token, base_url)
        if share_type == SHARE_TYPE_MEDIA:
            entry["download_url"] = build_download_url(token, base_url)
    return entry


async def _validate_entry(token: str) -> Dict[str, Any]:
    """校验分享条目可用性（不存在/过期/次数耗尽统一 404），过期即时落库。"""
    store = get_share_store()
    entry = await store.get_by_token(token)
    if not entry or entry["status"] != "active":
        raise HTTPException(status_code=404, detail="链接不存在或已失效")
    if store.is_expired(entry):
        await store.mark_expired(token)
        raise HTTPException(status_code=404, detail="链接已过期")
    if store.is_exhausted(entry):
        await store.mark_exhausted(token)
        raise HTTPException(status_code=404, detail="链接访问次数已达上限")
    return entry


def _resolve_file(entry: Dict[str, Any]) -> str:
    """沙箱校验并返回文件绝对路径，不存在则 404。"""
    from entities.filesystem.tools import _safe_path
    try:
        fp = _safe_path(entry["file_path"])
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="文件不存在")
    return fp


async def _record_access(entry: Dict[str, Any], request: Request) -> None:
    """记录审计日志并累加访问计数（下载与预览共用）。"""
    store = get_share_store()
    client_ip = request.client.host if request.client else ""
    await store.log_download(
        entry["token"],
        ip=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        file_name=entry["file_name"],
        file_size=entry["file_size"],
    )
    await store.touch_download(entry["token"])


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
        base_url = get_public_base_url().strip()
        for item in result["items"]:
            _attach_urls(item, base_url)
        return ShareLinkListResult(**result)

    @router.post("/links", response_model=ShareLinkOut)
    async def create_link(req: CreateShareRequest, request: Request) -> ShareLinkOut:
        """创建分享链接（WebUI 手动创建）。"""
        store = get_share_store()
        try:
            entry = await store.create(
                file_path=req.path,
                target_url=req.target_url,
                share_type=req.share_type,
                description=req.description,
                expires_in=req.expires_in,
                created_by="manual:webui",
                max_downloads=req.max_downloads,
            )
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        # 优先用配置的公网基址拼接完整 URL，未配置则回退 Request.url_for（多 host / 反代场景正确）
        base_url = get_public_base_url().strip()
        if base_url:
            _attach_urls(entry, base_url)
        else:
            share_type = entry.get("share_type", SHARE_TYPE_FILE)
            if share_type == SHARE_TYPE_FILE:
                entry["url"] = str(request.url_for("download_share_file", token=entry["token"]))
                entry["download_url"] = entry["url"]
            else:
                entry["url"] = str(request.url_for("view_share", token=entry["token"]))
                if share_type == SHARE_TYPE_MEDIA:
                    entry["download_url"] = str(
                        request.url_for("download_share_file", token=entry["token"]))
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
        """获取访问审计日志。"""
        store = get_share_store()
        result = await store.get_download_logs(
            token=token or None, page=page, page_size=page_size,
        )
        return DownloadLogListResult(**result)

    @router.get("/d/{token}", name="download_share_file")
    async def download(token: str, request: Request) -> FileResponse:
        """下载分享文件（token 鉴权，过期/撤销/次数耗尽返回 404）。"""
        entry = await _validate_entry(token)
        fp = _resolve_file(entry)

        await _record_access(entry, request)
        log(f"分享文件下载: {entry['file_name']} (token={token[:8]}...)", tag="分享")
        return FileResponse(
            fp,
            filename=entry["file_name"],
            content_disposition_type="attachment",
        )

    @router.get("/raw/{token}")
    async def raw(token: str) -> FileResponse:
        """媒体原始字节（inline + Range，供预览页内嵌引用，不重复计数）。

        私有短缓存：token 即内容键（内容变更会产生新 token），5 分钟内
        聊天卡片/预览页的重复引用直接命中浏览器缓存，减少回源。
        """
        entry = await _validate_entry(token)
        fp = _resolve_file(entry)
        headers: Dict[str, str] = {"Cache-Control": "private, max-age=300"}
        # HTML 内联渲染走 CSP sandbox：禁脚本/表单/弹窗 + 独立源，防存储型 XSS
        if entry.get("media_kind") == "html":
            headers["Content-Security-Policy"] = "sandbox"
        return FileResponse(
            fp,
            filename=entry["file_name"],
            content_disposition_type="inline",
            headers=headers,
        )

    @router.get("/v/{token}", name="view_share")
    async def view(token: str, request: Request) -> Any:
        """预览页：media 内嵌渲染 / link 落地页 / file 重定向下载。"""
        from core.config import get_config_bool

        try:
            entry = await _validate_entry(token)
        except HTTPException as e:
            # 匿名访问场景返回友好提示页而非 JSON 错误
            return HTMLResponse(
                render_unavailable_page(str(e.detail)), status_code=e.status_code)

        share_type = entry.get("share_type", SHARE_TYPE_FILE)
        if share_type == SHARE_TYPE_FILE:
            return RedirectResponse(
                url=f"/api/entity/share/d/{token}", status_code=302)

        await _record_access(entry, request)

        if share_type == SHARE_TYPE_LINK:
            embed_enabled = get_config_bool("share_view_embed_enabled", True)
            log(f"分享网址访问: {entry['target_url']} (token={token[:8]}...)", tag="分享")
            return HTMLResponse(render_link_page(entry, embed_enabled))

        # media：页面本身计数一次，raw 资源请求不再重复计数
        base_url = get_public_base_url().strip()
        raw_url = f"/api/entity/share/raw/{token}"
        download_url = build_download_url(token, base_url)
        log(f"分享媒体预览: {entry['file_name']} (token={token[:8]}...)", tag="分享")
        return HTMLResponse(render_media_page(entry, raw_url, download_url))

    return router
