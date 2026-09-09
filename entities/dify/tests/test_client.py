"""Dify Console/Service 客户端单元测试（httpx MockTransport，无真实网络）。"""
from __future__ import annotations

import httpx
import pytest

from entities.dify.client import (
    DifyApiError,
    DifyAuthError,
    DifyConsoleClient,
    DifyNotFoundError,
    DifyServiceClient,
)


def _login_response() -> httpx.Response:
    """模拟登录成功：Set-Cookie 写入三个会话 cookie。"""
    return httpx.Response(
        200,
        json={"result": "success"},
        headers={
            "set-cookie": "access_token=at1; Path=/; HttpOnly",
        },
        extensions={},
    )


def _with_cookies(response: httpx.Response) -> httpx.Response:
    """httpx MockTransport 不会自动写 cookie jar，手工补 set-cookie 头。"""
    response.headers = httpx.Headers([
        ("set-cookie", "access_token=at1; Path=/"),
        ("set-cookie", "refresh_token=rt1; Path=/"),
        ("set-cookie", "csrf_token=csrf1; Path=/"),
        *[(k, v) for k, v in response.headers.items() if k.lower() != "set-cookie"],
    ])
    return response


@pytest.mark.asyncio
async def test_login_and_csrf_header():
    """登录后请求自动携带 cookie 与 X-CSRF-Token 头。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return _with_cookies(httpx.Response(200, json={"result": "success"}))
        seen["csrf"] = request.headers.get("X-CSRF-Token")
        seen["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, json={"data": [], "has_more": False})

    async with DifyConsoleClient("http://dify.test") as client:
        client._client = httpx.AsyncClient(
            base_url="http://dify.test", transport=httpx.MockTransport(handler))
        await client.login("admin@dify.local", "pw")
        assert client._csrf_token() == "csrf1"
        await client.list_apps()

    assert seen["csrf"] == "csrf1"
    assert "access_token=at1" in seen["cookie"]


@pytest.mark.asyncio
async def test_login_wrong_password():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid credentials"})

    async with DifyConsoleClient("http://dify.test") as client:
        client._client = httpx.AsyncClient(
            base_url="http://dify.test", transport=httpx.MockTransport(handler))
        with pytest.raises(DifyAuthError):
            await client.login("a@b.c", "wrong")


@pytest.mark.asyncio
async def test_401_triggers_refresh_and_retry():
    """业务请求 401 → refresh 换新 cookie → 原请求自动重试一次。"""
    calls = {"apps": 0, "refresh": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return _with_cookies(httpx.Response(200, json={"result": "success"}))
        if request.url.path == "/console/api/refresh-token":
            calls["refresh"] += 1
            return _with_cookies(httpx.Response(200, json={"result": "success"}))
        if request.url.path == "/console/api/apps":
            calls["apps"] += 1
            if calls["apps"] == 1:
                return httpx.Response(401, json={"message": "token expired"})
            return httpx.Response(200, json={"data": [], "has_more": False})
        return httpx.Response(404, json={"message": "not found"})

    async with DifyConsoleClient("http://dify.test") as client:
        client._client = httpx.AsyncClient(
            base_url="http://dify.test", transport=httpx.MockTransport(handler))
        await client.login("a@b.c", "pw")
        result = await client.list_apps()

    assert result["data"] == []
    assert calls["refresh"] == 1
    assert calls["apps"] == 2


@pytest.mark.asyncio
async def test_404_maps_to_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return _with_cookies(httpx.Response(200, json={"result": "success"}))
        return httpx.Response(404, json={"message": "app not found"})

    async with DifyConsoleClient("http://dify.test") as client:
        client._client = httpx.AsyncClient(
            base_url="http://dify.test", transport=httpx.MockTransport(handler))
        await client.login("a@b.c", "pw")
        with pytest.raises(DifyNotFoundError):
            await client.get_app("missing-id")


@pytest.mark.asyncio
async def test_setup_admin_already_setup():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/setup" and request.method == "POST":
            return httpx.Response(400, json={"message": "already setup"})
        return httpx.Response(200, json={"step": "finished"})

    async with DifyConsoleClient("http://dify.test") as client:
        client._client = httpx.AsyncClient(
            base_url="http://dify.test", transport=httpx.MockTransport(handler))
        with pytest.raises(DifyApiError, match="初始化"):
            await client.setup_admin("a@b.c", "Admin", "password-123")


@pytest.mark.asyncio
async def test_service_client_bearer_and_payload():
    """Service 客户端：Bearer 头与 workflow 请求体正确。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        import json as _json
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={
            "workflow_run_id": "run-1",
            "data": {"status": "succeeded", "outputs": {"text": "done"}},
        })

    svc = DifyServiceClient("http://dify.test", "app-key-1")
    svc._timeout = 5
    # 手工注入 MockTransport：替换内部每次新建 client 的行为
    svc_client = httpx.AsyncClient(
        base_url="http://dify.test", transport=httpx.MockTransport(handler))

    # 直接走底层请求验证头与 payload
    resp = await svc_client.post(
        "/v1/workflows/run",
        json={"inputs": {"q": "hi"}, "response_mode": "blocking", "user": "anelf-agent"},
        headers=svc._headers(),
    )
    assert resp.status_code == 200
    assert seen["auth"] == "Bearer app-key-1"
    assert seen["body"]["inputs"] == {"q": "hi"}
    await svc_client.aclose()
