"""SSH 实体的 HTTP 路由（自动挂载到 /api/entity/ssh）。

经 web/server.py 的 _mount_entity_routers 扫描发现。
- 连接配置 CRUD（出站一律脱敏，仅暴露 has_password/has_key 标记）
- 连接/断开/测试/执行命令
- /stream SSE 实时推送连接状态变更（前端面板实时刷新）
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from core.log import log

from .manager import get_ssh_manager, subscribe_status, unsubscribe_status
from .schemas import (
    ConnectionCreateRequest,
    ConnectionListResult,
    ConnectionOut,
    ConnectionUpdateRequest,
    ExecRequest,
    ExecResult,
    SetDefaultRequest,
)
from .store import get_ssh_store


def _build_out(profile: Dict[str, Any], snapshot: Dict[str, Any]) -> ConnectionOut:
    """合并配置与实时状态为出站模型（脱敏）。"""
    return ConnectionOut(
        name=str(profile.get("name", "")),
        host=str(profile.get("host", "")),
        port=int(profile.get("port", 22)),
        username=str(profile.get("username", "")),
        description=str(profile.get("description", "")),
        status=str(snapshot.get("status", "disconnected")),
        last_error=str(snapshot.get("last_error", "")),
        connected_at=int(snapshot.get("connected_at", 0)),
        last_used_at=int(snapshot.get("last_used_at", 0)),
        is_default=bool(snapshot.get("is_default", False)),
        has_password=bool(str(profile.get("password", "")).strip()),
        has_key=bool(str(profile.get("key_path", "")).strip()),
    )


def build_router() -> APIRouter:
    router = APIRouter()

    # ── 连接配置 CRUD ────────────────────────────────────────────────

    @router.get("/connections", response_model=ConnectionListResult)
    async def list_connections() -> ConnectionListResult:
        """列出所有连接配置及其实时状态（脱敏）。"""
        store = get_ssh_store()
        manager = get_ssh_manager()
        outs: List[ConnectionOut] = []
        for profile in store.list_profiles():
            name = str(profile.get("name", ""))
            snapshot = manager.get_snapshot(name) or {}
            outs.append(_build_out(profile, snapshot))
        return ConnectionListResult(default=store.get_default_name(), connections=outs)

    @router.post("/connections", response_model=ConnectionOut)
    async def create_connection(req: ConnectionCreateRequest) -> ConnectionOut:
        """新增连接配置。"""
        store = get_ssh_store()
        if store.get(req.name):
            raise HTTPException(status_code=409, detail=f"连接名已存在: {req.name}")
        try:
            profile = await store.save(req.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        snapshot = get_ssh_manager().get_snapshot(req.name) or {}
        return _build_out(profile, snapshot)

    @router.put("/connections/{name}", response_model=ConnectionOut)
    async def update_connection(name: str, req: ConnectionUpdateRequest) -> ConnectionOut:
        """更新连接配置（密码留空保持不变，可重命名）。"""
        store = get_ssh_store()
        existing = store.get(name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"连接不存在: {name}")

        merged = dict(existing)
        updates = req.model_dump(exclude_none=True)
        # 密码/口令字段：显式传空串视为清空，未传（None）保持不变
        for key in ("password", "passphrase", "key_path"):
            value = getattr(req, key)
            if value is not None:
                merged[key] = value
        for key in ("host", "port", "username", "description", "name"):
            if key in updates:
                merged[key] = updates[key]

        rename_from = name if str(merged.get("name", name)) != name else ""
        try:
            profile = await store.save(merged, rename_from=rename_from)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        # 重命名后清理旧名连接池（store 已由 save 处理重命名）
        if rename_from:
            await get_ssh_manager().forget(rename_from)
        snapshot = get_ssh_manager().get_snapshot(str(profile["name"])) or {}
        return _build_out(profile, snapshot)

    @router.delete("/connections/{name}")
    async def delete_connection(name: str) -> dict:
        """删除连接配置并断开连接。"""
        removed = await get_ssh_manager().remove_profile(name)
        if not removed:
            raise HTTPException(status_code=404, detail=f"连接不存在: {name}")
        return {"status": "ok", "name": name}

    # ── 连接生命周期 ─────────────────────────────────────────────────

    @router.post("/connections/{name}/connect", response_model=ConnectionOut)
    async def connect(name: str) -> ConnectionOut:
        """建立连接。"""
        manager = get_ssh_manager()
        try:
            await manager.connect(name)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except OSError as e:
            # 含网络失败 / 认证失败 / 密钥文件缺失（均属远端或配置问题）
            raise HTTPException(status_code=502, detail=str(e)) from e
        profile = get_ssh_store().get(name) or {}
        snapshot = manager.get_snapshot(name) or {}
        return _build_out(profile, snapshot)

    @router.post("/connections/{name}/disconnect", response_model=ConnectionOut)
    async def disconnect(name: str) -> ConnectionOut:
        """断开连接。"""
        manager = get_ssh_manager()
        await manager.disconnect(name)
        profile = get_ssh_store().get(name) or {}
        snapshot = manager.get_snapshot(name) or {}
        return _build_out(profile, snapshot)

    @router.post("/default")
    async def set_default(req: SetDefaultRequest) -> dict:
        """设置默认连接。"""
        try:
            await get_ssh_store().set_default(req.name)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"status": "ok", "default": req.name}

    # ── 命令执行 ─────────────────────────────────────────────────────

    @router.post("/connections/{name}/exec", response_model=ExecResult)
    async def exec_command(name: str, req: ExecRequest) -> ExecResult:
        """在指定连接上执行命令（Web 端）。"""
        manager = get_ssh_manager()
        try:
            result = await manager.execute(
                req.command, name=name, timeout=float(req.timeout), work_dir=req.work_dir,
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except Exception as e:
            log(f"SSH Web 执行失败: {name} - {e}", "WARNING", tag="SSH")
            raise HTTPException(status_code=502, detail=str(e)) from e
        return ExecResult(**result)

    # ── SSE 实时状态流 ───────────────────────────────────────────────

    @router.get("/stream")
    async def status_stream(request: Request) -> EventSourceResponse:
        """SSE 推送连接状态变更（前端 EventSource 订阅）。"""
        queue = subscribe_status()

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield {
                            "event": msg.get("event", "status"),
                            "data": json.dumps(msg, ensure_ascii=False),
                        }
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": ""}
            finally:
                unsubscribe_status(queue)

        return EventSourceResponse(event_generator())

    return router
