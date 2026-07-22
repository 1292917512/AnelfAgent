"""Web 服务器 -- WebUI 独立 FastAPI 应用，与 HTTP API 适配器使用不同端口。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.log import log
from core.path import ConfigPaths
from web.auth_keys import extract_bearer_token, verify_bearer_api_key

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


def _mount_channel_routers(app: FastAPI) -> None:
    """挂载各频道包暴露的 HTTP 路由到 /api/channels/<id>。

    频道模块（channels.<name>.adapter）若提供模块级 ``build_router()``，
    即被挂载 — 不要求频道已启用（例如微信扫码登录需在启用前可用）。
    """
    import importlib

    channels_dir = Path(__file__).resolve().parent.parent / "channels"
    if not channels_dir.is_dir():
        return
    for item in sorted(channels_dir.iterdir()):
        if not item.is_dir() or item.name.startswith("_"):
            continue
        if not (item / "adapter.py").exists():
            continue
        try:
            mod = importlib.import_module(f"channels.{item.name}.adapter")
        except Exception:
            continue
        build_router = getattr(mod, "build_router", None)
        if not callable(build_router):
            continue
        try:
            app.include_router(
                build_router(),
                prefix=f"/api/channels/{item.name}",
                tags=[f"channel-{item.name}"],
            )
            log(f"频道路由已挂载: /api/channels/{item.name}")
        except Exception as exc:
            log(f"频道路由挂载失败: {item.name} - {exc}", "WARNING")


def _make_token(password: str) -> str:
    """根据密码生成确定性 token（跨重启有效）。"""
    return hashlib.sha256(f"anelf-auth:{password}".encode()).hexdigest()[:32]


def _load_auth_password() -> str:
    """从 webui.json 读取 auth.password，空字符串表示不启用密码。"""
    p = Path(ConfigPaths.WEBUI_CONFIG)
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8")).get("auth", {}).get("password", "")
        except Exception:
            pass
    return ""


_AUTH_EXEMPT = frozenset({"/api/auth/login", "/api/auth/check"})


class _AuthMiddleware(BaseHTTPMiddleware):
    """API 密码保护中间件。仅拦截 /api/* 请求，SPA 静态文件不受影响。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if not path.startswith("/api/") or path in _AUTH_EXEMPT:
            return await call_next(request)

        password = _load_auth_password()
        if not password:
            return await call_next(request)

        token = request.cookies.get("_anelf_token", "")
        if token == _make_token(password):
            return await call_next(request)

        return JSONResponse({"error": "unauthorized"}, status_code=401)


class _V1BearerAuthMiddleware(BaseHTTPMiddleware):
    """保护 /v1/* 的独立 Bearer API Key 鉴权。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if not path.startswith("/v1"):
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)

        authorization = request.headers.get("authorization", "")
        token = extract_bearer_token(authorization)
        if not verify_bearer_api_key(token):
            return JSONResponse(
                {
                    "error": {
                        "message": "Invalid API key",
                        "type": "invalid_request_error",
                        "code": "invalid_api_key",
                    }
                },
                status_code=401,
            )
        return await call_next(request)


def create_app() -> FastAPI:
    """创建 WebUI FastAPI 应用。"""
    app = FastAPI(title="AnelfAgent WebUI", version="1.0.0")

    app.add_middleware(_AuthMiddleware)
    app.add_middleware(_V1BearerAuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from web.routers import api_router
    from web.routers.v1_responses import router as v1_responses_router

    app.include_router(api_router)
    app.include_router(v1_responses_router, prefix="/v1")

    _mount_nonebot(app)
    _mount_channel_routers(app)

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
    p = Path(ConfigPaths.WEBUI_CONFIG)
    if p.exists():
        try:
            cfg = json.loads(p.read_text("utf-8")).get("server", {})
            return cfg.get("host", "0.0.0.0"), int(cfg.get("port", 8092))
        except Exception as e:
            log(f"WebUI 服务器配置加载失败: {e}", "DEBUG")
    return "0.0.0.0", 8092


_server: Any = None


async def start_web_server() -> None:
    """启动 WebUI 服务器，host/port 从 config/webui.json 读取。"""
    global _server
    import contextlib
    import uvicorn

    class _QuietServer(uvicorn.Server):
        """跳过 uvicorn 自带的信号处理，由外部统一管理关闭流程。"""

        @contextlib.contextmanager
        def capture_signals(self):  # type: ignore[override]
            yield

    host, port = _load_server_config()
    await register_webui_channel()

    app = create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    _server = _QuietServer(config)

    local_url = f"http://127.0.0.1:{port}"
    log(f"WebUI 已启动: {local_url}  (监听 {host}:{port}，局域网可访问)")
    try:
        await _server.serve()
    finally:
        _server = None


def request_web_shutdown() -> None:
    """请求 Web 服务器优雅关闭。"""
    if _server is not None:
        _server.should_exit = True
