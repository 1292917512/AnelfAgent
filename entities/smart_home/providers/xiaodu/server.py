"""播报音频内嵌服务 — 局域网 HTTP 下发完整 mp3。

小度 DLNA 客户端要求音频响应带 Content-Length（FileResponse 自动满足）。
文件名是内容 hash（不可枚举）；web 主服务的 /api/* 有密码鉴权，
DLNA 设备无法携带 Cookie，故组件内嵌独立端口服务（http_api 频道自建
端口的先例），全程不触碰实体目录外的代码。
"""

from __future__ import annotations

import socket
from typing import Optional

from aiohttp import web

from core.log import log

_LOG_TAG = "智能家居"


def lan_ip_for(peer_host: str) -> str:
    """本机到达对端所用的局域网 IP（UDP 路由探测，无实际流量）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect((peer_host, 80))
            return probe.getsockname()[0]
        except OSError:
            return ""


class AudioServer:
    """TTS 缓存目录的只读静态服务（随机 hash 文件名即访问凭据）。"""

    def __init__(self, content_dir: str, port: int) -> None:
        self._content_dir = content_dir
        self._port = port
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        """启动服务（幂等）。"""
        if self._runner is not None:
            return
        app = web.Application()
        app.router.add_get("/audio/{filename}", self._serve)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        log(f"播报音频服务已启动: 0.0.0.0:{self._port}", "INFO", tag=_LOG_TAG)

    async def stop(self) -> None:
        """关停服务（幂等）。"""
        runner, self._runner = self._runner, None
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception:
                pass

    def url_for(self, filename: str, peer_host: str) -> str:
        """构造对端可达的音频 URL（主机取到达音箱的本机局域网 IP）。"""
        host = lan_ip_for(peer_host)
        if not host:
            raise RuntimeError(f"无法确定到达 {peer_host} 的本机局域网地址")
        return f"http://{host}:{self._port}/audio/{filename}"

    async def _serve(self, request: web.Request) -> web.StreamResponse:
        """下发缓存音频（文件名白名单校验防目录穿越）。"""
        import os

        filename = request.match_info["filename"]
        if not filename.endswith(".mp3") or "/" in filename or "\\" in filename:
            raise web.HTTPNotFound()
        path = os.path.join(self._content_dir, filename)
        if not os.path.isfile(path):
            raise web.HTTPNotFound()
        return web.FileResponse(path)
