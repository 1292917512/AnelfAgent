"""MCP OAuth 授权组件测试（无网络：存储/登记表/回调服务/提供者装配）。"""

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


class TestFileTokenStorage:
    async def test_tokens_roundtrip(self, oauth_env):
        from mcp.shared.auth import OAuthToken

        store = oauth.FileTokenStorage("srv")
        assert await store.get_tokens() is None
        await store.set_tokens(OAuthToken(access_token="tok123", token_type="Bearer"))
        # 新实例从磁盘读到同一凭据
        fresh = oauth.FileTokenStorage("srv")
        tokens = await fresh.get_tokens()
        assert tokens is not None and tokens.access_token == "tok123"

    async def test_client_info_roundtrip(self, oauth_env):
        from mcp.shared.auth import OAuthClientInformationFull

        store = oauth.FileTokenStorage("srv")
        await store.set_client_info(OAuthClientInformationFull(
            client_id="cid", redirect_uris=["http://127.0.0.1:1/callback"],
        ))
        info = await oauth.FileTokenStorage("srv").get_client_info()
        assert info is not None and info.client_id == "cid"

    def test_has_and_delete_credentials(self, oauth_env):
        import asyncio

        from mcp.shared.auth import OAuthToken

        store = oauth.FileTokenStorage("srv")
        asyncio.run(store.set_tokens(OAuthToken(access_token="t", token_type="Bearer")))
        assert oauth.has_credentials("srv") is True
        assert oauth.delete_credentials("srv") is True
        assert oauth.has_credentials("srv") is False
        assert oauth.delete_credentials("srv") is False

    def test_corrupt_file_tolerated(self, oauth_env):
        from pathlib import Path
        Path(ConfigPaths.MCP_OAUTH_TOKENS).write_text("{bad", encoding="utf-8")
        assert oauth.has_credentials("srv") is False


class TestPendingAuth:
    def test_register_and_clear(self, oauth_env):
        oauth._pending["srv"] = {"url": "http://x", "started_at": __import__("time").time()}
        assert oauth.pending_auth("srv")["srv"]["url"] == "http://x"
        oauth.clear_pending_auth("srv")
        assert oauth.pending_auth("srv") == {}

    def test_expired_entries_pruned(self, oauth_env):
        import time
        oauth._pending["old"] = {"url": "http://x", "started_at": time.time() - 7200}
        oauth._pending["new"] = {"url": "http://y", "started_at": time.time()}
        result = oauth.pending_auth()
        assert "old" not in result and "new" in result


class TestLoopbackCallback:
    async def test_callback_receives_code(self, oauth_env):
        import urllib.request

        server = oauth.LoopbackCallbackServer()
        server.start()
        try:
            url = f"http://127.0.0.1:{server.port}/callback?code=abc&state=xyz"
            # 回调线程内完成后再通知 wait_callback
            import asyncio
            task = asyncio.ensure_future(server.wait_callback(timeout=10))
            await asyncio.sleep(0.1)
            urllib.request.urlopen(url, timeout=5).read()
            code, state = await task
            assert code == "abc" and state == "xyz"
        finally:
            server.close()

    async def test_timeout_raises(self, oauth_env):
        server = oauth.LoopbackCallbackServer()
        server.start()
        with pytest.raises(TimeoutError):
            await server.wait_callback(timeout=0.2)


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
        """oauth 配置字段经 MCPServerConfig 解析保留。"""
        srv = MCPServerConfig(name="a", url="https://x", oauth={"client_id": "c1"})
        assert srv.oauth == {"client_id": "c1"}
