"""GitHub App SDK 认证、安装隔离和权限范围测试。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from channels.github.app_auth import GitHubAppAuth
from channels.github.client import GitHubClient


def _gen_key() -> tuple[str, Any]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return pem, private.public_key()


async def test_sdk_app_and_installation_auth_are_cached_and_scoped() -> None:
    pem, public = _gen_key()
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/installation"):
            decoded = jwt.decode(request.headers["authorization"].split(" ")[1], public, algorithms=["RS256"])
            assert decoded["iss"] == "123"
            return httpx.Response(200, json={"id": 42})
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token", "expires_at": "2099-01-01T00:00:00Z"})
        assert request.headers["authorization"].endswith("installation-token")
        return httpx.Response(200, json={"ok": True})

    auth = GitHubAppAuth(app_id="123", private_key=pem, transport=httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for _ in range(2):
            flow = await auth.auth_for(repo="o/r", permissions={"contents": "read"}, repositories=["r"])
            await client.get("https://api.github.com/repos/o/r", auth=flow)
    assert sum(path.endswith("/installation") for _, path, _ in calls) == 1
    tokens = [body for _, path, body in calls if path.endswith("/access_tokens")]
    assert tokens == [{"repositories": ["r"], "permissions": {"contents": "read"}}]
    await auth.close()


async def test_multiple_installations_require_repository_or_explicit_id() -> None:
    pem, _ = _gen_key()
    auth = GitHubAppAuth(app_id="1", private_key=pem,
                        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[{"id": 1}, {"id": 2}])))
    with pytest.raises(ValueError, match="多个安装"):
        await auth.installation_for()
    await auth.auth_for(installation_id=2)
    await auth.close()


async def test_client_selects_repository_installation_without_first_installation_fallback() -> None:
    pem, _ = _gen_key()
    requested: list[str] = []
    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path.endswith("/installation"):
            return httpx.Response(200, json={"id": 9 if "/one/" in request.url.path else 10})
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": request.url.path.split("/")[-2], "expires_at": "2099-01-01T00:00:00Z"})
        expected = "9" if "/one/" in request.url.path else "10"
        assert request.headers["authorization"].endswith(expected)
        return httpx.Response(200, json=[])
    transport = httpx.MockTransport(handler)
    auth = GitHubAppAuth(app_id="1", private_key=pem, transport=transport)
    client = GitHubClient(auth_mode="app", app_auth=auth, transport=transport)
    await client.get("/repos/one/repo/events")
    await client.get("/repos/two/repo/events")
    assert "/app/installations" not in requested
    await client.close()
    await auth.close()


def test_empty_credentials_rejected() -> None:
    with pytest.raises(ValueError):
        GitHubAppAuth(app_id="", private_key="x")
    with pytest.raises(ValueError):
        GitHubAppAuth(app_id="1", private_key="")
