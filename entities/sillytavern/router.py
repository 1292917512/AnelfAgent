"""SillyTavern 实体的 HTTP 路由（自动挂载到 /api/entity/sillytavern）。

与 AI 工具共用 service/st_client 同一实现；认证由全局
_AuthMiddleware（_anelf_token cookie）统一兜底。

/webui/* 为酒馆网页的同源反向代理（仿 web/routers/channel_webui.py），
让外部浏览器经本站（8092）即可打开只监听回环的酒馆页面。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, Optional

import aiohttp
import httpx
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from core.log import log

from . import chat_bridge, git_ops, service
from . import config as st_config
from .st_client import STError, get_st_client


def build_router() -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @router.get("/status")
    async def status() -> Dict[str, Any]:
        return await asyncio.to_thread(service.status)

    @router.post("/start")
    async def start() -> Dict[str, Any]:
        return await asyncio.to_thread(service.start)

    @router.post("/stop")
    async def stop() -> Dict[str, Any]:
        return await asyncio.to_thread(service.stop)

    @router.post("/restart")
    async def restart() -> Dict[str, Any]:
        return await asyncio.to_thread(service.restart)

    @router.get("/logs")
    async def logs(max_chars: int = 4000) -> Dict[str, Any]:
        max_chars = max(200, min(max_chars, 50000))
        return {"log_tail": await asyncio.to_thread(service.tail_log, max_chars)}

    # ------------------------------------------------------------------
    # 实体配置
    # ------------------------------------------------------------------

    @router.get("/config")
    async def get_config() -> Dict[str, Any]:
        return st_config.load_config()

    @router.post("/config")
    async def save_config(data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(st_config.save_config, data)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Git 更新 / 二次开发
    # ------------------------------------------------------------------

    @router.get("/git")
    async def git_status() -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(git_ops.status)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @router.get("/git/versions")
    async def git_versions(remote: str = "origin") -> Dict[str, Any]:
        """远端可用版本（分支）列表，供面板查看/切换。"""
        try:
            return await asyncio.to_thread(git_ops.remote_versions, remote)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    class GitCheckoutBody(BaseModel):
        name: str = Field(min_length=1)
        remote: str = "origin"

    @router.post("/git/checkout")
    async def git_checkout(body: GitCheckoutBody) -> Dict[str, Any]:
        """切换到指定远端分支版本（要求工作区干净）。"""
        try:
            return await asyncio.to_thread(git_ops.checkout_version, body.remote, body.name)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    class GitUpdateBody(BaseModel):
        remote: str = "origin"
        branch: Optional[str] = None

    @router.post("/git/update")
    async def git_update(body: GitUpdateBody) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(git_ops.pull, body.remote, body.branch)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    class GitCommitBody(BaseModel):
        message: str = Field(min_length=1)

    @router.post("/git/commit")
    async def git_commit(body: GitCommitBody) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(git_ops.commit_push, body.message)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # 酒馆桥接（以下要求酒馆运行中）
    # ------------------------------------------------------------------

    def _running_base() -> str:
        if not service.is_running():
            raise _NotRunning()
        return st_config.base_url()

    class _NotRunning(Exception):
        pass

    from fastapi import HTTPException

    def _call(fn):
        """在 to_thread 中执行 fn，统一错误 → HTTP 状态码。"""
        async def _run():
            try:
                return await asyncio.to_thread(fn)
            except _NotRunning:
                raise HTTPException(400, "酒馆未运行，请先启动")
            except ValueError as e:
                raise HTTPException(400, str(e))
            except STError as e:
                raise HTTPException(502, str(e))
        return _run

    @router.get("/characters")
    async def characters() -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            client = get_st_client()
            chars = client.characters_all(_running_base())
            return {"count": len(chars),
                    "characters": [client.character_brief(c) for c in chars]}
        return await _call(_run)()

    @router.get("/characters/{avatar}")
    async def character_detail(avatar: str) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            return get_st_client().get_character(_running_base(), avatar)
        return await _call(_run)()

    class CharacterCreateBody(BaseModel):
        name: str = Field(min_length=1)
        description: str = ""
        personality: str = ""
        first_mes: str = ""
        scenario: str = ""
        mes_example: str = ""
        system_prompt: str = ""
        tags: list[str] = []

    @router.post("/characters/create")
    async def character_create(body: CharacterCreateBody) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            fields = body.model_dump()
            fields["tags"] = [t for t in body.tags if t.strip()]
            avatar = get_st_client().create_character(_running_base(), fields)
            return {"ok": True, "avatar": avatar}
        return await _call(_run)()

    class CharacterEditBody(BaseModel):
        avatar: str
        field: str
        value: str
        current_name: str = ""

    @router.post("/characters/edit")
    async def character_edit(body: CharacterEditBody) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            result = get_st_client().edit_character_field(
                _running_base(), body.avatar,
                body.current_name or body.avatar.rsplit(".", 1)[0],
                body.field, body.value)
            return {"ok": True, "result": result}
        return await _call(_run)()

    class CharacterDeleteBody(BaseModel):
        avatar: str
        delete_chats: bool = False

    @router.post("/characters/delete")
    async def character_delete(body: CharacterDeleteBody) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            get_st_client().delete_character(_running_base(), body.avatar, body.delete_chats)
            return {"ok": True, "deleted": body.avatar}
        return await _call(_run)()

    # ------------------------------------------------------------------
    # 设置 / 模型配置
    # ------------------------------------------------------------------

    @router.get("/settings")
    async def get_settings() -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            return get_st_client().get_settings(_running_base())["settings"]
        return await _call(_run)()

    @router.post("/settings")
    async def save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            get_st_client().save_settings(_running_base(), settings)
            return {"ok": True}
        return await _call(_run)()

    class ModelConfigBody(BaseModel):
        main_api: str = ""
        model: str = ""
        temperature: float = 0.0
        max_context: int = 0
        max_tokens: int = 0

    @router.post("/settings/model")
    async def set_model_config(body: ModelConfigBody) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            client = get_st_client()
            base = _running_base()
            settings = client.get_settings(base)["settings"]
            changed: Dict[str, Any] = {}
            if body.main_api:
                settings["main_api"] = body.main_api
                changed["main_api"] = body.main_api
            oai = dict(settings.get("oai_settings", {}) or {})
            if body.model:
                oai["openai_model"] = body.model
                changed["model"] = body.model
            if body.temperature > 0:
                oai["temp_openai"] = min(body.temperature, 2.0)
                changed["temperature"] = oai["temp_openai"]
            if body.max_context > 0:
                oai["openai_max_context"] = body.max_context
                changed["max_context"] = body.max_context
            if body.max_tokens > 0:
                oai["openai_max_tokens"] = body.max_tokens
                changed["max_tokens"] = body.max_tokens
            if not changed:
                raise ValueError("未指定任何要修改的参数")
            settings["oai_settings"] = oai
            client.save_settings(base, settings)
            return {"ok": True, "changed": changed}
        return await _call(_run)()

    # ------------------------------------------------------------------
    # 聊天记录
    # ------------------------------------------------------------------

    @router.get("/chats")
    async def chats(avatar: str) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            return {"avatar": avatar,
                    "chats": get_st_client().character_chats(_running_base(), avatar)}
        return await _call(_run)()

    @router.get("/chats/content")
    async def chat_content(avatar: str, file_name: str) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            return {"file": file_name,
                    "messages": get_st_client().get_chat(_running_base(), avatar, file_name)}
        return await _call(_run)()

    # ------------------------------------------------------------------
    # AI × 酒馆角色对话（实体内部生成编排，用 AnelfAgent 自己的人设与主模型）
    # ------------------------------------------------------------------

    class ChatSendBody(BaseModel):
        avatar: str = Field(min_length=1)
        message: str = Field(min_length=1)
        chat_file: str = ""
        name: str = "Anelf"

    @router.post("/chat/send")
    async def chat_send(body: ChatSendBody) -> Dict[str, Any]:
        """AI 对酒馆角色说话（走 anelf-bridge 插件，复用酒馆生成管道）。"""
        def _run() -> Dict[str, Any]:
            return chat_bridge.chat_turn(
                body.avatar, body.message, body.chat_file or None, body.name)
        try:
            return await asyncio.to_thread(_run)
        except _NotRunning:
            raise HTTPException(400, "酒馆未运行，请先启动")
        except chat_bridge.TavernChatError as e:
            raise HTTPException(502, str(e))

    # ------------------------------------------------------------------
    # 模型直连（把 AnelfAgent 已配置的模型应用到酒馆）
    # ------------------------------------------------------------------

    @router.get("/my-models")
    async def my_models() -> Dict[str, Any]:
        """列出 AnelfAgent 已配置的可对话模型。"""
        def _run() -> Dict[str, Any]:
            from services.model import ModelService
            svc = ModelService()
            out = []
            for prov in svc.list_providers():
                pid = prov.get("id")
                for m in svc.list_provider_models(pid):
                    if "chat" not in (m.get("model_types") or ["chat"]):
                        continue
                    out.append({
                        "provider_id": pid,
                        "provider_name": prov.get("name", pid),
                        "model_id": m.get("id") or m.get("name"),
                        "model": m.get("model"),
                        "base_url": prov.get("base_url", ""),
                        "api_type": prov.get("api_type", "openai"),
                    })
            return {"models": out}
        return await asyncio.to_thread(_run)

    class UseMyModelBody(BaseModel):
        model_id: str = Field(min_length=1)

    @router.post("/my-models/use")
    async def use_my_model(body: UseMyModelBody) -> Dict[str, Any]:
        """把指定 AnelfAgent 模型接到酒馆（写 custom 源 + 密钥）。"""
        def _run() -> Dict[str, Any]:
            base = _running_base()
            from agent.llm import get_llm_manager
            from .tools import _normalize_chat_endpoint
            manager = get_llm_manager()
            client = manager.get_client(body.model_id)
            if client is None:
                raise ValueError(f"模型不存在: {body.model_id}")
            cfg = client.config
            provider = manager.get_provider(cfg.provider_id)
            if provider is None:
                raise ValueError(f"供应商不存在: {cfg.provider_id}")
            base_url = str(getattr(provider, "base_url", "") or "").rstrip("/")
            api_key = str(getattr(provider, "api_key", "") or "")
            if not base_url:
                raise ValueError(f"供应商 {cfg.provider_id} 未配置 base_url")
            endpoint = _normalize_chat_endpoint(base_url)
            st_client = get_st_client()
            settings = st_client.get_settings(base)["settings"]
            oai = dict(settings.get("oai_settings", {}) or {})
            oai["chat_completion_source"] = "custom"
            oai["custom_url"] = endpoint
            oai["openai_model"] = str(cfg.model)
            settings["oai_settings"] = oai
            settings["main_api"] = "openai"
            st_client.save_settings(base, settings)
            if api_key:
                st_client.write_secret(base, "api_key_custom", api_key)
            return {"ok": True, "model": cfg.model, "endpoint": endpoint,
                    "provider": cfg.provider_id}
        return await _call(_run)()

    # ------------------------------------------------------------------
    # 酒馆网页同源反代（/webui）——仿 web/routers/channel_webui.py
    # ------------------------------------------------------------------

    _PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    _HOP_BY_HOP = frozenset({
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
    })
    _REWRITE_TYPES = ("text/html", "javascript", "text/css")
    _proxy_client: Optional[httpx.AsyncClient] = None

    def _webui_prefix(request: Request) -> str:
        """代理前缀（含 include_router 挂载的 /api/entity/sillytavern 前缀）。"""
        return request.scope.get("root_path", "") + "/api/entity/sillytavern/webui"

    def _rewrite_text(text: str, prefix: str) -> str:
        guard = r"(?!" + re.escape(prefix) + r"/)"
        attrs = re.compile(r'(?P<a>href="|src="|action="|srcset=")' + guard + "/")
        text = attrs.sub(lambda m: f'{m.group("a")}{prefix}/', text)
        css_url = re.compile(r"url\(" + guard + "/")
        return css_url.sub(f"url({prefix}/", text)

    def _rewrite_location(location: str, origin: str, prefix: str) -> str:
        if location.startswith(origin):
            return prefix + location[len(origin):]
        if location.startswith("/"):
            return prefix + location
        return location

    def _rewrite_set_cookie(value: str, prefix: str) -> str:
        if re.search(r"(?i)(?:^|;\s*)path=", value):
            return re.sub(r"(?i)((?:^|;\s*)path=)/[^;]*", rf"\g<1>{prefix}/", value)
        return value + f"; Path={prefix}/"

    async def _pclient() -> httpx.AsyncClient:
        nonlocal _proxy_client
        if _proxy_client is None:
            _proxy_client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
            )
        return _proxy_client

    @router.api_route("/webui", methods=_PROXY_METHODS)
    @router.api_route("/webui/", methods=_PROXY_METHODS)
    @router.api_route("/webui/{path:path}", methods=_PROXY_METHODS)
    async def proxy_webui(request: Request, path: str = "") -> Response:
        """将请求转发到本机酒馆服务，并重写响应中的绝对路径。"""
        if not service.is_running():
            return JSONResponse({"error": "酒馆未运行，无法打开网页"}, status_code=400)
        origin = st_config.base_url()
        prefix = _webui_prefix(request)
        upstream_url = f"{origin}/{path}"
        if request.url.query:
            upstream_url += f"?{request.url.query}"
        headers = {
            key: value for key, value in request.headers.items()
            if key.lower() not in _HOP_BY_HOP and key.lower() != "cookie"
        }
        headers["accept-encoding"] = "identity"
        try:
            upstream = await (await _pclient()).request(
                request.method, upstream_url,
                content=await request.body(), headers=headers,
            )
        except httpx.HTTPError as exc:
            return JSONResponse({"error": f"酒馆不可达: {exc}"}, status_code=502)

        resp_headers = {
            key: value for key, value in upstream.headers.items()
            if key.lower() not in _HOP_BY_HOP
            and key.lower() not in ("content-encoding", "content-length",
                                    "set-cookie", "location")
        }
        if "location" in upstream.headers:
            resp_headers["location"] = _rewrite_location(
                upstream.headers["location"], origin, prefix)
        content = upstream.content
        content_type = upstream.headers.get("content-type", "")
        if any(kind in content_type for kind in _REWRITE_TYPES):
            text = content.decode(upstream.encoding or "utf-8", errors="replace")
            content = _rewrite_text(text, prefix).encode("utf-8")
        response = Response(content=content, status_code=upstream.status_code,
                            headers=resp_headers)
        for set_cookie in upstream.headers.get_list("set-cookie"):
            response.raw_headers.append((
                b"set-cookie", _rewrite_set_cookie(set_cookie, prefix).encode("latin-1"),
            ))
        return response

    @router.websocket("/webui/{path:path}")
    @router.websocket("/webui")
    async def proxy_webui_ws(websocket: WebSocket, path: str = "") -> None:
        """桥接酒馆网页的 WebSocket 连接（鉴权走前置 HTTP 代理逻辑）。"""
        if not service.is_running():
            await websocket.close(code=4404)
            return
        origin = st_config.base_url().replace("http://", "ws://").replace(
            "https://", "wss://")
        upstream_url = f"{origin}/{path}"
        if websocket.url.query:
            upstream_url += f"?{websocket.url.query}"
        headers = {
            key: value for key, value in websocket.headers.items()
            if key.lower() not in _HOP_BY_HOP
            and key.lower() not in ("cookie", "sec-websocket-key",
                                    "sec-websocket-version", "sec-websocket-extensions")
        }
        await websocket.accept()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(upstream_url, headers=headers) as upstream:
                    async def client_to_upstream() -> None:
                        while True:
                            event: Dict[str, Any] = await websocket.receive()
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
                            elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                              aiohttp.WSMsgType.ERROR):
                                break

                    tasks = [
                        asyncio.create_task(client_to_upstream()),
                        asyncio.create_task(upstream_to_client()),
                    ]
                    _, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
        except Exception as exc:
            log(f"酒馆 WebUI WS 代理异常: {exc}", "DEBUG")
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    return router
