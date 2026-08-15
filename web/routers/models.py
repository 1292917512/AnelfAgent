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


def _validate_reasoning_effort(value: str) -> str:
    from agent.llm.reasoning import normalize_effort
    normalized = normalize_effort(value)
    if value.strip() and not normalized:
        raise ValueError(
            f"无效的 reasoning_effort: {value}（可选 off/minimal/low/medium/high/xhigh/max）"
        )
    return normalized


ReasoningEffort = Annotated[str, AfterValidator(_validate_reasoning_effort)]


ApiType = Annotated[str, AfterValidator(_validate_api_type)]
ModelTypeValue = Literal[
    "chat", "vision", "embedding", "image_gen", "image_edit",
    "asr", "tts", "video", "music", "rerank",
]
VisionFormat = Literal["base64", "url", "both"]
ChatProtocolValue = Literal["chat_completions", "responses", "auto"]
_RESERVED_REQUEST_PARAMS = frozenset({
    "model", "messages", "prompt", "input", "tools", "tool_choice",
    "stream", "api_key", "api_base", "http_client", "extra_body",
    "extra_headers",
})


def _validate_request_params(value: Dict[str, Any]) -> Dict[str, Any]:
    collisions = _RESERVED_REQUEST_PARAMS.intersection(value)
    if collisions:
        raise ValueError(f"request_params 不允许覆盖保留参数: {sorted(collisions)}")
    return value


def _validate_extra_headers(value: Dict[str, str]) -> Dict[str, str]:
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError("extra_headers 必须是字符串键值对对象")
    return value


RequestParams = Annotated[Dict[str, Any], AfterValidator(_validate_request_params)]
ExtraHeaders = Annotated[Dict[str, str], AfterValidator(_validate_extra_headers)]

# 允许通过显式传 null 清除配置、恢复"由模型默认决定"的可选采样参数
_CLEARABLE_PARAM_FIELDS = frozenset({"temperature", "top_p", "max_tokens"})

def _normalize_model_params(req: BaseModel) -> Dict[str, Any]:
    """规范化扩展参数。

    用 exclude_unset 而非 exclude_none：显式传 null 的可选采样参数
    （temperature/max_tokens）需要透传给 update_config 以清除已有配置，
    恢复"由模型默认决定"的 auto 行为。
    """
    structured_fields = {"request_params", "extra_body", "extra_params", "extra_headers"}
    structured_supplied = bool(req.model_fields_set & structured_fields)
    params = req.model_dump(exclude_unset=True)
    # null 仅对白名单字段（可清除回 auto 的采样参数）透传，其余字段 null 视为未提供
    params = {
        k: v for k, v in params.items()
        if v is not None or k in _CLEARABLE_PARAM_FIELDS
    }
    request_params = params.pop("request_params", {})
    extra_body = params.pop("extra_body", {})
    legacy_extra = params.pop("extra_params", {})
    extra_headers = params.pop("extra_headers", {})

    merged_extra = dict(legacy_extra)
    merged_extra.update(extra_body)
    # type 判断而非 isinstance：UpdateModelReq 继承 CreateModelReq，
    # 更新语义下未显式提供结构化字段时不得回填默认值
    if structured_supplied or type(req) is CreateModelReq:
        params["request_params"] = request_params
        params["extra_body"] = merged_extra
        params["extra_params"] = {}
        params["extra_headers"] = extra_headers
    return params


def _serialize_model_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """将内部模型格式转换为公开 API 格式。"""
    result = dict(config)
    result.setdefault("request_params", {})
    result.setdefault("extra_headers", {})
    result.setdefault("chat_protocol", "chat_completions")
    result.setdefault("builtin_tools", [])
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
    media_protocol: str = ""


