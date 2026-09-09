"""Dify 实体的 HTTP 路由（自动挂载到 /api/entity/dify）。

经 web/server.py 的 _mount_entity_routers 扫描发现，与 AI 工具（tools.py）
共用 service.py 的同一实现。出站一律脱敏（管理员密码 / API Key 仅回显占位符）。

连接地址（dify_base_url 等配置项）的修改走核心接口 PUT /api/entities/dify/config，
本路由只承载实体业务端点。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import service
from .client import DifyApiError, DifyAuthError, DifyNotFoundError
from .config import DifySecretsStore, get_dify_store
from .dsl import DslError
from .service import DifyStateError


def _http_error(exc: Exception) -> HTTPException:
    """实体异常 → HTTP 错误（中文 detail，面板 toast 直接展示）。"""
    if isinstance(exc, DifyNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DifyAuthError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, (DifyStateError, DslError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, DifyApiError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=f"Dify 操作失败: {exc}")


# ── 请求模型 ──────────────────────────────────────────────────────────


class AdminRequest(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=1)


class AppCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    mode: str = Field(..., min_length=1)
    description: str = ""


class DslImportRequest(BaseModel):
    yaml_content: str = Field(..., min_length=1)
    new_name: str = ""


class DslApplyRequest(BaseModel):
    yaml_content: str = Field(..., min_length=1)
    publish: bool = False


class PublishRequest(BaseModel):
    marked_name: str = ""
    marked_comment: str = ""


class CopyRequest(BaseModel):
    name: str = ""


class RunWorkflowRequest(BaseModel):
    inputs: Dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    conversation_id: str = ""


class CredentialRequest(BaseModel):
    credentials: Dict[str, Any] = Field(..., min_length=1)


class DatasetCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""


class McpEnableRequest(BaseModel):
    description: str = ""


def build_router() -> APIRouter:
    router = APIRouter()

    # ── 连接与状态 ────────────────────────────────────────────────────

    @router.get("/status")
    async def get_status() -> Dict[str, Any]:
        """连接状态（配置/可达性/初始化进度/版本/凭据）。"""
        return await service.get_status()

    @router.post("/connect")
    async def connect() -> Dict[str, Any]:
        """连接握手：探测可达性 → 自动初始化（未初始化的自托管实例）→ 验证凭据。"""
        try:
            return await service.connect()
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/config")
    async def get_config_view() -> Dict[str, Any]:
        """实体配置 + 管理员凭据（脱敏）。"""
        store = get_dify_store()
        return {
            "settings": service.get_settings(),
            "admin": DifySecretsStore.mask_admin(store.get_admin()),
        }

    @router.put("/config/admin")
    async def save_admin(req: AdminRequest) -> Dict[str, Any]:
        """录入/更新管理员凭据（登录验证通过后才保存；password=占位符表示不变）。"""
        store = get_dify_store()
        password = req.password
        if DifySecretsStore.is_masked(password):
            existing = store.get_admin()
            if existing.get("email") == req.email.strip() and existing.get("password"):
                return {"ok": True, "email": req.email.strip(), "unchanged": True}
            raise HTTPException(status_code=400, detail="密码为占位符且无既有凭据可沿用")
        try:
            return await service.setup_admin_manual(req.email, password)
        except Exception as exc:
            raise _http_error(exc) from exc

    # ── 应用与工作流 ──────────────────────────────────────────────────

    @router.get("/apps")
    async def list_apps(name: str = "") -> Dict[str, Any]:
        try:
            return await service.list_apps(name)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/apps")
    async def create_app(req: AppCreateRequest) -> Dict[str, Any]:
        try:
            return await service.create_app(req.name, req.mode, req.description)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/apps/import")
    async def import_app(req: DslImportRequest) -> Dict[str, Any]:
        try:
            return await service.import_dsl(req.yaml_content, req.new_name)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/apps/{app_id}/dsl")
    async def export_app_dsl(app_id: str) -> Dict[str, Any]:
        try:
            return await service.export_dsl(app_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.put("/apps/{app_id}/dsl")
    async def apply_app_dsl(app_id: str, req: DslApplyRequest) -> Dict[str, Any]:
        try:
            return await service.apply_dsl(app_id, req.yaml_content, req.publish)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/apps/{app_id}/publish")
    async def publish_app(app_id: str, req: PublishRequest) -> Dict[str, Any]:
        try:
            return await service.publish(app_id, req.marked_name, req.marked_comment)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/apps/{app_id}/copy")
    async def copy_app(app_id: str, req: CopyRequest) -> Dict[str, Any]:
        try:
            return await service.copy_app(app_id, req.name)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.delete("/apps/{app_id}")
    async def delete_app(app_id: str) -> Dict[str, Any]:
        try:
            return await service.delete_app(app_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    # ── 运行调试 ──────────────────────────────────────────────────────

    @router.post("/apps/{app_id}/run")
    async def run_workflow(app_id: str, req: RunWorkflowRequest) -> Dict[str, Any]:
        try:
            return await service.run_workflow(app_id, req.inputs)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/apps/{app_id}/chat")
    async def chat(app_id: str, req: ChatRequest) -> Dict[str, Any]:
        try:
            return await service.chat(app_id, req.query, req.inputs, req.conversation_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    # ── API Key ───────────────────────────────────────────────────────

    @router.get("/apps/{app_id}/api-keys")
    async def list_api_keys(app_id: str) -> Dict[str, Any]:
        try:
            return await service.list_api_keys(app_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/apps/{app_id}/api-keys")
    async def create_api_key(app_id: str) -> Dict[str, Any]:
        try:
            return await service.create_api_key(app_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.delete("/apps/{app_id}/api-keys/{key_id}")
    async def revoke_api_key(app_id: str, key_id: str) -> Dict[str, Any]:
        try:
            return await service.revoke_api_key(app_id, key_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    # ── 模型供应商 / 数据集 ───────────────────────────────────────────

    @router.get("/providers")
    async def list_providers() -> Dict[str, Any]:
        try:
            return await service.list_model_providers()
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/providers/{provider}/credentials")
    async def set_credential(provider: str, req: CredentialRequest) -> Dict[str, Any]:
        try:
            return await service.set_model_credential(provider, req.credentials)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/datasets")
    async def list_datasets() -> Dict[str, Any]:
        try:
            return await service.list_datasets()
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/datasets")
    async def create_dataset(req: DatasetCreateRequest) -> Dict[str, Any]:
        try:
            return await service.create_dataset(req.name, req.description)
        except Exception as exc:
            raise _http_error(exc) from exc

    # ── MCP 桥接 ──────────────────────────────────────────────────────

    @router.get("/apps/{app_id}/mcp")
    async def mcp_status(app_id: str) -> Dict[str, Any]:
        try:
            return await service.get_mcp_status(app_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/apps/{app_id}/mcp")
    async def mcp_enable(app_id: str, req: McpEnableRequest) -> Dict[str, Any]:
        try:
            return await service.enable_mcp(app_id, req.description)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.delete("/apps/{app_id}/mcp")
    async def mcp_disable(app_id: str) -> Dict[str, Any]:
        try:
            return await service.disable_mcp(app_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    return router
