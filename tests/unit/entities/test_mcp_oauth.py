"""MCP OAuth 组件测试（无真实网络：存储/登记表/回环回调/发现/刷新/提供者流）。"""

import asyncio
import json
import time
import types
import urllib.request
from pathlib import Path

import httpx
import pytest

from core.path import ConfigPaths
from entities.mcp import oauth
from entities.mcp.config import MCPServerConfig


@pytest.fixture()
def oauth_env(tmp_path, monkeypatch):
    """隔离 token 存储路径与待授权登记表。"""
    monkeypatch.setattr(ConfigPaths, "MCP_OAUTH_TOKENS", str(tmp_path / "mcp_oauth.json"))
    monkeypatch.setattr(oauth, "_pending", {})
    return tmp_path


def _entry_with_tokens(access="tok", refresh="ref", expires_in=None, **extra):
    tokens = {"access_token": access, "token_type": "Bearer"}
    if refresh:
        tokens["refresh_token"] = refresh
    if expires_in is not None:
        tokens["expires_in"] = expires_in
    return {"tokens": tokens, **extra}


# ------------------------------------------------------------------
# 凭据存储
# ------------------------------------------------------------------

class TestCredentialStore:
    def test_entry_roundtrip(self, oauth_env):
        from mcp.shared.auth import OAuthToken

        oauth._save_tokens(
            "srv", OAuthToken(access_token="a1", token_type="Bearer", expires_in=3600),
            client_info={"client_id": "cid"},
            server_meta={"issuer": "https://as", "token_endpoint": "https://as/token"},
        )
        entry = oauth._load_entry("srv")
        assert entry["client_info"] == {"client_id": "cid"}
        assert entry["server_meta"]["issuer"] == "https://as"
        assert isinstance(entry["expires_at"], float)
        assert oauth.credential_snapshot("srv")["authorized"] is True
        snapshot = oauth.credential_snapshot("srv")
        assert snapshot["authorized"] is True
        assert snapshot["has_refresh_token"] is False
        assert snapshot["static_client"] is True

    def test_save_preserves_existing_meta(self, oauth_env):
        from mcp.shared.auth import OAuthToken

        meta = {"issuer": "https://as", "token_endpoint": "https://as/token"}
        oauth._save_tokens("srv", OAuthToken(access_token="a", token_type="Bearer"),
                           server_meta=meta)
        oauth._save_tokens("srv", OAuthToken(access_token="b", token_type="Bearer"))
        assert oauth._load_entry("srv")["server_meta"] == meta

    def test_clear_tokens_variants(self, oauth_env):
        from mcp.shared.auth import OAuthToken

        oauth._save_tokens("srv", OAuthToken(access_token="a", token_type="Bearer"),
                           client_info={"client_id": "cid"}, server_meta={"issuer": "https://as"})
        oauth._clear_tokens("srv", keep_client=True)
        entry = oauth._load_entry("srv")
        assert "tokens" not in entry and entry.get("client_info") == {"client_id": "cid"}
        oauth._clear_tokens("srv", keep_client=False)
        # server_meta 是发现缓存而非凭据，保留以免去下次授权的发现往返
        after = oauth._load_entry("srv")
        assert "tokens" not in after and "client_info" not in after
        assert after.get("server_meta") == {"issuer": "https://as"}

    def test_has_and_delete_credentials(self, oauth_env):
        from mcp.shared.auth import OAuthToken

        oauth._save_tokens("srv", OAuthToken(access_token="t", token_type="Bearer"))
        assert oauth.credential_snapshot("srv")["authorized"] is True
        assert oauth.delete_credentials("srv") is True
        assert oauth.credential_snapshot("srv")["authorized"] is False
        assert oauth.delete_credentials("srv") is False

    def test_corrupt_file_tolerated(self, oauth_env):
        Path(ConfigPaths.MCP_OAUTH_TOKENS).write_text("{bad", encoding="utf-8")
        assert oauth.credential_snapshot("srv")["authorized"] is False
        assert oauth._load_entry("srv") == {}


