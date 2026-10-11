"""小度令牌续期工具测试：refresh/授权码双路径、回写双 MCP entry、缺凭据兜底。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from core.config import ConfigManager
from entities.smart_home import tools as tools_mod


class _FakeStore:
    """MCPServerStore 替身（记录 update 调用，不碰磁盘与桥）。"""

    def __init__(self) -> None:
        self.updates: List[tuple] = []

    def update_server_config(self, name: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        self.updates.append((name, patch))
        return {"before": {}, "after": patch}


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch):
    calls: List[Dict[str, str]] = []
    saved: List[tuple] = []
    store = _FakeStore()

    async def fake_baidu(params: Dict[str, str]) -> Dict[str, Any]:
        calls.append(params)
        if params.get("refresh_token") == "bad-refresh":
            return {"error": "invalid_grant",
                    "error_description": "refresh token 已失效"}
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 2592000,
        }

    monkeypatch.setattr(tools_mod, "_baidu_token_request", fake_baidu)
    monkeypatch.setattr(tools_mod, "_mcp_store", lambda: store)
    monkeypatch.setattr(tools_mod, "save_config_value",
                        lambda k, v: saved.append((k, v)))
    ConfigManager.set("smart_home_xiaodu_app_key", "app-key")
    ConfigManager.set("smart_home_xiaodu_secret_key", "secret-key")
    ConfigManager.set("smart_home_xiaodu_refresh_token", "old-refresh")
    return calls, saved, store


def _result(raw: str) -> Dict[str, Any]:
    return json.loads(raw)


class TestXiaoduRefreshToken:
    @pytest.mark.asyncio
    async def test_refresh_path_updates_servers_and_rotates(
            self, env) -> None:
        calls, saved, store = env
        out = _result(await tools_mod.xiaodu_refresh_token())
        assert out["success"] is True
        assert out["refresh_token_rotated"] is True
        assert calls[0]["grant_type"] == "refresh_token"
        assert calls[0]["refresh_token"] == "old-refresh"
        assert store.updates == [
            ("xiaodu-iot", {"env": {"ACCESS_TOKEN": "new-access"}}),
            ("xiaodu", {"headers": {"ACCESS_TOKEN": "new-access"}}),
        ]
        assert ("smart_home_xiaodu_refresh_token", "new-refresh") in saved
        assert any(k == "smart_home_xiaodu_token_expires_at" and v
                   for k, v in saved)
        # 密钥与令牌不进返回值
        assert "new-access" not in json.dumps(out)

    @pytest.mark.asyncio
    async def test_code_path(self, env) -> None:
        calls, _, _ = env
        out = _result(await tools_mod.xiaodu_refresh_token(code=" auth-code "))
        assert out["success"] is True
        assert calls[0]["grant_type"] == "authorization_code"
        assert calls[0]["code"] == "auth-code"

    @pytest.mark.asyncio
    async def test_missing_app_info(self, env,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
        ConfigManager.set("smart_home_xiaodu_app_key", "")
        out = _result(await tools_mod.xiaodu_refresh_token())
        assert "error" in out
        assert out["cause"] == "config"

    @pytest.mark.asyncio
    async def test_no_refresh_no_code_gives_auth_url(self, env) -> None:
        ConfigManager.set("smart_home_xiaodu_refresh_token", "")
        out = _result(await tools_mod.xiaodu_refresh_token())
        assert "error" in out
        assert "openapi.baidu.com/oauth/2.0/authorize" in out.get("hint", "")

    @pytest.mark.asyncio
    async def test_baidu_error_surfaces_hint(self, env) -> None:
        ConfigManager.set("smart_home_xiaodu_refresh_token", "bad-refresh")
        out = _result(await tools_mod.xiaodu_refresh_token())
        assert "error" in out
        assert out["cause"] == "permission"
        assert "refresh_token 失效" in out.get("hint", "")
