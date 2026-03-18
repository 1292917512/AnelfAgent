"""Web 服务器 -- WebUI 独立 FastAPI 应用，与 HTTP API 适配器使用不同端口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.log import log
from core.path import ConfigPaths

_WEB_DIR = Path(__file__).parent
FRONTEND_DIST = _WEB_DIR / "frontend" / "dist"
_FALLBACK_HTML = (_WEB_DIR / "fallback.html").read_text("utf-8")


def _mount_nonebot(app: FastAPI) -> None:
    """如果 NoneBot 桥接已初始化，挂载其 ASGI 路由到 /nonebot。"""
    try:
        from channels.nonebot_bridge.nonebot_init import is_initialized, mount_nonebot_app
        if is_initialized():
            mount_nonebot_app(app)
    except ImportError:
        pass


def create_app() -> FastAPI:
    """创建 WebUI FastAPI 应用。"""
    app = FastAPI(title="AnelfAgent WebUI", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from web.routers import api_router
    app.include_router(api_router)

    _mount_nonebot(app)

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    if FRONTEND_DIST.exists():
        _index_html = (FRONTEND_DIST / "index.html").read_text("utf-8")

        @app.get("/")
        async def root() -> RedirectResponse:
            return RedirectResponse("/webui/")

        app.mount("/webui/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="webui-assets")

        @app.get("/webui/{path:path}")
        async def webui_spa(path: str) -> HTMLResponse:
            """SPA fallback: all /webui/* routes return index.html."""
            file_path = FRONTEND_DIST / path
            if file_path.is_file():
                content = file_path.read_bytes()
                suffix = file_path.suffix
                media = {
                    ".js": "application/javascript",
                    ".css": "text/css",
                    ".svg": "image/svg+xml",
                    ".png": "image/png",
                    ".ico": "image/x-icon",
                    ".json": "application/json",
                }.get(suffix, "application/octet-stream")
                from starlette.responses import Response
                return Response(content=content, media_type=media)
            return HTMLResponse(_index_html)

        @app.get("/webui")
        async def webui_root() -> HTMLResponse:
            return HTMLResponse(_index_html)
    else:
        @app.get("/")
        async def fallback() -> HTMLResponse:
            return HTMLResponse(_FALLBACK_HTML)

        @app.get("/webui/{path:path}")
        async def webui_fallback(path: str) -> HTMLResponse:
            return HTMLResponse(_FALLBACK_HTML)

    return app


async def register_webui_channel() -> None:
    """启动已注册的 WebUI 频道（由 bootstrap register_channels 统一注册）。"""
    try:
        from agent.channel import get_channel_manager
        cm = get_channel_manager()
        if "webui" in cm.list_channels():
            await cm.start_channel("webui")
            log("WebUI 频道已启动")
        else:
            from channels.webui import WebUIChannel
            channel = WebUIChannel()
            cm.register(channel)
            await cm.start_channel("webui")
            log("WebUI 频道已注册并启动")
    except Exception as e:
        log(f"WebUI 频道启动失败: {e}", "WARNING")



def _load_server_config() -> tuple[str, int]:
    """从 config/webui.json 读取 host/port。"""
    import json
    p = Path(ConfigPaths.WEBUI_CONFIG)
    if p.exists():
        try:
            cfg = json.loads(p.read_text("utf-8")).get("server", {})
            return cfg.get("host", "0.0.0.0"), int(cfg.get("port", 8092))
        except Exception as e:
            from core.log import log
            log(f"WebUI 服务器配置加载失败: {e}", "DEBUG")
    return "0.0.0.0", 8092


async def start_web_server() -> None:
    """启动 WebUI 服务器，host/port 从 config/webui.json 读取。"""
    import uvicorn

    host, port = _load_server_config()
    await register_webui_channel()

    app = create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    local_url = f"http://127.0.0.1:{port}"
    log(f"WebUI 已启动: {local_url}  (监听 {host}:{port}，局域网可访问)")
    await server.serve()