# ------------------------------------------------------------------
# 待授权登记
# ------------------------------------------------------------------

class TestPendingAuth:
    def test_register_and_clear(self, oauth_env):
        oauth._register_pending("srv", "http://x")
        assert oauth.pending_auth("srv")["srv"]["url"] == "http://x"
        oauth.clear_pending_auth("srv")
        assert oauth.pending_auth("srv") == {}

    def test_expired_entries_pruned(self, oauth_env):
        oauth._pending["old"] = {"url": "http://x", "started_at": time.time() - 7200}
        oauth._pending["new"] = {"url": "http://y", "started_at": time.time()}
        result = oauth.pending_auth()
        assert "old" not in result and "new" in result


# ------------------------------------------------------------------
# 回环回调服务
# ------------------------------------------------------------------

class TestLoopbackCallback:
    async def test_callback_receives_code(self, oauth_env):
        server = oauth.LoopbackCallbackServer()
        server.start()
        try:
            server.expect_state("xyz")
            url = f"http://127.0.0.1:{server.port}/callback?code=abc&state=xyz"
            task = asyncio.ensure_future(server.wait_callback(timeout=10))
            await asyncio.sleep(0.1)
            urllib.request.urlopen(url, timeout=5).read()
            assert await task == "abc"
        finally:
            server.close()

    async def test_state_mismatch_keeps_waiting(self, oauth_env):
        server = oauth.LoopbackCallbackServer()
        server.start()
        try:
            server.expect_state("good")
            task = asyncio.ensure_future(server.wait_callback(timeout=10))
            await asyncio.sleep(0.1)
            # 陌生 state：回 400，等待继续
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{server.port}/callback?code=bad&state=other", timeout=5)
            assert err.value.code == 400
            assert not task.done()
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.port}/callback?code=ok&state=good", timeout=5).read()
            assert await task == "ok"
        finally:
            server.close()

    async def test_error_param_rejects(self, oauth_env):
        server = oauth.LoopbackCallbackServer()
        server.start()
        try:
            task = asyncio.ensure_future(server.wait_callback(timeout=10))
            await asyncio.sleep(0.1)
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.port}/callback?error=access_denied", timeout=5).read()
            with pytest.raises(PermissionError):
                await task
        finally:
            server.close()

    async def test_timeout_raises(self, oauth_env):
        server = oauth.LoopbackCallbackServer()
        server.start()
        try:
            with pytest.raises(TimeoutError):
                await server.wait_callback(timeout=0.2)
        finally:
            server.close()


# ------------------------------------------------------------------
# 提供者装配
# ------------------------------------------------------------------

class TestProviderAssembly:
    def test_stdio_not_eligible(self):
        srv = MCPServerConfig(name="a", command="npx", transport="stdio")
        assert oauth.oauth_eligible(srv) is False
        assert oauth.make_oauth_provider(srv) is None

    def test_http_eligible_builds_provider(self, oauth_env):
        srv = MCPServerConfig(name="a", url="https://mcp.example.com/mcp",
                              transport="streamable_http")
        provider = oauth.make_oauth_provider(srv)
        assert provider is not None

    def test_oauth_config_field_roundtrip(self):
        srv = MCPServerConfig(name="a", url="https://x", oauth={"client_id": "c1"})
        assert srv.oauth == {"client_id": "c1"}


# ------------------------------------------------------------------
# httpx 假客户端（discovery / refresh / 事务共用）
# ------------------------------------------------------------------

