"""外部 SDK 访问面，复用模型 SDK、实体 schema、权限入口及工具执行器。

模型看到：模型端点只传调用方显式参数；工具直调不自动发起思维轮次。
Token：没有额外模型调用或提示词；按原 SDK 和具体工具计费。
缓存：不修改稳定前缀，工具请求仅绑定运行期会话归属。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any, get_type_hints

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic_core import to_jsonable_python

from agent.channel.context import bind_current_channel, reset_current_channel
from agent.llm.model_api import ModelAPI, get_model_api
from core.conversation_scope import bind_scope, reset_scope
from core.entity import EntityRegistry
from core.tool_context import tool_request

from .schemas import SessionTarget
from .transport import portal_dependencies

if TYPE_CHECKING:
    from .adapter import HttpApiChannel


MODEL_OPERATIONS = (
    "chat", "describe_images", "describe_video", "embed", "embed_multimodal",
    "transcribe", "text_to_speech", "rerank", "generate_image", "edit_image",
    "generate_video", "generate_music", "judge",
)


class ToolRequest(SessionTarget):
    """单次工具直调，权限裁决使用 HTTP 会话与调用用户。"""

    arguments: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=128)


def _model_request(operation: str) -> type[BaseModel]:
    """从 SDK 公共签名派生输入声明，参数和默认值不维护第二份。"""
    method = getattr(ModelAPI, operation)
    annotations = get_type_hints(method)
    fields: dict[str, Any] = {
        name: (annotations[name], ... if parameter.default is inspect.Parameter.empty else parameter.default)
        for name, parameter in inspect.signature(method).parameters.items() if name != "self"
    }
    return create_model(f"Sdk_{operation}", __config__=ConfigDict(
        extra="forbid", val_json_bytes="base64", ser_json_bytes="base64", allow_inf_nan=False,
    ), **fields)


def _model_endpoint(operation: str, model: type[BaseModel]) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def endpoint(body: BaseModel) -> dict[str, Any]:
        api = get_model_api()
        method: Callable[..., Awaitable[Any]] = getattr(api, operation)
        # model_dump 会把 ImageContent 等 dataclass 降成 dict；SDK 需要其原始类型。
        arguments = {name: getattr(body, name) for name in type(body).model_fields}
        result = await method(**arguments)
        serialized = to_jsonable_python(result, bytes_mode="base64")
        if isinstance(serialized, dict):
            serialized.pop("raw", None)
        return {"status": "ok", "result": serialized}

    endpoint.__name__ = f"sdk_{operation}"
    endpoint.__annotations__["body"] = model
    return endpoint


def build_sdk_router(adapter: HttpApiChannel) -> APIRouter:
    """按能力拆分路由；新增能力经公开 SDK 或实体注册表扩展，统一沿用门户认证。"""
    router = APIRouter(prefix="/api/sdk", tags=["sdk"], dependencies=portal_dependencies())
    slots = asyncio.Semaphore(adapter.config.sdk_concurrency)

    async def capacity() -> AsyncIterator[None]:
        if slots.locked():
            raise HTTPException(503, "SDK 并发调用已达上限，请稍后重试", headers={"Retry-After": "1"})
        async with slots:
            yield

    for operation in MODEL_OPERATIONS:
        model = _model_request(operation)
        router.add_api_route(f"/models/{operation}", _model_endpoint(operation, model), methods=["POST"],
                             dependencies=[Depends(capacity)],
                             summary=inspect.getdoc(getattr(ModelAPI, operation)) or operation)

    @router.get("")
    async def discovery() -> dict[str, Any]:
        return {
            "api_version": "1", "portal": adapter.portal_spec(),
            "model_operations": list(MODEL_OPERATIONS),
            "tool_contract": "GET /api/sdk/tools 动态发现；POST /api/sdk/tools/{name} 通过统一权限入口调用",
            "execution_contract": "POST /api/sessions/control；stop 协作式停止，resume 作为新消息续接原会话",
        }

    @router.get("/models")
    async def models(model_type: str = "chat") -> dict[str, Any]:
        return {"status": "ok", "models": get_model_api().list_models(model_type)}

    @router.get("/tools")
    async def tools(group: str = "") -> dict[str, Any]:
        schemas = EntityRegistry.get_tool_schemas_by_group(group) if group else EntityRegistry.get_tool_schemas()
        return {"status": "ok", "tools": schemas}

    @router.post("/tools/{name}", dependencies=[Depends(capacity)])
    async def invoke_tool(name: str, body: ToolRequest) -> dict[str, Any]:
        from agent.approval import get_approval_gate
        from agent.hooks import hooks_active, run_event_hooks

        if not EntityRegistry.get_tool_schema_by_names([name]):
            raise HTTPException(404, "工具不存在或已禁用")
        dedup_key = json.dumps(["sdk-tool", body.scope, name, body.request_id])
        if adapter._dedup.is_duplicate(dedup_key):
            return {"status": "duplicate", "request_id": body.request_id}
        started = False
        scope_token = bind_scope(body.scope)
        channel_token = bind_current_channel(adapter.channel_id)
        try:
            with tool_request(body.scope, "http_api", message_id=body.request_id):
                arguments = json.dumps(body.arguments, ensure_ascii=False)
                if hooks_active("tool_pre"):
                    hook = await run_event_hooks("tool_pre", tool_name=name, arguments=arguments, scope=body.scope)
                    if not hook.allowed:
                        raise HTTPException(403, f"工具调用被 hook 阻塞: {hook.reason}")
                verdict = await get_approval_gate().check(
                    tool_name=name, tool_args=body.arguments, reason=f"外部应用调用工具 {name}",
                    scope=body.scope, channel_id=adapter.channel_id, user_id=body.user_id,
                )
                if not verdict.allowed:
                    raise HTTPException(403, verdict.reason or "工具调用未获许可")
                started = True
                entry = EntityRegistry.get(name)
                sensitive_result = bool(entry and entry.meta.get("sensitive_result"))
                raw = await EntityRegistry.execute_tool(name, arguments)
                if hooks_active("tool_post"):
                    from core.tool_results import redact_tool_result
                    try:
                        await run_event_hooks(
                            "tool_post", tool_name=name, arguments=arguments[:500], scope=body.scope,
                            result_preview=redact_tool_result(raw, sensitive_result=sensitive_result)[:400],
                        )
                    except Exception as exc:
                        from core.log import log
                        log(f"HTTP SDK tool_post 执行失败: {type(exc).__name__}", "DEBUG")
                try:
                    result: Any = json.loads(raw)
                except ValueError:
                    result = raw
                if isinstance(result, dict) and result.get("code") in {
                    "TOOL_ARGUMENTS_INVALID", "TOOL_DISABLED", "TOOL_NOT_FOUND", "TOOL_UNAVAILABLE",
                }:
                    started = False
                return {"status": "error" if isinstance(result, dict) and result.get("error") else "ok",
                        "result": result, "request_id": body.request_id, "notice": verdict.notice}
        finally:
            if not started:
                adapter._dedup.discard(dedup_key)
            reset_current_channel(channel_token)
            reset_scope(scope_token)

    return router
