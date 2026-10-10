"""GitHub App 鉴权测试(内存 RSA 密钥 + MockTransport,不触网)。

覆盖:JWT 载荷与 RS256 可验 / installation id 发现 / token 缓存与到期换新。
"""

from __future__ import annotations

import time
from typing import Any, List

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from channels.github.app_auth import GitHubAppAuth


def _gen_key() -> tuple[str, Any]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return pem, private.public_key()


def _auth(handler: Any, pem: str) -> GitHubAppAuth:
    return GitHubAppAuth(
        app_id="12345", private_key=pem,
        transport=httpx.MockTransport(handler),
    )


class TestJwt:
    def test_jwt_claims_and_rs256_signature(self) -> None:
        pem, public_key = _gen_key()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        auth = _auth(handler, pem)
        token = auth._jwt()
        decoded = pyjwt.decode(token, public_key, algorithms=["RS256"])
        assert decoded["iss"] == "12345"
        assert 0 < decoded["exp"] - decoded["iat"] <= 600 + 60  # 9min 有效期 + 1min 漂移
        # 缓存:8 分钟内不再签发(同一对象)
        assert auth._jwt() == token

    def test_empty_credentials_rejected(self) -> None:
        with pytest.raises(ValueError):
            GitHubAppAuth(app_id="", private_key="x")
        with pytest.raises(ValueError):
            GitHubAppAuth(app_id="1", private_key="")


class TestInstallationToken:
    async def test_discovery_and_token_flow(self) -> None:
        pem, _ = _gen_key()
        calls: List[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            assert request.headers["authorization"].startswith("Bearer ")
            if request.url.path == "/app/installations":
                return httpx.Response(200, json=[{"id": 777}])
            if request.url.path == "/app/installations/777/access_tokens":
                return httpx.Response(201, json={
                    "token": "inst-tok-1",
                    "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime(time.time() + 3600)),
                })
            return httpx.Response(404)

        auth = _auth(handler, pem)
        token = await auth.installation_token()
        assert token == "inst-tok-1"
        # 缓存命中:不再发请求
        assert await auth.installation_token() == "inst-tok-1"
        assert calls == ["GET /app/installations",
                         "POST /app/installations/777/access_tokens"]
        await auth.close()

    async def test_token_refresh_after_expiry(self) -> None:
        pem, _ = _gen_key()
        token_seq = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/app/installations":
                return httpx.Response(200, json=[{"id": 1}])
            token_seq["n"] += 1
            return httpx.Response(201, json={
                "token": f"tok-{token_seq['n']}",
                "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime(time.time() + 3600)),
            })

        auth = _auth(handler, pem)
        assert await auth.installation_token() == "tok-1"
        auth._token_expires_at = time.time() - 1  # 强制过期
        assert await auth.installation_token() == "tok-2"
        await auth.close()

    async def test_no_installation_raises(self) -> None:
        pem, _ = _gen_key()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        auth = _auth(handler, pem)
        with pytest.raises(RuntimeError, match="尚未安装"):
            await auth.installation_token()
        await auth.close()

    async def test_api_error_raises(self) -> None:
        pem, _ = _gen_key()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "bad jwt"})

        auth = _auth(handler, pem)
        with pytest.raises(RuntimeError, match="401"):
            await auth.installation_token()
        await auth.close()