@router.post("/providers")
async def create_provider(req: CreateProviderReq) -> Dict[str, Any]:
    ok = _svc.add_provider(
        req.id, name=req.name, base_url=req.base_url,
        api_key=req.api_key, api_type=req.api_type, proxy_url=req.proxy_url,
        media_protocol=req.media_protocol,
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
    media_protocol: Optional[str] = None


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
    except ValueError as e:
        # 非法 base_url（_validate_remote_url）属于客户端输入错误
        raise HTTPException(400, str(e)) from e
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
    except ValueError as e:
        # 非法 base_url（_validate_remote_url）属于客户端输入错误
        raise HTTPException(400, str(e)) from e
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


class ModelInfoBatchReq(BaseModel):
    models: List[str] = Field(max_length=500)
    api_type: ApiType = "openai"


@router.post("/model-info/batch")
async def get_model_info_batch(req: ModelInfoBatchReq) -> Dict[str, Any]:
    """批量查询模型能力信息（litellm 本地模型表，一次请求返回全部）。

    供远程模型浏览器的行内元数据展示与批量添加使用，
    避免逐模型一次 HTTP 往返。
    """
    return {
        "info": {m: _svc.get_model_info(m, req.api_type) for m in dict.fromkeys(req.models)}
    }


class CreateModelReq(BaseModel):
    id: str
    model: str = ""
    model_types: List[ModelTypeValue] = Field(default_factory=lambda: ["chat"])
    temperature: Optional[float] = Field(
        default=None, ge=0, le=2,
        description="可选采样温度；缺省不下发，由 provider/SDK 按模型默认决定",
    )
    top_p: Optional[float] = Field(
        default=None, ge=0, le=1,
        description="可选 nucleus 采样；缺省不下发，由 provider/SDK 按模型默认决定",
    )
    max_tokens: Optional[int] = Field(
        default=None, ge=0,
        description="可选输出预算上限；缺省不限制，由 provider/SDK 按模型默认决定",
    )
    frequency_penalty: float = Field(default=0.0, ge=-2, le=2)
    presence_penalty: float = Field(default=0.0, ge=-2, le=2)
    timeout: float = Field(default=120.0, gt=0)
    context_window: int = Field(default=0, ge=0)
    supports_vision: bool = False
    supports_tools: bool = True
    supports_forced_tool_choice: bool = True
    vision_format: VisionFormat = "base64"
    supports_reasoning: bool = False
    reasoning_effort: ReasoningEffort = ""
    chat_protocol: ChatProtocolValue = "chat_completions"
    builtin_tools: List[Any] = Field(
        default_factory=list,
        description="供应商内置工具声明（服务端执行，如 web_search/code_interpreter）；与本地同名工具冲突时内置优先",
    )
    request_params: RequestParams = Field(default_factory=dict)
    extra_body: Dict[str, Any] = Field(default_factory=dict)
    extra_params: Dict[str, Any] = Field(
        default_factory=dict,
    )
    extra_headers: ExtraHeaders = Field(
        default_factory=dict,
        description="自定义请求头，最后应用到 HTTP 请求（可覆盖鉴权头）",
    )
    enabled: bool = True


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
        raise HTTPException(400, f"模型 '{req.model_id}' 不存在、已停用或不支持工具调用，无法设为默认对话模型")
    return {"status": "ok"}


# ── 子代理统一注册表（内置难度档 + 自定义档案） ──────────────────────


@router.get("/sub-agents")
async def list_sub_agents() -> Dict[str, Any]:
    """全部子代理档案（内置难度档在前，含模型可用性标记）。"""
    return {"sub_agents": _svc.list_sub_agents()}


class SubAgentCreateReq(BaseModel):
    name: str
    model_id: str
    description: str = ""


@router.post("/sub-agents")
async def create_sub_agent(req: SubAgentCreateReq) -> Dict[str, Any]:
    ok, message = _svc.create_sub_agent(req.name, req.model_id, req.description)
    if not ok:
        raise HTTPException(400, message)
    return {"status": "ok", "message": message}


class SubAgentUpdateReq(BaseModel):
    model_id: Optional[str] = None
    models: Optional[List[str]] = None
    description: Optional[str] = None


@router.put("/sub-agents/{name}")
async def update_sub_agent(name: str, req: SubAgentUpdateReq) -> Dict[str, Any]:
    ok, message = _svc.update_sub_agent(
        name,
        model_id=req.model_id or "",
        models=req.models,
        description=req.description or "",
    )
    if not ok:
        raise HTTPException(404, message)
    return {"status": "ok", "message": message}


@router.delete("/sub-agents/{name}")
async def delete_sub_agent(name: str) -> Dict[str, str]:
    ok, message = _svc.remove_sub_agent(name)
    if not ok:
        raise HTTPException(404 if "不存在" in message else 400, message)
    return {"status": "ok", "message": message}


class TestConnectionReq(BaseModel):
    base_url: str
    api_key: str = ""
    provider_id: str = ""
    api_type: ApiType = "openai"
    extra_headers: ExtraHeaders = Field(default_factory=dict)


@router.post("/test")
async def test_connection(req: TestConnectionReq) -> Dict[str, str]:
    try:
        api_key = _svc.resolve_provider_api_key(req.provider_id, req.api_key)
        api_type = req.api_type
        if req.provider_id:
            prov = _svc.get_provider(req.provider_id)
            if prov is not None:
                api_type = prov.get("api_type", api_type)
        result = await _svc.test_connection(
            req.base_url, api_key, api_type, req.extra_headers or None,
        )
        return {"result": result}
    except ValueError as e:
        # 非法 base_url（_validate_remote_url）属于客户端输入错误
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        return {
            "result": f"连接失败: {_svc.sanitize_error(e, req.api_key)}",
        }


@router.get("/api-types")
async def list_api_types() -> Dict[str, Any]:
    """返回支持的 api_type 列表（单一权威来源，前端不再硬编码）。

    common 组（openai/anthropic）为两大主流协议，市面上绝大多数中转/
    国产模型都是其兼容实现；其余归为 other。
    """
    from agent.llm.config import API_TYPES, DEFAULT_BASE_URLS

    common = {"openai", "anthropic"}
    return {
        "api_types": [
            {
                "value": t,
                "group": "common" if t in common else "other",
                "default_base_url": DEFAULT_BASE_URLS.get(t, ""),
            }
            for t in API_TYPES
        ]
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
            req.base_url, api_key, req.model, req.api_type, provider_id=req.provider_id,
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
    """从 GitHub 拉取最新 LiteLLM 模型价格表并合并（保留自定义注册模型），支持代理。"""
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
            # register_model 逐条合并并失效 litellm 内部缓存，
            # 整表替换会丢弃自定义模型注册且绕过缓存失效
            litellm.register_model(data)
            return {"status": "ok", "model_count": len(litellm.model_cost)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"更新失败: {e}") from e


# ── 模型（动态路径 /{model_id}，放最后避免吞掉固定路径） ────────────


class UpdateModelReq(CreateModelReq):
    """更新请求：字段与创建一致，全部改为可选（仅透传显式提供的字段）。"""

    id: Optional[str] = None
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
    reasoning_effort: Optional[ReasoningEffort] = None
    chat_protocol: Optional[ChatProtocolValue] = None
    builtin_tools: Optional[List[Any]] = None
    request_params: Optional[RequestParams] = None
    extra_body: Optional[Dict[str, Any]] = None
    extra_params: Optional[Dict[str, Any]] = None
    extra_headers: Optional[ExtraHeaders] = None


class TestChatReq(BaseModel):
    """真实链路对话测试（保存并测试）。

    model_id 指向已保存模型时以其配置为基底；draft 为编辑中的模型草稿
    （模型级字段），合并后构造临时客户端走真实流式链路。
    """

    provider_id: str
    model_id: str = ""
    draft: Optional[UpdateModelReq] = None


@router.post("/test-chat")
async def test_chat(req: TestChatReq) -> Dict[str, Any]:
    try:
        draft = _normalize_model_params(req.draft) if req.draft is not None else None
        if draft is not None:
            draft.pop("id", None)
        return await _svc.test_chat(req.provider_id, req.model_id, draft)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        return {"ok": False, "error": _svc.sanitize_error(e)}


@router.get("/{model_id}")
async def get_model(model_id: str) -> Dict[str, Any]:
    cfg = _svc.get_model_config(model_id)
    if cfg is None:
        raise HTTPException(404, f"模型 '{model_id}' 不存在")
    return _serialize_model_config(cfg)


@router.put("/{model_id}")
async def update_model(model_id: str, req: UpdateModelReq) -> Dict[str, str]:
    params = _normalize_model_params(req)
    params.pop("id", None)  # 更新不允许改 id（继承自 CreateModelReq 的字段）
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
