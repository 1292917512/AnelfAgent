"""模型管理 API 路由（供应商-模型层级）。"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import AfterValidator, BaseModel, Field

from services import ModelService

router = APIRouter(prefix="/models", tags=["models"])

_svc = ModelService()


def _validate_api_type(value: str) -> str:
    from agent.llm.llm_client import API_TYPES
    if value not in API_TYPES:
        raise ValueError(f"不支持的 api_type: {value}")
    return value


ApiType = Annotated[str, AfterValidator(_validate_api_type)]
ModelTypeValue = Literal[
    "chat", "vision", "embedding", "image_gen", "image_edit",
    "asr", "tts", "video", "rerank",
]
VisionFormat = Literal["base64", "url", "both"]
ChatProtocolValue = Literal["chat_completions", "responses", "auto"]
_RESERVED_REQUEST_PARAMS = frozenset({
    "model", "messages", "prompt", "input", "tools", "tool_choice",
    "stream", "api_key", "api_base", "http_client", "extra_body",
})


def _validate_request_params(value: Dict[str, Any]) -> Dict[str, Any]:
    collisions = _RESERVED_REQUEST_PARAMS.intersection(value)
    if collisions:
        raise ValueError(f"request_params 不允许覆盖保留参数: {sorted(collisions)}")
    return value


RequestParams = Annotated[Dict[str, Any], AfterValidator(_validate_request_params)]

def _normalize_model_params(req: BaseModel) -> Dict[str, Any]:
    """规范化扩展参数，并兼容旧 extra_params 字段。"""
    structured_fields = {"request_params", "extra_body", "extra_params"}
    structured_supplied = bool(req.model_fields_set & structured_fields)
    params = req.model_dump(exclude_none=True)
    request_params = params.pop("request_params", {})
    extra_body = params.pop("extra_body", {})
    legacy_extra = params.pop("extra_params", {})

    merged_extra = dict(legacy_extra)
    merged_extra.update(extra_body)
    if structured_supplied or isinstance(req, CreateModelReq):
        params["request_params"] = request_params
        params["extra_body"] = merged_extra
        params["extra_params"] = {}
    return params


def _serialize_model_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """将内部模型格式转换为公开 API 格式。"""
    result = dict(config)
    result.setdefault("request_params", {})
    result.setdefault("chat_protocol", "chat_completions")
    legacy_extra = result.pop("extra_params", {})
    extra_body = dict(legacy_extra)
    extra_body.update(result.get("extra_body", {}))
    result["extra_body"] = extra_body
    return result

# ── 供应商 ───────────────────────────────────────────────────────────


@router.get("/providers")
async def list_providers() -> List[Dict[str, Any]]:
    return _svc.list_providers()


class CreateProviderReq(BaseModel):
    id: str
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    api_type: ApiType = "openai"
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
    api_type: Optional[ApiType] = None
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
    return [
        _serialize_model_config(model)
        for model in _svc.list_provider_models(pid)
    ]


@router.get("/providers/{pid}/remote-models")
async def fetch_remote_models(pid: str) -> Dict[str, Any]:
    """从供应商 API 拉取远程可用模型列表。"""
    try:
        models = await _svc.fetch_provider_remote_models(pid)
        existing = set(_svc.get_all_model_ids())
        for m in models:
            m["already_added"] = m["id"] in existing
        return {"models": models}
    except Exception as e:
        raise HTTPException(
            502,
            f"获取远程模型列表失败: {_svc.sanitize_error(e)}",
        ) from e


class FetchRemoteReq(BaseModel):
    base_url: str
    api_key: str = ""


@router.post("/remote-models")
async def fetch_remote_models_generic(req: FetchRemoteReq) -> Dict[str, Any]:
    """通过指定 URL 拉取远程可用模型列表。"""
    try:
        models = await _svc.fetch_remote_models(req.base_url, req.api_key)
        existing = set(_svc.get_all_model_ids())
        for m in models:
            m["already_added"] = m["id"] in existing
        return {"models": models}
    except Exception as e:
        raise HTTPException(
            502,
            f"获取远程模型列表失败: {_svc.sanitize_error(e, req.api_key)}",
        ) from e


class ModelInfoReq(BaseModel):
    model: str
    api_type: ApiType = "openai"


@router.post("/model-info")
async def get_model_info(req: ModelInfoReq) -> Dict[str, Any]:
    """通过 litellm 查询模型能力信息（max_tokens / vision / tools 等）。"""
    return _svc.get_model_info(req.model, req.api_type)


class CreateModelReq(BaseModel):
    id: str
    model: str = ""
    model_types: List[ModelTypeValue] = Field(default_factory=lambda: ["chat"])
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=1.0, ge=0, le=1)
    max_tokens: int = Field(default=4096, ge=0)
    frequency_penalty: float = Field(default=0.0, ge=-2, le=2)
    presence_penalty: float = Field(default=0.0, ge=-2, le=2)
    timeout: float = Field(default=120.0, gt=0)
    context_window: int = Field(default=0, ge=0)
    supports_vision: bool = False
    supports_tools: bool = True
    supports_forced_tool_choice: bool = True
    vision_format: VisionFormat = "base64"
    supports_reasoning: bool = False
    chat_protocol: ChatProtocolValue = "chat_completions"
    request_params: RequestParams = Field(default_factory=dict)
    extra_body: Dict[str, Any] = Field(default_factory=dict)
    extra_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="已弃用：兼容旧客户端，按 extra_body 处理",
    )


@router.post("/providers/{pid}/models")
async def create_model(pid: str, req: CreateModelReq) -> Dict[str, Any]:
    params = _normalize_model_params(req)
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
    provider_id: str = ""


@router.post("/test")
async def test_connection(req: TestConnectionReq) -> Dict[str, str]:
    try:
        api_key = _svc.resolve_provider_api_key(req.provider_id, req.api_key)
        result = await _svc.test_connection(req.base_url, api_key)
        return {"result": result}
    except Exception as e:
        return {
            "result": f"连接失败: {_svc.sanitize_error(e, req.api_key)}",
        }


class ProbeReq(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    api_type: ApiType = "openai"
    provider_id: str = ""


@router.post("/probe")
async def probe_capabilities(req: ProbeReq) -> Dict[str, Any]:
    try:
        api_key = _svc.resolve_provider_api_key(req.provider_id, req.api_key)
        return await _svc.probe_capabilities(
            req.base_url, api_key, req.model, req.api_type,
        )
    except Exception as e:
        return {"error": _svc.sanitize_error(e, req.api_key)}


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

    proxy: Optional[str] = None
    if req.proxy_url:
        url = req.proxy_url.strip()
        if not url.startswith(("http://", "https://", "socks5://")):
            url = f"http://{url}"
        proxy = url

    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=30.0) as client:
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
    model_types: Optional[List[ModelTypeValue]] = None
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    top_p: Optional[float] = Field(default=None, ge=0, le=1)
    max_tokens: Optional[int] = Field(default=None, ge=0)
    frequency_penalty: Optional[float] = Field(default=None, ge=-2, le=2)
    presence_penalty: Optional[float] = Field(default=None, ge=-2, le=2)
    timeout: Optional[float] = Field(default=None, gt=0)
    context_window: Optional[int] = Field(default=None, ge=0)
    supports_vision: Optional[bool] = None
    supports_tools: Optional[bool] = None
    supports_forced_tool_choice: Optional[bool] = None
    vision_format: Optional[VisionFormat] = None
    supports_reasoning: Optional[bool] = None
    chat_protocol: Optional[ChatProtocolValue] = None
    request_params: Optional[RequestParams] = None
    extra_body: Optional[Dict[str, Any]] = None
    extra_params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="已弃用：兼容旧客户端，按 extra_body 处理",
    )


@router.get("/{model_id}")
async def get_model(model_id: str) -> Dict[str, Any]:
    cfg = _svc.get_model_config(model_id)
    if cfg is None:
        raise HTTPException(404, f"模型 '{model_id}' 不存在")
    return _serialize_model_config(cfg)


@router.put("/{model_id}")
async def update_model(model_id: str, req: UpdateModelReq) -> Dict[str, str]:
    params = _normalize_model_params(req)
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
