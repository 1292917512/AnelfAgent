"""模型管理 API 路由（供应商-模型层级）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import ModelService

router = APIRouter(prefix="/models", tags=["models"])

_svc = ModelService()

# ── 供应商 ───────────────────────────────────────────────────────────


@router.get("/providers")
async def list_providers() -> List[Dict[str, Any]]:
    return _svc.list_providers()


class CreateProviderReq(BaseModel):
    id: str
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    api_type: str = "openai"
    proxy_url: str = ""


@router.post("/providers")
async def create_provider(req: CreateProviderReq) -> Dict[str, Any]:
    ok = _svc.add_provider(
        req.id, name=req.name, base_url=req.base_url,
        api_key=req.api_key, api_type=req.api_type, proxy_url=req.proxy_url,
    )
    if not ok:
        raise HTTPException(409, f"供应商 '{req.id}' 已存在")
    return {"status": "ok", "id": req.id}


class UpdateProviderReq(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_type: Optional[str] = None
    proxy_url: Optional[str] = None


@router.put("/providers/{pid}")
async def update_provider(pid: str, req: UpdateProviderReq) -> Dict[str, str]:
    params = {k: v for k, v in req.model_dump().items() if v is not None}
    if not _svc.update_provider(pid, **params):
        raise HTTPException(404, f"供应商 '{pid}' 不存在")
    return {"status": "ok"}


@router.delete("/providers/{pid}")
async def remove_provider(pid: str) -> Dict[str, str]:
    if not _svc.remove_provider(pid):
        raise HTTPException(404, f"供应商 '{pid}' 不存在")
    return {"status": "ok"}


@router.get("/providers/{pid}/models")
async def provider_models(pid: str) -> List[Dict[str, Any]]:
    return _svc.list_provider_models(pid)


class CreateModelReq(BaseModel):
    id: str
    model: str = ""
    model_types: List[str] = ["chat"]
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 4096
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    timeout: float = 120.0
    supports_vision: bool = False
    supports_tools: bool = True
    vision_format: str = "base64"
    supports_reasoning: bool = False


@router.post("/providers/{pid}/models")
async def create_model(pid: str, req: CreateModelReq) -> Dict[str, Any]:
    params = req.model_dump()
    mid = params.pop("id")
    ok = _svc.add_model(pid, mid, **params)
    if not ok:
        raise HTTPException(409, f"模型 '{mid}' 已存在或供应商不存在")
    return {"status": "ok", "id": mid}


# ── 优先级 / 默认 / 测试（固定路径，必须在 /{model_id} 之前） ────────


@router.get("/priorities")
async def get_priorities() -> Dict[str, List[Dict[str, Any]]]:
    return _svc.get_type_priorities()


class SetPriorityReq(BaseModel):
    model_ids: List[str]


@router.put("/priorities/{model_type}")
async def set_priority(model_type: str, req: SetPriorityReq) -> Dict[str, str]:
    _svc.set_type_priority(model_type, req.model_ids)
    return {"status": "ok"}


class SetDefaultReq(BaseModel):
    model_id: str


@router.put("/config/default")
async def set_default(req: SetDefaultReq) -> Dict[str, str]:
    ok = _svc.set_default(req.model_id)
    if not ok:
        raise HTTPException(400, f"模型 '{req.model_id}' 不存在或不支持工具调用，无法设为默认对话模型")
    return {"status": "ok"}


class TestConnectionReq(BaseModel):
    base_url: str
    api_key: str = ""


@router.post("/test")
async def test_connection(req: TestConnectionReq) -> Dict[str, str]:
    try:
        result = await _svc.test_connection(req.base_url, req.api_key)
        return {"result": result}
    except Exception as e:
        return {"result": f"连接失败: {e}"}


class ProbeReq(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    api_type: str = "openai"


@router.post("/probe")
async def probe_capabilities(req: ProbeReq) -> Dict[str, Any]:
    try:
        return await _svc.probe_capabilities(
            req.base_url, req.api_key, req.model, req.api_type,
        )
    except Exception as e:
        return {"error": str(e)}


# ── LiteLLM 模型价格表 ───────────────────────────────────────────────

_COST_MAP_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main"
    "/model_prices_and_context_window.json"
)


@router.get("/cost-map/info")
async def get_cost_map_info() -> Dict[str, Any]:
    """返回当前内存中 LiteLLM 模型价格表的信息。"""
    import litellm
    return {"model_count": len(litellm.model_cost)}


class CostMapUpdateReq(BaseModel):
    proxy_url: str = ""


@router.post("/cost-map/update")
async def update_cost_map(req: CostMapUpdateReq) -> Dict[str, Any]:
    """从 GitHub 拉取最新 LiteLLM 模型价格表，支持代理。"""
    import httpx
    import litellm

    proxies: Dict[str, str] = {}
    if req.proxy_url:
        proxies = {"http://": req.proxy_url, "https://": req.proxy_url}

    try:
        async with httpx.AsyncClient(proxies=proxies, timeout=30.0) as client:  # type: ignore[arg-type]
            response = await client.get(_COST_MAP_URL)
            response.raise_for_status()
            data: Dict[str, Any] = response.json()
            litellm.model_cost = data
            return {"status": "ok", "model_count": len(data)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"更新失败: {e}") from e


# ── 模型（动态路径 /{model_id}，放最后避免吞掉固定路径） ────────────


class UpdateModelReq(BaseModel):
    model: Optional[str] = None
    model_types: Optional[List[str]] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    timeout: Optional[float] = None
    supports_vision: Optional[bool] = None
    supports_tools: Optional[bool] = None
    vision_format: Optional[str] = None
    supports_reasoning: Optional[bool] = None


@router.get("/{model_id}")
async def get_model(model_id: str) -> Dict[str, Any]:
    cfg = _svc.get_model_config(model_id)
    if cfg is None:
        raise HTTPException(404, f"模型 '{model_id}' 不存在")
    return cfg


@router.put("/{model_id}")
async def update_model(model_id: str, req: UpdateModelReq) -> Dict[str, str]:
    params = {k: v for k, v in req.model_dump().items() if v is not None}
    if not _svc.update_model(model_id, **params):
        raise HTTPException(404, f"模型 '{model_id}' 不存在")
    return {"status": "ok"}


@router.delete("/{model_id}")
async def remove_model(model_id: str) -> Dict[str, str]:
    if not _svc.remove_model(model_id):
        raise HTTPException(404, f"模型 '{model_id}' 不存在")
    return {"status": "ok"}


class RenameModelReq(BaseModel):
    new_id: str


@router.put("/{model_id}/rename")
async def rename_model(model_id: str, req: RenameModelReq) -> Dict[str, Any]:
    ok = _svc.rename_model(model_id, req.new_id)
    return {"ok": ok}


class MovePriorityReq(BaseModel):
    direction: int


@router.put("/{model_id}/priority-move/{model_type}")
async def move_priority(
    model_id: str, model_type: str, req: MovePriorityReq,
) -> Dict[str, Any]:
    ok = _svc.move_model_priority(model_type, model_id, req.direction)
    return {"ok": ok}