def install_mock_http(monkeypatch, handler):
    """把 oauth 模块内的 httpx.AsyncClient 替换为 MockTransport 假客户端。"""
    real = httpx

    class _Factory:
        def __init__(self, **kwargs):
            self._client = real.AsyncClient(
                transport=real.MockTransport(handler), timeout=kwargs.get("timeout", 10),
                follow_redirects=kwargs.get("follow_redirects", False),
            )

        async def __aenter__(self):
            return self._client

        async def __aexit__(self, *exc):
            await self._client.aclose()

        def __getattr__(self, name):
            return getattr(self._client, name)

    fake = types.SimpleNamespace(AsyncClient=_Factory, HTTPError=real.HTTPError)
    monkeypatch.setattr(oauth, "httpx", fake)


def _json_response(status, payload):
    return httpx.Response(status, json=payload)


# ------------------------------------------------------------------
# 发现（RFC 9728 / 8414）
# ------------------------------------------------------------------

class TestDiscovery:
    async def test_prm_and_as_metadata(self, oauth_env, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/.well-known/oauth-protected-resource/mcp":
                return _json_response(200, {
                    "resource": "https://mcp.example.com/mcp",
                    "authorization_servers": ["https://as.example.com"],
                })
            if path == "/.well-known/oauth-authorization-server":
                return _json_response(200, {
                    "issuer": "https://as.example.com",
                    "authorization_endpoint": "https://as.example.com/authorize",
                    "token_endpoint": "https://as.example.com/token",
                    "registration_endpoint": "https://as.example.com/register",
                })
            return _json_response(404, {})

        install_mock_http(monkeypatch, handler)
        meta = await oauth._discover_server_meta("https://mcp.example.com/mcp")
        assert meta["issuer"] == "https://as.example.com"
        assert meta["token_endpoint"] == "https://as.example.com/token"
        assert meta["resource"] == "https://mcp.example.com/mcp"

    async def test_no_oauth_declared_returns_none(self, oauth_env, monkeypatch):
        """PRM 与 AS 元数据全 404 → 未声明 OAuth（None），不发明端点。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(404, {})

        install_mock_http(monkeypatch, handler)
        assert await oauth._discover_server_meta("https://mcp.example.com/mcp") is None

    async def test_gateway_garbage_json_returns_none(self, oauth_env, monkeypatch):
        """网关型服务器（如 amap）对一切路径回 200+业务错误 JSON：候选全无效
        → 未声明 OAuth，绝不落到发明 /register 的路径（回归实网故障）。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"status": "0", "info": "INVALID_USER_KEY",
                                        "infocode": "10001"})

        install_mock_http(monkeypatch, handler)
        assert await oauth._discover_server_meta("https://mcp.amap.com/mcp") is None

    async def test_gateway_html_returns_none(self, oauth_env, monkeypatch):
        """SPA 型服务器对一切路径回 200+HTML：同样按候选不可用处理。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>index</html>")

        install_mock_http(monkeypatch, handler)
        assert await oauth._discover_server_meta("https://mcp.example.com/mcp") is None

    async def test_prm_without_as_doc_uses_defaults(self, oauth_env, monkeypatch):
        """PRM 找到而 AS 元数据文档缺失：按 PRM 指定的 AS 推导缺省端点。"""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/oauth-protected-resource/mcp":
                return _json_response(200, {
                    "resource": "https://mcp.example.com/mcp",
                    "authorization_servers": ["https://as.example.com"],
                })
            return _json_response(404, {})

        install_mock_http(monkeypatch, handler)
        meta = await oauth._discover_server_meta("https://mcp.example.com/mcp")
        assert meta["issuer"] == "https://as.example.com"
        assert meta["authorization_endpoint"] == "https://as.example.com/authorize"
        assert meta["token_endpoint"] == "https://as.example.com/token"
        assert meta["registration_endpoint"] is None  # 无文档不发明注册端点

    async def test_prepare_rejects_non_oauth_server(self, oauth_env, monkeypatch):
        """非 OAuth 服务器发起授权事务：立即报可行动错误（不发明端点）。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"status": "0", "info": "INVALID_USER_KEY"})

        install_mock_http(monkeypatch, handler)
        monkeypatch.setattr(oauth, "webbrowser", types.SimpleNamespace(open=lambda url: None))
        session = oauth.AuthorizationSession("srv", "https://mcp.amap.com/mcp", {})
        try:
            with pytest.raises(oauth.OAuthConfigurationError) as err:
                await session.prepare()
            assert "未声明 OAuth 支持" in str(err.value)
            assert "静态凭据" in str(err.value)
        finally:
            await session.aclose()

    async def test_dcr_missing_client_id_error_carries_body(self, oauth_env, monkeypatch):
        meta = {
            "issuer": "https://as.example.com",
            "authorization_endpoint": "https://as.example.com/authorize",
            "token_endpoint": "https://as.example.com/token",
            "registration_endpoint": "https://as.example.com/register",
        }
        oauth._write_entry("srv", {"server_meta": meta})

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"status": "0", "info": "INVALID_USER_KEY"})

        install_mock_http(monkeypatch, handler)
        monkeypatch.setattr(oauth, "webbrowser", types.SimpleNamespace(open=lambda url: None))
        session = oauth.AuthorizationSession("srv", "https://mcp.example.com/mcp", {})
        try:
            with pytest.raises(oauth.OAuthConfigurationError) as err:
                await session.prepare()
            assert "INVALID_USER_KEY" in str(err.value)
            assert "oauth.client_id" in str(err.value)
        finally:
            await session.aclose()

    async def test_issuer_mismatch_rejected(self, oauth_env, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/oauth-authorization-server":
                return _json_response(200, {
                    "issuer": "https://evil.example.com",
                    "authorization_endpoint": "https://evil.example.com/authorize",
                    "token_endpoint": "https://evil.example.com/token",
                })
            return _json_response(404, {})

        install_mock_http(monkeypatch, handler)
        with pytest.raises(oauth.OAuthConfigurationError):
            await oauth._discover_server_meta("https://mcp.example.com/mcp")

    async def test_unsupported_flow_rejected(self, oauth_env, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/oauth-authorization-server":
                return _json_response(200, {
                    "issuer": "https://mcp.example.com",
                    "authorization_endpoint": "https://mcp.example.com/authorize",
                    "token_endpoint": "https://mcp.example.com/token",
                    "response_types_supported": ["token"],
                    "code_challenge_methods_supported": ["plain"],
                })
            return _json_response(404, {})

        install_mock_http(monkeypatch, handler)
        with pytest.raises(oauth.OAuthConfigurationError):
            await oauth._discover_server_meta("https://mcp.example.com/mcp")


# ------------------------------------------------------------------
# 刷新分类（invalid_grant / invalid_client / 临时故障 / 成功）
# ------------------------------------------------------------------

def _provider(name="srv", url="https://mcp.example.com/mcp"):
    return oauth.McpOAuthProvider(name, url, {})


class TestRefresh:
    async def test_success_saves_tokens(self, oauth_env, monkeypatch):
        entry = _entry_with_tokens(
            expires_in=None,
            server_meta={"issuer": "https://as", "token_endpoint": "https://as/token",
                         "resource": "https://mcp.example.com"},
            client_info={"client_id": "cid"},
        )
        # 直接落一个带 expires_at 的临期条目
        entry["expires_at"] = time.time() - 10
        oauth._write_entry("srv", entry)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/token"
            form = request.read().decode()
            assert "grant_type=refresh_token" in form
            return _json_response(200, {"access_token": "fresh", "token_type": "Bearer",
                                        "expires_in": 3600})

        install_mock_http(monkeypatch, handler)
        provider = _provider()
        tokens = oauth._entry_tokens(oauth._load_entry("srv"))
        fresh = await provider._refresh(oauth._load_entry("srv"), tokens)
        assert fresh.access_token == "fresh"
        saved = oauth._entry_tokens(oauth._load_entry("srv"))
        assert saved.access_token == "fresh"

    async def test_missing_rotation_keeps_old_refresh(self, oauth_env, monkeypatch):
        entry = _entry_with_tokens(
            refresh="old-ref", expires_in=None,
            server_meta={"issuer": "https://as", "token_endpoint": "https://as/token"},
        )
        entry["expires_at"] = time.time() - 10
        oauth._write_entry("srv", entry)

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"access_token": "fresh", "token_type": "Bearer"})

        install_mock_http(monkeypatch, handler)
        provider = _provider()
        tokens = oauth._entry_tokens(oauth._load_entry("srv"))
        fresh = await provider._refresh(oauth._load_entry("srv"), tokens)
        assert fresh.refresh_token == "old-ref"

    async def test_invalid_grant_clears_tokens_keeps_client(self, oauth_env, monkeypatch):
        entry = _entry_with_tokens(
            server_meta={"issuer": "https://as", "token_endpoint": "https://as/token"},
            client_info={"client_id": "cid"},
        )
        entry["expires_at"] = time.time() - 10
        oauth._write_entry("srv", entry)

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(400, {"error": "invalid_grant"})

        install_mock_http(monkeypatch, handler)
        provider = _provider()
        tokens = oauth._entry_tokens(oauth._load_entry("srv"))
        assert await provider._refresh(oauth._load_entry("srv"), tokens) is None
        after = oauth._load_entry("srv")
        assert "tokens" not in after and after.get("client_info") == {"client_id": "cid"}

    async def test_static_invalid_client_is_configuration_error(self, oauth_env, monkeypatch):
        entry = _entry_with_tokens(
            server_meta={"issuer": "https://as", "token_endpoint": "https://as/token"},
            client_info={"client_id": "cid"},
        )
        entry["expires_at"] = time.time() - 10
        oauth._write_entry("srv", entry)

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(401, {"error": "invalid_client"})

        install_mock_http(monkeypatch, handler)
        provider = oauth.McpOAuthProvider("srv", "https://mcp.example.com/mcp",
                                          {"client_id": "cid"})
        tokens = oauth._entry_tokens(oauth._load_entry("srv"))
        with pytest.raises(oauth.OAuthConfigurationError):
            await provider._refresh(oauth._load_entry("srv"), tokens)

    async def test_server_error_is_temporary(self, oauth_env, monkeypatch):
        entry = _entry_with_tokens(
            server_meta={"issuer": "https://as", "token_endpoint": "https://as/token"},
        )
        entry["expires_at"] = time.time() - 10
        oauth._write_entry("srv", entry)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="unavailable")

        install_mock_http(monkeypatch, handler)
        provider = _provider()
        tokens = oauth._entry_tokens(oauth._load_entry("srv"))
        with pytest.raises(oauth.OAuthTemporaryError):
            await provider._refresh(oauth._load_entry("srv"), tokens)
        # 临时故障不清凭据
        assert oauth.credential_snapshot("srv")["authorized"] is True


