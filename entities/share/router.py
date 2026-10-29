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
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from core.http_endpoints import self_authenticated
from core.log import log

from .schemas import (
    CreateShareRequest,
    DownloadLogListResult,
    ShareLinkListResult,
    ShareLinkOut,
    ShareStats,
)
from .store import (
    ACCESS_SECONDS,
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
    """校验链接存在、未撤销且未过期；额度在领取访问时原子校验。"""
    store = get_share_store()
    entry = await store.get_by_token(token)
    if not entry or entry["status"] != "active":
        raise HTTPException(status_code=404, detail="链接不存在或已失效")
    if store.is_expired(entry):
        await store.mark_expired(token)
        raise HTTPException(status_code=404, detail="链接已过期")
    return entry


def _resolve_file(entry: Dict[str, Any]) -> str:
    """沙箱校验并返回文件绝对路径，不存在则 404。"""
    from entities.filesystem.tools import safe_path
    try:
        fp = safe_path(entry["file_path"])
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="文件不存在")
    return fp


async def _access(entry: Dict[str, Any], request: Request, *, reuse: str = "") -> str:
    """资源请求可复用已领取的访问；页面导航始终领取新额度。"""
    store = get_share_store()
    token = entry["token"]
    if reuse:
        if store.valid_access(token, reuse):
            return reuse
        raise HTTPException(404, "访问凭据无效或已过期")
    if not await store.claim_access(
        token, ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    ):
        raise HTTPException(404, "链接已失效或访问次数已达上限")
    return store.issue_access(token)


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
    @self_authenticated
    async def download(token: str, request: Request) -> FileResponse:
        """下载分享文件（token 鉴权，过期/撤销/次数耗尽返回 404）。"""
        entry = await _validate_entry(token)
        fp = _resolve_file(entry)

        await _access(entry, request, reuse=request.query_params.get("access", ""))
        log(f"分享文件下载: {entry['file_name']} (token={token[:8]}...)", tag="分享")
        return FileResponse(
            fp,
            filename=entry["file_name"],
            content_disposition_type="attachment",
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @router.get("/raw/{token}")
    @self_authenticated
    async def raw(token: str, request: Request) -> FileResponse:
        """媒体原始字节与 Range 共用短期访问凭据；直访也须领取额度。"""
        entry = await _validate_entry(token)
        fp = _resolve_file(entry)
        cookie = request.cookies.get("_share_access", "")
        if cookie and not get_share_store().valid_access(token, cookie):
            cookie = ""
        access = await _access(entry, request, reuse=request.query_params.get("access", "") or cookie)
        response = FileResponse(
            fp, filename=entry["file_name"], content_disposition_type="inline",
            headers={"Cache-Control": "no-store", "Content-Security-Policy": "sandbox",
                     "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"},
        )
        response.set_cookie(
            "_share_access", access, max_age=ACCESS_SECONDS, httponly=True,
            secure=request.url.scheme == "https", samesite="lax", path=request.url.path,
        )
        return response

    @router.get("/v/{token}", name="view_share")
    @self_authenticated
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

        try:
            if share_type == SHARE_TYPE_MEDIA:
                _resolve_file(entry)
            access = await _access(entry, request)
        except HTTPException as exc:
            return HTMLResponse(render_unavailable_page(str(exc.detail)), status_code=exc.status_code)

        if share_type == SHARE_TYPE_LINK:
            embed_enabled = get_config_bool("share_view_embed_enabled", True)
            log(f"分享网址访问: {entry['target_url']} (token={token[:8]}...)", tag="分享")
            return HTMLResponse(render_link_page(entry, embed_enabled), headers={"Cache-Control": "no-store"})

        # media：页面本身计数一次，raw 资源请求不再重复计数
        base_url = get_public_base_url().strip()
        query = urlencode({"access": access})
        raw_url = f"/api/entity/share/raw/{token}?{query}"
        download_url = f"{build_download_url(token, base_url)}?{query}"
        log(f"分享媒体预览: {entry['file_name']} (token={token[:8]}...)", tag="分享")
        return HTMLResponse(render_media_page(entry, raw_url, download_url),
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    return router
