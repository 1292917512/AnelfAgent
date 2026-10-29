"""网关流的执行归属、背压与终态记录。

Model Experience:
- 模型看到什么：供应商请求和输出内容原样传递，响应身份与终态同步到网关。
- token 影响：无额外模型调用或 token。
- 缓存影响：不修改模型输入或任何上下文前缀。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable

from agent.llm.responses.client import parse_responses_payload
from agent.llm.responses.session import TERMINAL_STATUSES, ResponseSession, ResponseSessionStore
from agent.llm.responses.types import ResponseStreamEvent
from core.async_helper import finish_owned
from core.log import log


async def stream_response(
    source: AsyncGenerator[ResponseStreamEvent, None],
    *,
    sessions: ResponseSessionStore,
    model_id: str,
    provider_id: str,
    api_type: str,
    api_base: str,
    transport: str,
    map_error: Callable[[Exception], Exception],
) -> AsyncGenerator[ResponseStreamEvent, None]:
    """由独立任务读取上游；取消和消费端关闭均等待上游释放资源。"""
    queue: asyncio.Queue[ResponseStreamEvent] = asyncio.Queue(maxsize=1)
    session: ResponseSession | None = None

    async def produce() -> None:
        nonlocal session
        try:
            try:
                async for event in source:
                    response = event.data.get("response")
                    if isinstance(response, dict):
                        rid = response.get("id")
                        if not isinstance(rid, str) or not rid:
                            raise ValueError("供应商响应缺少 id")
                        if session is None:
                            session = await sessions.create(
                                model_id=model_id, provider_id=provider_id, api_type=api_type,
                                api_base=api_base, transport=transport, response_id=rid,
                                task=asyncio.current_task(),
                            )
                        elif rid != session.response_id:
                            raise ValueError("供应商流中的 response id 发生变化")
                    if event.type in {"error", "response.error"}:
                        if session is not None:
                            error = event.data.get("error", event.data)
                            await sessions.fail(session.response_id, error=error)
                        await queue.put(event)
                        return
                    if session is None:
                        raise ValueError("供应商流在创建响应前发送了内容")
                    if event.type.removeprefix("response.") in TERMINAL_STATUSES:
                        if not isinstance(response, dict):
                            raise ValueError("供应商终态事件缺少 response")
                        payload = dict(response)
                        payload["status"] = event.type.removeprefix("response.")
                        await sessions.complete(session.response_id, result=parse_responses_payload(payload, transport=transport))
                        await queue.put(ResponseStreamEvent(event.type, {**event.data, "response": payload}, True))
                        return
                    await queue.put(event)
            finally:
                await finish_owned(asyncio.create_task(source.aclose()))
            raise RuntimeError("Responses 流在缺少终态事件时结束")
        except asyncio.CancelledError:
            if session is not None:
                await sessions.cancel(session.response_id)
            raise
        except Exception as exc:
            mapped = map_error(exc)
            if session is not None and session.status in TERMINAL_STATUSES:
                log(f"Responses 终态后的清理失败: {mapped}", "WARNING")
                return
            if session is not None:
                await sessions.fail(session.response_id, error={"message": str(mapped), "type": "server_error"})
            raise mapped from exc
        finally:
            if session is not None:
                session.task = None

    task = asyncio.create_task(produce(), name="responses-stream")
    pending: asyncio.Task[ResponseStreamEvent] | None = None
    try:
        while not task.done() or not queue.empty():
            if not queue.empty():
                yield queue.get_nowait()
                continue
            pending = asyncio.create_task(queue.get())
            await asyncio.wait((pending, task), return_when=asyncio.FIRST_COMPLETED)
            if pending.done():
                yield pending.result()
            else:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            pending = None
        if task.cancelled():
            if session is not None and session.status == "cancelled":
                yield ResponseStreamEvent("response.cancelled", {
                    "type": "response.cancelled", "response": {
                        "id": session.response_id, "object": "response", "status": "cancelled", "model": model_id,
                    },
                }, True)
        else:
            task.result()
    finally:
        if pending is not None:
            pending.cancel()
        if not task.done() and not task.cancelling():
            task.cancel()
        cleanup = asyncio.gather(task, *([pending] if pending is not None else []), return_exceptions=True)
        await finish_owned(cleanup)