# ------------------------------------------------------------------
# 提供者 auth flow（驱动 async_auth_flow，喂假响应）
# ------------------------------------------------------------------

async def _drive(provider_flow, responses):
    """驱动 async_auth_flow：逐个喂响应，返回每次 yield 时的 Authorization 头。

    401 重试时 httpx 复用同一个 Request 对象（原地改头再 yield），事后读
    headers 拿不到首轮状态，必须在 yield 时刻采样。
    """
    auths: list = []
    iterator = provider_flow.__aiter__()
    request = await iterator.__anext__()
    auths.append(request.headers.get("Authorization", ""))
    for response in responses:
        try:
            request = await iterator.asend(response)
        except StopAsyncIteration:
            return auths
        auths.append(request.headers.get("Authorization", ""))
    return auths


def _request():
    return httpx.Request("POST", "https://mcp.example.com/mcp")


class TestFirstYield:
    async def test_no_credentials_first_request_is_bare(self, oauth_env, monkeypatch):
        """无凭据（amap 首连必然如此）：首轮发裸请求，不在请求发出前拉起
        授权事务——回归实网「服务器未声明 OAuth 支持」误报。"""
        calls = []

        async def fake_auth(extra_scopes=""):
            calls.append("auth")

        provider = _provider()
        monkeypatch.setattr(provider, "_run_authorization", fake_auth)
        auths = await _drive(
            provider.async_auth_flow(_request()),
            [httpx.Response(200)],
        )
        assert calls == []
        assert auths == [""]  # 裸发，无 Bearer 头

    async def test_bare_request_then_bearer_challenge_authorizes(self, oauth_env, monkeypatch):
        """无凭据 + 服务器 401 Bearer 挑战：第二轮才进授权流。"""
        tokens_returned = []

        async def fake_auth(extra_scopes=""):
            tokens_returned.append("auth")
            from mcp.shared.auth import OAuthToken
            return OAuthToken(access_token="fresh", token_type="Bearer")

        provider = _provider()
        monkeypatch.setattr(provider, "_run_authorization", fake_auth)
        challenge = httpx.Response(401, headers={"WWW-Authenticate": "Bearer"})
        auths = await _drive(
            provider.async_auth_flow(_request()),
            [challenge, httpx.Response(200)],
        )
        assert tokens_returned == ["auth"]
        assert auths == ["", "Bearer fresh"]


