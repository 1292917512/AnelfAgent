"""频道 WebUI 同源反向代理 — 将频道内嵌管理界面（如 NapCat WebUI）代理到本站路径。

外部浏览器只需访问本站即可打开运行在回环/内网地址上的频道 WebUI：
HTML/JS/CSS 响应中的绝对路径被重写为代理前缀，Location 与 Set-Cookie 同步改写，
WebSocket 连接经 aiohttp 双向桥接。代理位于 /api/* 密码保护之下，
转发目标来自管理员配置的频道 webui_url，不构成用户可控的 SSRF 面。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import httpx
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse, Response

from core.log import log
from services import AdapterService

router = APIRouter(prefix="/channels", tags=["channel-webui"])

_adapter_svc = AdapterService()

_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

# 逐跳头禁止转发（RFC 2616 13.5.1）+ host/content-length 由 httpx 重建
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
})

# 需要路径重写的文本响应类型
_REWRITE_TYPES = ("text/html", "javascript", "text/css")

_http_client: Optional[httpx.AsyncClient] = None


def _client() -> httpx.AsyncClient:
    """复用的代理 HTTP 客户端（不跟随重定向，Location 由代理改写）。"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            follow_redirects=False,
        )
    return _http_client


def _resolve_target(channel_id: str) -> Optional[Tuple[str, str, str]]:
    """解析频道 WebUI 转发目标，返回 (origin, base_path, base_query)；未配置或非法时返回 None。"""
    raw = _adapter_svc.get_channel_webui_url(channel_id)
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    return origin, parts.path.lstrip("/"), parts.query


def _build_upstream_url(origin: str, base_path: str, path: str, query: str) -> str:
    """拼接源站 URL；query 为已合并的最终查询串，path 为空时落到配置地址自带路径。"""
    url = f"{origin}/{path or base_path}"
    if query:
        url += f"?{query}"
    return url


def _rewrite_text(text: str, prefix: str) -> str:
    """将文本响应中的站内绝对路径重写为代理前缀路径（带前缀守卫，不重复重写）。"""
    guard = r"(?!" + re.escape(prefix) + r"/)"
    quoted = re.compile(r"""(?P<q>["'])""" + guard + r"/(?P<seg>webui|api)(?P<rest>/|(?P=q))")
    text = quoted.sub(
        lambda m: f'{m.group("q")}{prefix}/{m.group("seg")}{m.group("rest")}',
        text,
    )
    attrs = re.compile(r'(?P<a>href="|src="|action=")' + guard + "/")
    text = attrs.sub(lambda m: f'{m.group("a")}{prefix}/', text)
    css_url = re.compile(r"url\(" + guard + "/")
    return css_url.sub(f"url({prefix}/", text)


def _rewrite_location(location: str, origin: str, prefix: str) -> str:
    """重写重定向 Location，使跳转始终留在代理路径内。"""
    if location.startswith(origin):
        return prefix + location[len(origin):]
    if location.startswith("/"):
        return prefix + location
    return location


def _rewrite_set_cookie(value: str, prefix: str) -> str:
    """将 Cookie 的 Path 限定到代理前缀下，避免污染本站同源 Cookie。"""
    if re.search(r"(?i)(?:^|;\s*)path=", value):
        return re.sub(r"(?i)((?:^|;\s*)path=)/[^;]*", rf"\g<1>{prefix}/", value)
    return value + f"; Path={prefix}/"


