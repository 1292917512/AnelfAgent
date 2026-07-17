"""进程内 Responses 会话表。"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.llm.responses.types import ResponseResult


@dataclass(slots=True)
class ResponseSession:
    """单次 Responses 调用会话。"""

    response_id: str
    model_id: str
    provider_id: str
    api_type: str
    api_base: str
    transport: str
    status: str = "in_progress"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: Optional[ResponseResult] = None
    raw: Optional[dict[str, Any]] = None
    task: Optional[asyncio.Task[Any]] = None
    error: Optional[dict[str, Any]] = None

    def touch(self) -> None:
        self.updated_at = time.time()


class ResponseSessionStore:
    """绑定 response_id 与 provider/model，支持取消与查询。"""

    def __init__(self, *, ttl_seconds: float = 3600.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, ResponseSession] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def new_response_id() -> str:
        return f"resp_{uuid.uuid4().hex}"

    async def create(
        self,
        *,
        model_id: str,
        provider_id: str,
        api_type: str,
        api_base: str,
        transport: str,
        response_id: str = "",
        task: Optional[asyncio.Task[Any]] = None,
    ) -> ResponseSession:
        await self.cleanup_expired()
        rid = response_id or self.new_response_id()
        session = ResponseSession(
            response_id=rid,
            model_id=model_id,
            provider_id=provider_id,
            api_type=api_type,
            api_base=api_base,
            transport=transport,
            task=task,
        )
        async with self._lock:
            self._sessions[rid] = session
        return session

    async def get(self, response_id: str) -> Optional[ResponseSession]:
        await self.cleanup_expired()
        async with self._lock:
            return self._sessions.get(response_id)

    async def require(
        self,
        response_id: str,
        *,
        provider_id: str = "",
        api_base: str = "",
    ) -> ResponseSession:
        session = await self.get(response_id)
        if session is None:
            raise KeyError(f"response 不存在: {response_id}")
        if provider_id and session.provider_id != provider_id:
            raise PermissionError("response_id 与 provider 不匹配")
        if api_base and session.api_base.rstrip("/") != api_base.rstrip("/"):
            raise PermissionError("response_id 与 api_base 不匹配")
        return session

    async def complete(
        self,
        response_id: str,
        *,
        result: ResponseResult,
        raw: Optional[dict[str, Any]] = None,
    ) -> ResponseSession:
        async with self._lock:
            session = self._sessions.get(response_id)
            if session is None:
                raise KeyError(f"response 不存在: {response_id}")
            session.status = result.status or "completed"
            session.result = result
            session.raw = raw if raw is not None else result.raw
            session.touch()
            return session

    async def fail(
        self,
        response_id: str,
        *,
        error: dict[str, Any],
        status: str = "failed",
    ) -> Optional[ResponseSession]:
        async with self._lock:
            session = self._sessions.get(response_id)
            if session is None:
                return None
            session.status = status
            session.error = error
            session.touch()
            return session

    async def cancel(self, response_id: str) -> ResponseSession:
        async with self._lock:
            session = self._sessions.get(response_id)
            if session is None:
                raise KeyError(f"response 不存在: {response_id}")
            task = session.task
            session.status = "cancelled"
            session.touch()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        return session

    async def delete(self, response_id: str) -> bool:
        async with self._lock:
            return self._sessions.pop(response_id, None) is not None

    async def cleanup_expired(self) -> int:
        now = time.time()
        async with self._lock:
            expired = [
                rid for rid, session in self._sessions.items()
                if now - session.updated_at > self._ttl_seconds
            ]
            for rid in expired:
                self._sessions.pop(rid, None)
        return len(expired)


_STORE: Optional[ResponseSessionStore] = None


def get_response_session_store() -> ResponseSessionStore:
    global _STORE
    if _STORE is None:
        _STORE = ResponseSessionStore()
    return _STORE