class TestBearerGate:
    async def test_401_without_bearer_challenge_does_nothing(self, oauth_env, monkeypatch):
        """静态凭据服务器（key 在 URL/headers，如 amap）：401 无 Bearer 挑战时
        不刷新、不授权，重试原样发出（失败由 bridge 重连处理）——回归实网故障。"""
        calls = []

        async def fake_refresh(entry, tokens):
            calls.append("refresh")

        async def fake_auth(extra_scopes=""):
            calls.append("auth")

        # 预置有效凭据：排除首轮无凭据进授权路径的干扰，专注验证 401 门控
        entry = _entry_with_tokens(access="live", refresh="ref")
        entry["expires_at"] = time.time() + 3600
        oauth._write_entry("srv", entry)
        provider = _provider()
        monkeypatch.setattr(provider, "_refresh", fake_refresh)
        monkeypatch.setattr(provider, "_run_authorization", fake_auth)
        auths = await _drive(
            provider.async_auth_flow(_request()),
            [httpx.Response(401)],
        )
        assert calls == []
        # 无 Bearer 挑战：只发一轮，不刷新不重试（失败由 bridge 重连处理）
        assert auths == ["Bearer live"]

    async def test_prepare_rejects_missing_registration_endpoint(self, oauth_env, monkeypatch):
        """PRM 存在但 AS 元数据缺失：不发明注册端点，报 DCR 不支持。"""
        async def fake_discover(url):
            return {
                "issuer": "https://as.example.com",
                "authorization_endpoint": "https://as.example.com/authorize",
                "token_endpoint": "https://as.example.com/token",
                "registration_endpoint": None,
                "resource": "https://mcp.example.com",
            }

        monkeypatch.setattr(oauth, "_discover_server_meta", fake_discover)
        monkeypatch.setattr(oauth, "webbrowser", types.SimpleNamespace(open=lambda url: None))
        session = oauth.AuthorizationSession("srv", "https://mcp.example.com/mcp", {})
        try:
            with pytest.raises(oauth.OAuthConfigurationError) as err:
                await session.prepare()
            assert "不支持 DCR" in str(err.value) or "未声明动态注册端点" in str(err.value)
        finally:
            await session.aclose()


