"""SillyTavern 实体的 HTTP 路由（自动挂载到 /api/entity/sillytavern）。

与 AI 工具共用 service/st_client 同一实现；认证由全局
_AuthMiddleware（_anelf_token cookie）统一兜底。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

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
                raise HTTPException(400, "酒馆未运行，请先启动") from None
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            except STError as e:
                raise HTTPException(502, str(e)) from e
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
            raise HTTPException(400, "酒馆未运行，请先启动") from None
        except chat_bridge.TavernChatError as e:
            raise HTTPException(502, str(e)) from e

    # ------------------------------------------------------------------
    # 能力域端点（世界书/向量记忆/群聊/导入/备份/统计/扩展）
    # ------------------------------------------------------------------

    @router.get("/worldbooks")
    async def worldbooks() -> Dict[str, Any]:
        def _run() -> Any:
            return {"books": get_st_client().worldinfo_list(_running_base())}
        return await _call(_run)()

    @router.get("/worldbooks/{name}")
    async def worldbook_detail(name: str) -> Dict[str, Any]:
        def _run() -> Any:
            return get_st_client().worldinfo_get(_running_base(), name)
        return await _call(_run)()

    class MemoryBody(BaseModel):
        collection_id: str = Field(min_length=1)
        text: str = ""
        query: str = ""
        top_k: int = 5

    @router.post("/memory/insert")
    async def memory_insert(body: MemoryBody) -> Dict[str, Any]:
        def _run() -> Any:
            get_st_client().vector_insert(_running_base(), body.text, body.collection_id)
            return {"ok": True}
        return await _call(_run)()

    @router.post("/memory/query")
    async def memory_query(body: MemoryBody) -> Dict[str, Any]:
        def _run() -> Any:
            return {"results": get_st_client().vector_query(
                _running_base(), body.collection_id, body.query, body.top_k)}
        return await _call(_run)()

    @router.get("/groups")
    async def groups() -> Dict[str, Any]:
        def _run() -> Any:
            return {"groups": get_st_client().groups_all(_running_base())}
        return await _call(_run)()

    class ImportBody(BaseModel):
        url: str = Field(min_length=1)

    @router.post("/import")
    async def import_url(body: ImportBody) -> Dict[str, Any]:
        def _run() -> Any:
            return get_st_client().content_import_url(_running_base(), body.url)
        return await _call(_run)()

    @router.get("/stats")
    async def stats() -> Dict[str, Any]:
        def _run() -> Any:
            return get_st_client().stats_get(_running_base())
        return await _call(_run)()

    @router.get("/extensions")
    async def extensions() -> Dict[str, Any]:
        def _run() -> Any:
            exts = get_st_client().extensions_discover(_running_base())
            return {"count": len(exts), "extensions": exts}
        return await _call(_run)()

    class ExtInstallBody(BaseModel):
        url: str = Field(min_length=1)
        global_install: bool = True

    @router.post("/extensions/install")
    async def extension_install(body: ExtInstallBody) -> Dict[str, Any]:
        def _run() -> Any:
            return {"ok": True, "result": get_st_client().extension_install(
                _running_base(), body.url, body.global_install)}
        return await _call(_run)()

    return router