@router.api_route("/{channel_id}/webui", methods=_PROXY_METHODS)
@router.api_route("/{channel_id}/webui/", methods=_PROXY_METHODS)
@router.api_route("/{channel_id}/webui/{path:path}", methods=_PROXY_METHODS)
async def proxy_channel_webui(channel_id: str, request: Request, path: str = "") -> Response:
    """将请求转发到频道配置的 WebUI 源站，并按需重写响应内容。"""
    target = _resolve_target(channel_id)
    if target is None:
        return JSONResponse({"error": "该频道未配置 WebUI 地址"}, status_code=404)
    origin, base_path, base_query = target
    prefix = f"/api/channels/{channel_id}/webui"

    # 浏览器未带 query 且请求的是入口路径时，回落到配置地址自带的 query（如 NapCat 的 ?token=）
    merged_query = request.url.query or (base_query if not path else "")
    upstream_url = _build_upstream_url(origin, base_path, path, merged_query)
    headers = {
        key: value for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP and key.lower() != "cookie"
    }
    # 本站 Cookie 不下发到源站；强制 identity 以便对文本响应做路径重写
    headers["accept-encoding"] = "identity"

    try:
        upstream = await _client().request(
            request.method,
            upstream_url,
            content=await request.body(),
            headers=headers,
        )
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"WebUI 目标不可达: {exc}"}, status_code=502)

    resp_headers = {
        key: value for key, value in upstream.headers.items()
        if key.lower() not in _HOP_BY_HOP
        and key.lower() not in ("content-encoding", "content-length", "set-cookie", "location")
    }
    if "location" in upstream.headers:
        location = _rewrite_location(upstream.headers["location"], origin, prefix)
        # 源站重定向丢失 query 时补回（如 NapCat 靠 ?token= 自动登录）
        if merged_query and "?" not in location:
            location += f"?{merged_query}"
        resp_headers["location"] = location

    content = upstream.content
    content_type = upstream.headers.get("content-type", "")
    if any(kind in content_type for kind in _REWRITE_TYPES):
        text = content.decode(upstream.encoding or "utf-8", errors="replace")
        content = _rewrite_text(text, prefix).encode("utf-8")

    response = Response(
        content=content,
        status_code=upstream.status_code,
        headers=resp_headers,
    )
    for set_cookie in upstream.headers.get_list("set-cookie"):
        response.raw_headers.append((
            b"set-cookie",
            _rewrite_set_cookie(set_cookie, prefix).encode("latin-1"),
        ))
    return response


@router.websocket("/{channel_id}/webui")
@router.websocket("/{channel_id}/webui/")
@router.websocket("/{channel_id}/webui/{path:path}")
async def proxy_channel_webui_ws(websocket: WebSocket, channel_id: str, path: str = "") -> None:
    """桥接频道 WebUI 的 WebSocket 连接（鉴权与 HTTP 代理一致）。"""
    # BaseHTTPMiddleware 不覆盖 ws scope，这里手动执行与 _AuthMiddleware 同等的校验
    from web.server import _load_auth_password, _make_token

    password = _load_auth_password()
    if password and websocket.cookies.get("_anelf_token", "") != _make_token(password):
        await websocket.close(code=4401)
        return

    target = _resolve_target(channel_id)
    if target is None:
        await websocket.close(code=4404)
        return
    origin, base_path, base_query = target
    ws_origin = origin.replace("http://", "ws://").replace("https://", "wss://")
    merged_query = websocket.url.query or (base_query if not path else "")
    upstream_url = _build_upstream_url(ws_origin, base_path, path, merged_query)

    headers = {
        key: value for key, value in websocket.headers.items()
        if key.lower() not in _HOP_BY_HOP
        and key.lower() not in ("cookie", "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions")
    }

    await websocket.accept()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(upstream_url, headers=headers) as upstream:

                async def client_to_upstream() -> None:
                    while True:
                        event: dict[str, Any] = await websocket.receive()
                        if event["type"] == "websocket.disconnect":
                            break
                        if event.get("text") is not None:
                            await upstream.send_str(event["text"])
                        elif event.get("bytes") is not None:
                            await upstream.send_bytes(event["bytes"])

                async def upstream_to_client() -> None:
                    async for msg in upstream:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await websocket.send_text(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await websocket.send_bytes(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

                tasks = [
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                ]
                _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
    except Exception as exc:
        log(f"频道 WebUI WS 代理异常: {channel_id} - {exc}", "DEBUG")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