class TestProviderFlow:
    async def test_valid_token_single_round(self, oauth_env):
        oauth._write_entry("srv", _entry_with_tokens(access="valid", refresh=None))
        entry = oauth._load_entry("srv")
        entry["expires_at"] = time.time() + 3600
        oauth._write_entry("srv", entry)
        provider = _provider()
        auths = await _drive(provider.async_auth_flow(_request()), [httpx.Response(200)])
        assert auths == ["Bearer valid"]

    async def test_401_refresh_first(self, oauth_env, monkeypatch):
        entry = _entry_with_tokens(
            access="stale",
            server_meta={"issuer": "https://as", "token_endpoint": "https://as/token"},
        )
        entry["expires_at"] = time.time() + 3600  # 看似有效但被 401 拒绝
        oauth._write_entry("srv", entry)

        calls = []

        async def fake_refresh(entry, tokens):
            calls.append("refresh")
            from mcp.shared.auth import OAuthToken
            return OAuthToken(access_token="fresh", token_type="Bearer")

        provider = _provider()
        monkeypatch.setattr(provider, "_refresh", fake_refresh)
        challenge = httpx.Response(401, headers={"WWW-Authenticate": "Bearer"})
        auths = await _drive(
            provider.async_auth_flow(_request()),
            [challenge, httpx.Response(200)],
        )
        assert calls == ["refresh"]
        assert auths == ["Bearer stale", "Bearer fresh"]

    async def test_403_step_up_reauthorizes_with_union_scope(self, oauth_env, monkeypatch):
        oauth._write_entry("srv", _entry_with_tokens(access="limited"))
        scopes_seen = []

        async def fake_run_authorization(extra_scopes=""):
            scopes_seen.append(extra_scopes)
            from mcp.shared.auth import OAuthToken
            return OAuthToken(access_token="elevated", token_type="Bearer", scope="read write")

        provider = _provider()
        monkeypatch.setattr(provider, "_run_authorization", fake_run_authorization)
        challenge = httpx.Response(
            403, headers={"WWW-Authenticate":
                          'Bearer error="insufficient_scope", scope="read write"'},
        )
        auths = await _drive(
            provider.async_auth_flow(_request()),
            [challenge, httpx.Response(200)],
        )
        assert scopes_seen == ["read write"]
        assert auths == ["Bearer limited", "Bearer elevated"]

    async def test_refresh_single_flight(self, oauth_env, monkeypatch):
        entry = _entry_with_tokens(
            server_meta={"issuer": "https://as", "token_endpoint": "https://as/token"},
        )
        entry["expires_at"] = time.time() - 10
        oauth._write_entry("srv", entry)

        counter = {"n": 0}

        async def fake_refresh(entry, tokens):
            counter["n"] += 1
            await asyncio.sleep(0.05)
            from mcp.shared.auth import OAuthToken
            fresh = OAuthToken(access_token="shared", token_type="Bearer")
            oauth._save_tokens("srv", fresh)  # 与真实刷新同契约：结果落盘
            return fresh

        provider = _provider()
        monkeypatch.setattr(provider, "_refresh", fake_refresh)
        results = await asyncio.gather(provider._access_token(), provider._access_token())
        assert counter["n"] == 1
        assert results == ["shared", "shared"]

    async def test_temporary_refresh_failsoft_keeps_current(self, oauth_env, monkeypatch):
        entry = _entry_with_tokens(
            access="current",
            server_meta={"issuer": "https://as", "token_endpoint": "https://as/token"},
        )
        entry["expires_at"] = time.time() - 10
        oauth._write_entry("srv", entry)

        async def fake_refresh(entry, tokens):
            raise oauth.OAuthTemporaryError("as down")

        provider = _provider()
        monkeypatch.setattr(provider, "_refresh", fake_refresh)
        # 临期触发刷新失败 → fail-soft 返回现值
        assert await provider._access_token() == "current"


