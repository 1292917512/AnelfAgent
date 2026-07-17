"""OpenAI 兼容 Responses API 网关（根路径 /v1/responses）。"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent.llm.responses.session import get_response_session_store
from services.responses import ResponsesService, ResponsesServiceError

router = APIRouter(prefix="/responses", tags=["v1-responses"])
_svc = ResponsesService()


class CreateResponseReq(BaseModel):
    model: str
    input: Union[str, List[Dict[str, Any]]]
    instructions: str = ""
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    previous_response_id: str = ""
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    store: Optional[bool] = None
    stream: bool = False
    extra: Optional[Dict[str, Any]] = None


class CompactResponseReq(BaseModel):
    model: str = ""
    input: Optional[Union[str, List[Dict[str, Any]]]] = None
    instructions: str = ""
    previous_response_id: str = ""
    extra: Optional[Dict[str, Any]] = None


def _error_response(exc: ResponsesServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_openai_error())


@router.post("")
@router.post("/")
async def create_response(
    req: CreateResponseReq,
    request: Request,
) -> Any:
    if req.stream:
        store = get_response_session_store()
        response_id = store.new_response_id()

        async def event_gen() -> AsyncGenerator[str, None]:
            try:
                async for event in _svc.stream(
                    model=req.model,
                    input=req.input,
                    instructions=req.instructions,
                    tools=req.tools,
                    tool_choice=req.tool_choice,
                    previous_response_id=req.previous_response_id,
                    temperature=req.temperature,
                    top_p=req.top_p,
                    max_output_tokens=req.max_output_tokens,
                    metadata=req.metadata,
                    store=req.store,
                    extra=req.extra,
                    response_id=response_id,
                ):
                    if await request.is_disconnected():
                        try:
                            await _svc.cancel(response_id)
                        except Exception:
                            pass
                        break
                    yield _svc.encode_sse(event)
            except ResponsesServiceError as exc:
                yield (
                    "event: error\n"
                    f"data: {json.dumps(exc.to_openai_error(), ensure_ascii=False)}\n\n"
                )
            except Exception as exc:
                mapped = _svc.map_exception(exc)
                yield (
                    "event: error\n"
                    f"data: {json.dumps(mapped.to_openai_error(), ensure_ascii=False)}\n\n"
                )

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        return await _svc.create(
            model=req.model,
            input=req.input,
            instructions=req.instructions,
            tools=req.tools,
            tool_choice=req.tool_choice,
            previous_response_id=req.previous_response_id,
            temperature=req.temperature,
            top_p=req.top_p,
            max_output_tokens=req.max_output_tokens,
            metadata=req.metadata,
            store=req.store,
            extra=req.extra,
        )
    except ResponsesServiceError as exc:
        return _error_response(exc)


@router.post("/compact")
async def compact_response(req: CompactResponseReq) -> Any:
    try:
        return await _svc.compact(
            model=req.model,
            input=req.input,
            instructions=req.instructions,
            previous_response_id=req.previous_response_id,
            extra=req.extra,
        )
    except ResponsesServiceError as exc:
        return _error_response(exc)


@router.get("/{response_id}")
async def get_response(response_id: str) -> Any:
    try:
        return await _svc.get(response_id)
    except ResponsesServiceError as exc:
        return _error_response(exc)


@router.delete("/{response_id}")
async def delete_response(response_id: str) -> Any:
    try:
        return await _svc.delete(response_id)
    except ResponsesServiceError as exc:
        return _error_response(exc)


@router.post("/{response_id}/cancel")
async def cancel_response(response_id: str) -> Any:
    try:
        return await _svc.cancel(response_id)
    except ResponsesServiceError as exc:
        return _error_response(exc)
