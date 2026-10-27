"""HTTP 门户的认证和流式请求体边界；不访问业务状态或模型。"""

from __future__ import annotations

from collections import deque
from typing import Callable
from uuid import uuid4

from fastapi import Security
from fastapi.params import Depends as Dependency
from fastapi.security import APIKeyHeader, HTTPBearer
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def portal_dependencies() -> list[Dependency]:
    """OpenAPI 声明两种等价凭据；实际认证在读取请求体之前完成。"""
    return [Security(APIKeyHeader(name="x-api-token", auto_error=False)), Security(HTTPBearer(auto_error=False))]


class HttpBoundary:
    """解析业务参数前认证；累计检查真实请求体，兼容无 Content-Length 的分块上传。"""

    def __init__(self, app: ASGIApp, *, authenticate: Callable[[Request], bool], max_body: int) -> None:
        self.app = app
        self.authenticate = authenticate
        self.max_body = max_body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        original_send = send

        async def traced_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                if not any(name.lower() == b"x-request-id" for name, _ in headers):
                    headers.append((b"x-request-id", request_id.encode()))
            await original_send(message)

        send = traced_send
        route_path = scope["path"]
        root = scope.get("root_path", "").rstrip("/")
        if root and route_path.startswith(root + "/"):
            route_path = route_path[len(root):]
        if route_path.startswith("/api/") and not self.authenticate(request):
            await JSONResponse({"status": "error", "error": "未认证：缺少或错误的 API Token"},
                               status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
            return
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > self.max_body:
            await self._too_large(scope, receive, send)
            return
        buffered: deque[Message] = deque()
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.max_body:
                await self._too_large(scope, receive, send)
                return
            buffered.append(message)
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            return buffered.popleft() if buffered else await receive()

        await self.app(scope, replay, send)

    async def _too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse({"status": "error", "error": f"请求体超过 {self.max_body} 字节上限"},
                           status_code=413)(scope, receive, send)