# ------------------------------------------------------------------
# 授权事务（prepare / wait_and_exchange）
# ------------------------------------------------------------------

class TestAuthorizationSession:
    async def test_static_client_authorize_and_exchange(self, oauth_env, monkeypatch):
        meta = {
            "issuer": "https://as.example.com",
            "authorization_endpoint": "https://as.example.com/authorize",
            "token_endpoint": "https://as.example.com/token",
            "resource": "https://mcp.example.com",
        }
        oauth._write_entry("srv", {"server_meta": meta})

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/token"
            form = request.read().decode()
            assert "grant_type=authorization_code" in form
            assert "code_verifier=" in form
            return _json_response(200, {"access_token": "new", "token_type": "Bearer",
                                        "refresh_token": "r1", "expires_in": 3600})

        install_mock_http(monkeypatch, handler)
        monkeypatch.setattr(oauth, "webbrowser", types.SimpleNamespace(open=lambda url: None))

        session = oauth.AuthorizationSession(
            "srv", "https://mcp.example.com/mcp",
            {"client_id": "static-cid", "scopes": "read"})
        await session.prepare()
        assert "https://as.example.com/authorize?" in session.authorize_url
        assert "client_id=static-cid" in session.authorize_url
        assert "code_challenge_method=S256" in session.authorize_url
        assert oauth.pending_auth("srv")["srv"]["url"] == session.authorize_url

        # 模拟用户回调（正确 state）
        import urllib.parse
        parsed = urllib.parse.urlparse(session.authorize_url)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        callback_port = session._callback.port
        code_task = asyncio.ensure_future(session.wait_and_exchange())
        await asyncio.sleep(0.1)
        urllib.request.urlopen(
            f"http://127.0.0.1:{callback_port}/callback?code=the-code&state={params['state']}",
            timeout=5).read()
        tokens = await code_task
        assert tokens.access_token == "new"
        # 持久化 + 待授权清理
        saved = oauth._load_entry("srv")
        assert saved["tokens"]["access_token"] == "new"
        assert saved["client_info"]["client_id"] == "static-cid"
        assert oauth.pending_auth("srv") == {}

    async def test_dcr_registers_fresh_client(self, oauth_env, monkeypatch):
        meta = {
            "issuer": "https://as.example.com",
            "authorization_endpoint": "https://as.example.com/authorize",
            "token_endpoint": "https://as.example.com/token",
            "registration_endpoint": "https://as.example.com/register",
        }
        # 预置 server_meta：跳过发现，只验动态注册与 URL 构造
        oauth._write_entry("srv", {"server_meta": meta})
        registered = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/register":
                body = json.loads(request.read())
                registered.append(body)
                return _json_response(201, {"client_id": "dcr-1", "client_secret": "s3cret"})
            return _json_response(404, {})

        install_mock_http(monkeypatch, handler)
        monkeypatch.setattr(oauth, "webbrowser", types.SimpleNamespace(open=lambda url: None))

        session = oauth.AuthorizationSession("srv", "https://mcp.example.com/mcp", {})
        await session.prepare()
        assert registered and registered[0]["token_endpoint_auth_method"] == "none"
        assert session._client == {"client_id": "dcr-1", "client_secret": "s3cret"}
        assert "client_id=dcr-1" in session.authorize_url
        await session.aclose()

    async def test_close_clears_pending(self, oauth_env, monkeypatch):
        meta = {
            "issuer": "https://as.example.com",
            "authorization_endpoint": "https://as.example.com/authorize",
            "token_endpoint": "https://as.example.com/token",
        }
        oauth._write_entry("srv", {"server_meta": meta})
        install_mock_http(monkeypatch, lambda request: _json_response(404, {}))
        monkeypatch.setattr(oauth, "webbrowser", types.SimpleNamespace(open=lambda url: None))

        session = oauth.AuthorizationSession(
            "srv", "https://mcp.example.com/mcp", {"client_id": "cid"})
        await session.prepare()
        assert oauth.pending_auth("srv")
        await session.aclose()
        assert oauth.pending_auth("srv") == {}
