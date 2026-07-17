"""Responses API 编排服务。"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional, Union

from agent.llm.responses.router import ResponsesCapabilityError
from agent.llm.responses.session import ResponseSessionStore, get_response_session_store
from agent.llm.responses.types import ResponseResult, ResponseStreamEvent
from services.model import ModelService


class ResponsesServiceError(Exception):
    """对外可映射为 OpenAI error 对象的业务异常。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        error_type: str = "invalid_request_error",
        code: str = "",
        param: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.param = param

    def to_openai_error(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message": self.message,
            "type": self.error_type,
        }
        if self.code:
            payload["code"] = self.code
        if self.param:
            payload["param"] = self.param
        return {"error": payload}


class ResponsesService:
    """编排 create/stream/lifecycle，并对齐 OpenAI error 格式。"""

    def __init__(
        self,
        *,
        session_store: Optional[ResponseSessionStore] = None,
        model_service: Optional[ModelService] = None,
    ) -> None:
        self._sessions = session_store or get_response_session_store()
        self._models = model_service or ModelService()

    def _manager(self) -> Any:
        from agent.llm import get_llm_manager
        return get_llm_manager()

    def resolve_client(self, model: str) -> Any:
        client = self._manager().resolve_client(model)
        if client is None:
            raise ResponsesServiceError(
                f"模型不存在: {model}",
                status_code=404,
                error_type="invalid_request_error",
                code="model_not_found",
                param="model",
            )
        return client

    def sanitize_message(self, message: str, *extra_secrets: str) -> str:
        return self._models.sanitize_error(Exception(message), *extra_secrets)

    def map_exception(self, exc: Exception, *extra_secrets: str) -> ResponsesServiceError:
        if isinstance(exc, ResponsesServiceError):
            exc.message = self.sanitize_message(exc.message, *extra_secrets)
            return exc
        if isinstance(exc, ResponsesCapabilityError):
            return ResponsesServiceError(
                self.sanitize_message(str(exc), *extra_secrets),
                status_code=400,
                error_type="invalid_request_error",
                code="unsupported_capability",
            )
        if isinstance(exc, KeyError):
            return ResponsesServiceError(
                self.sanitize_message(str(exc), *extra_secrets),
                status_code=404,
                error_type="invalid_request_error",
                code="response_not_found",
            )
        if isinstance(exc, PermissionError):
            return ResponsesServiceError(
                self.sanitize_message(str(exc), *extra_secrets),
                status_code=403,
                error_type="invalid_request_error",
                code="response_provider_mismatch",
            )
        return ResponsesServiceError(
            self.sanitize_message(str(exc), *extra_secrets),
            status_code=500,
            error_type="server_error",
            code="internal_error",
        )

    @staticmethod
    def result_to_dict(result: ResponseResult) -> dict[str, Any]:
        if result.raw:
            payload = dict(result.raw)
            payload.setdefault("id", result.id)
            payload.setdefault("status", result.status)
            payload.setdefault("model", result.model)
            return payload
        usage = None
        if result.usage is not None:
            usage = {
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "total_tokens": result.usage.total_tokens,
            }
        return {
            "id": result.id,
            "object": "response",
            "status": result.status,
            "model": result.model,
            "output": result.output,
            "output_text": result.output_text,
            "usage": usage,
            "error": result.error,
            "previous_response_id": result.previous_response_id or None,
        }

    async def create(
        self,
        *,
        model: str,
        input: Union[str, list[dict[str, Any]]],
        instructions: str = "",
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        previous_response_id: str = "",
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
        store: Optional[bool] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        client = self.resolve_client(model)
        if previous_response_id:
            await self._sessions.require(
                previous_response_id,
                provider_id=client.config.provider_id,
                api_base=client.config.base_url,
            )
        try:
            result = await client.responses_create(
                input=input,
                instructions=instructions,
                tools=tools,
                tool_choice=tool_choice,
                previous_response_id=previous_response_id,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_output_tokens,
                metadata=metadata,
                store=store,
                extra=extra,
            )
        except Exception as exc:
            raise self.map_exception(exc, client.config.api_key) from exc

        response_id = result.id or self._sessions.new_response_id()
        if not result.id:
            result.id = response_id
        route = client.responses_client().route
        await self._sessions.create(
            model_id=client.config.name,
            provider_id=client.config.provider_id,
            api_type=client.config.api_type,
            api_base=client.config.base_url,
            transport=route.transport.value,
            response_id=response_id,
        )
        await self._sessions.complete(response_id, result=result)
        return self.result_to_dict(result)

    async def stream(
        self,
        *,
        model: str,
        input: Union[str, list[dict[str, Any]]],
        instructions: str = "",
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        previous_response_id: str = "",
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
        store: Optional[bool] = None,
        extra: Optional[dict[str, Any]] = None,
        response_id: str = "",
    ) -> AsyncGenerator[ResponseStreamEvent, None]:
        client = self.resolve_client(model)
        if previous_response_id:
            await self._sessions.require(
                previous_response_id,
                provider_id=client.config.provider_id,
                api_base=client.config.base_url,
            )
        route = client.responses_client().route
        rid = response_id or self._sessions.new_response_id()
        await self._sessions.create(
            model_id=client.config.name,
            provider_id=client.config.provider_id,
            api_type=client.config.api_type,
            api_base=client.config.base_url,
            transport=route.transport.value,
            response_id=rid,
        )
        final_result: Optional[ResponseResult] = None
        try:
            async for event in client.responses_stream(
                input=input,
                instructions=instructions,
                tools=tools,
                tool_choice=tool_choice,
                previous_response_id=previous_response_id,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_output_tokens,
                metadata=metadata,
                store=store,
                extra=extra,
            ):
                data = dict(event.data)
                if "response" in data and isinstance(data["response"], dict):
                    resp = dict(data["response"])
                    if not resp.get("id"):
                        resp["id"] = rid
                    data["response"] = resp
                    if event.type == "response.completed":
                        from agent.llm.responses.client import parse_responses_payload
                        final_result = parse_responses_payload(
                            resp, transport=route.transport.value,
                        )
                elif event.type.startswith("response.") and "id" not in data:
                    data.setdefault("id", rid)
                yield ResponseStreamEvent(
                    type=event.type,
                    data=data,
                    is_terminal=event.is_terminal,
                )
            if final_result is not None:
                await self._sessions.complete(rid, result=final_result)
            else:
                await self._sessions.complete(
                    rid,
                    result=ResponseResult(id=rid, status="completed", model=model),
                )
        except Exception as exc:
            mapped = self.map_exception(exc, client.config.api_key)
            await self._sessions.fail(
                rid,
                error=mapped.to_openai_error()["error"],
            )
            raise mapped from exc

    async def get(self, response_id: str) -> dict[str, Any]:
        session = await self._sessions.get(response_id)
        if session is not None:
            if session.result is not None:
                return self.result_to_dict(session.result)
            if session.error is not None:
                return {
                    "id": response_id,
                    "object": "response",
                    "status": session.status,
                    "error": session.error,
                }
            if session.transport == "native":
                client = self._manager().get_client(session.model_id)
                if client is not None:
                    try:
                        result = await client.responses_get(response_id)
                        await self._sessions.complete(response_id, result=result)
                        return self.result_to_dict(result)
                    except Exception as exc:
                        raise self.map_exception(exc, client.config.api_key) from exc
            return {
                "id": response_id,
                "object": "response",
                "status": session.status,
                "model": session.model_id,
            }
        raise ResponsesServiceError(
            f"response 不存在: {response_id}",
            status_code=404,
            code="response_not_found",
        )

    async def delete(self, response_id: str) -> dict[str, Any]:
        session = await self._sessions.get(response_id)
        if session is None:
            raise ResponsesServiceError(
                f"response 不存在: {response_id}",
                status_code=404,
                code="response_not_found",
            )
        if session.transport == "native":
            client = self._manager().get_client(session.model_id)
            if client is not None:
                try:
                    await client.responses_delete(response_id)
                except Exception as exc:
                    # 本地会话仍可删除；native 失败时继续清理本地。
                    _ = self.map_exception(exc, client.config.api_key)
        await self._sessions.delete(response_id)
        return {
            "id": response_id,
            "object": "response",
            "deleted": True,
        }

    async def cancel(self, response_id: str) -> dict[str, Any]:
        session = await self._sessions.get(response_id)
        if session is None:
            raise ResponsesServiceError(
                f"response 不存在: {response_id}",
                status_code=404,
                code="response_not_found",
            )
        if session.transport == "native":
            client = self._manager().get_client(session.model_id)
            if client is not None:
                try:
                    result = await client.responses_cancel(response_id)
                    await self._sessions.complete(response_id, result=result)
                    return self.result_to_dict(result)
                except Exception as exc:
                    raise self.map_exception(exc, client.config.api_key) from exc
        cancelled = await self._sessions.cancel(response_id)
        if cancelled.result is not None:
            payload = self.result_to_dict(cancelled.result)
            payload["status"] = "cancelled"
            return payload
        return {
            "id": response_id,
            "object": "response",
            "status": "cancelled",
            "model": cancelled.model_id,
        }

    async def compact(
        self,
        *,
        model: str = "",
        input: Union[str, list[dict[str, Any]], None] = None,
        instructions: str = "",
        previous_response_id: str = "",
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        client = None
        if previous_response_id:
            session = await self._sessions.require(previous_response_id)
            client = self._manager().get_client(session.model_id)
            if client is None and model:
                client = self.resolve_client(model)
        elif model:
            client = self.resolve_client(model)
        if client is None:
            raise ResponsesServiceError(
                "compact 需要 model 或 previous_response_id",
                status_code=400,
                param="model",
            )
        route = client.responses_client().route
        if route.transport.value != "native":
            raise ResponsesServiceError(
                "compact 仅支持 native OpenAI/Azure Responses",
                status_code=501,
                code="unsupported_capability",
            )
        try:
            result = await client.responses_compact(
                input=input if input is not None else "",
                instructions=instructions,
                previous_response_id=previous_response_id,
                extra=extra,
            )
        except Exception as exc:
            raise self.map_exception(exc, client.config.api_key) from exc
        return self.result_to_dict(result)

    @staticmethod
    def encode_sse(event: ResponseStreamEvent) -> str:
        payload = json.dumps(event.data, ensure_ascii=False)
        return f"event: {event.type}\ndata: {payload}\n\n"
