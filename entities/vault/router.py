"""密码本 Web API（build_router 自动挂载到 /api/entity/vault）。

安全约定：
- 出站一律经 service._public() 形态（EntryOut 不含任何密文/明文敏感字段）
- 明文仅在 /reveal、/totp、/export 三个端点按显式请求返回
- 解锁模型：machine（默认，自动解锁）/ master（主密码加固，可双向转换）
- 主密码错误返回 401；主密码模式锁定态返回 423；未初始化/已初始化冲突返回 409
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, HTTPException

from core.config import get_config_int
from core.log import log

from . import crypto, generator
from .schemas import (
    BreachReportOut,
    ChangeMasterRequest,
    EntryCreateRequest,
    EntryListOut,
    EntryOut,
    EntryUpdateRequest,
    ExportOut,
    ExportRequest,
    GenerateOut,
    GenerateRequest,
    ImportOut,
    ImportRequest,
    MasterEnableRequest,
    PasswordRequest,
    RevealOut,
    RevealRequest,
    SetupRequest,
    StatusOut,
    TotpOut,
)
from .service import (
    EntryNotFoundError,
    VaultAlreadyInitializedError,
    VaultAuthError,
    VaultError,
    VaultNotInitializedError,
    get_vault_service,
)
from .session import VaultLockedError


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, VaultAuthError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, VaultLockedError):
        return HTTPException(status_code=423, detail=str(exc))
    if isinstance(exc, EntryNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (VaultAlreadyInitializedError, VaultNotInitializedError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (VaultError, crypto.VaultCryptoError, ValueError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=f"内部错误: {exc}")


def build_router() -> APIRouter:
    router = APIRouter()
    service = get_vault_service()

    def wrap(fn: Callable):  # noqa: ANN202
        async def handler(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            try:
                return await fn(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as exc:
                raise _translate(exc) from exc
        return handler

    # ------------------------------------------------------------------
    # 状态与解锁生命周期
    # ------------------------------------------------------------------

    @router.get("/status", response_model=StatusOut)
    async def status():
        return await wrap(service.status)()

    @router.post("/setup", response_model=StatusOut, status_code=201)
    async def setup(req: SetupRequest):
        if req.mode == "machine":
            await wrap(service.setup_machine)()
        elif req.mode == "master":
            if not req.password:
                raise HTTPException(status_code=400, detail="主密码模式必须提供主密码")
            await wrap(service.setup)(req.password)
        else:
            raise HTTPException(status_code=400, detail=f"不支持的模式: {req.mode}")
        return await service.status()

    @router.post("/unlock", response_model=StatusOut)
    async def unlock(req: PasswordRequest):
        await wrap(service.unlock)(req.password)
        return await service.status()

    @router.post("/lock", response_model=StatusOut)
    async def lock():
        service.lock()
        return await service.status()

    @router.post("/change-master", response_model=StatusOut)
    async def change_master(req: ChangeMasterRequest):
        await wrap(service.change_master)(req.old_password, req.new_password)
        return await service.status()

    @router.post("/master/enable", response_model=StatusOut)
    async def master_enable(req: MasterEnableRequest):
        """机器模式 → 主密码模式（启用主密码保护）。"""
        await wrap(service.enable_master)(req.password)
        return await service.status()

    @router.post("/master/disable", response_model=StatusOut)
    async def master_disable():
        """主密码模式 → 机器模式（恢复自动解锁）。需当前已解锁。"""
        await wrap(service.disable_master)()
        return await service.status()

    # ------------------------------------------------------------------
    # 条目 CRUD
    # ------------------------------------------------------------------

    @router.get("/entries", response_model=EntryListOut)
    async def list_entries(query: str = "", tag: str = "",
                           favorite: bool = False, limit: int = 100):
        entries = await wrap(service.search)(
            query, tag=tag, favorite_only=favorite,
            limit=max(1, min(500, limit)))
        return {"count": len(entries), "entries": entries}

    @router.get("/entries/tags", response_model=list[str])
    async def list_tags():
        return await wrap(service.list_all_tags)()

    @router.post("/entries", response_model=EntryOut, status_code=201)
    async def create_entry(req: EntryCreateRequest):
        password = req.password
        if req.generate and not password:
            password = generator.generate_password(
                get_config_int("vault_generator_default_length", 20))
        return await wrap(service.add_entry)(
            title=req.title, username=req.username, url=req.url,
            password=password, totp_secret=req.totp_secret, notes=req.notes,
            tags=req.tags, favorite=req.favorite)

    @router.get("/entries/{entry_id}", response_model=EntryOut)
    async def get_entry(entry_id: str):
        return await wrap(service.get_entry)(entry_id)

    @router.put("/entries/{entry_id}", response_model=EntryOut)
    async def update_entry(entry_id: str, req: EntryUpdateRequest):
        return await wrap(service.update_entry)(
            entry_id, title=req.title, username=req.username, url=req.url,
            password=req.password, totp_secret=req.totp_secret,
            notes=req.notes, tags=req.tags, favorite=req.favorite)

    @router.delete("/entries/{entry_id}")
    async def delete_entry(entry_id: str):
        await wrap(service.delete_entry)(entry_id)
        return {"deleted": entry_id}

    # ------------------------------------------------------------------
    # 明文访问（需解锁）
    # ------------------------------------------------------------------

    @router.post("/entries/{entry_id}/reveal", response_model=RevealOut)
    async def reveal(entry_id: str, req: RevealRequest):
        value = await wrap(service.reveal)(entry_id, req.field)
        log(f"密码本明文读取: entry={entry_id[:8]} field={req.field}", "INFO")
        return {"entry_id": entry_id, "field": req.field, "value": value}

    @router.get("/entries/{entry_id}/totp", response_model=TotpOut)
    async def totp_code(entry_id: str):
        return await wrap(service.totp_code)(entry_id)

    # ------------------------------------------------------------------
    # 生成器 / 导入导出 / 体检
    # ------------------------------------------------------------------

    @router.post("/generate", response_model=GenerateOut)
    async def generate(req: GenerateRequest):
        try:
            if req.memorable:
                password = generator.generate_memorable(max(2, req.length // 5))
            else:
                password = generator.generate_password(
                    req.length, upper=req.upper, lower=req.lower,
                    digits=req.digits, symbols=req.symbols,
                    exclude_ambiguous=req.exclude_ambiguous)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"password": password,
                "strength": generator.assess_strength(password)}

    @router.post("/import", response_model=ImportOut)
    async def import_entries(req: ImportRequest):
        return await wrap(service.import_text)(
            req.content, req.format, strategy=req.strategy,
            master_password=req.master_password)

    @router.post("/export", response_model=ExportOut)
    async def export_entries(req: ExportRequest):
        content = await wrap(service.export)(req.format, req.master_password)
        log(f"密码本导出: format={req.format}", "INFO")
        return {"format": req.format, "content": content}

    @router.post("/breach-check", response_model=BreachReportOut)
    async def breach_check():
        return await wrap(service.breach_check)()

    return router
