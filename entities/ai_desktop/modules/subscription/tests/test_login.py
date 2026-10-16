"""额度平台选择、授权复用和扫码会话生命周期。"""

import asyncio
import json
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, ConnectError, MockTransport, Response

from core.command import CommandResult
from core.config import ConfigManager
from entities.ai_desktop.modules.subscription import bailian
from entities.ai_desktop.modules.subscription import login as login_module
from entities.ai_desktop.modules.subscription import module as subscription_module
from entities.ai_desktop.modules.subscription.login import ConsoleLogin
from entities.ai_desktop.modules.subscription.module import SubscriptionModule
from entities.ai_desktop.modules.subscription.providers import ProviderSpec


async def test_platforms_are_opt_in_and_disable_immediately(monkeypatch):
    credential, fetch = Mock(return_value=("key", "source")), Mock(return_value={"windows": []})
    spec = ProviderSpec("test", "Test", credential, fetch)
    monkeypatch.setattr(subscription_module, "PROVIDERS", (spec,))
    module = SubscriptionModule()
    await module.refresh()
    assert module.detail()["providers"] == [] and module.render() is None
    credential.assert_not_called()
    fetch.assert_not_called()
    ConfigManager.set(module.full_config_key("provider_test_enabled"), True)
    await module.refresh()
    fetch.assert_called_once()
    assert module.detail()["providers"][0]["state"] == "ok"
    ConfigManager.set(module.full_config_key("provider_test_enabled"), False)
    assert module.detail()["providers"] == [] and module.render() is None
    await module.refresh()
    fetch.assert_called_once()


async def test_render_and_detail_do_not_resolve_credentials(monkeypatch):
    credential = Mock(return_value=("key", "source"))
    spec = ProviderSpec("test", "Test", credential, lambda _: {"windows": []})
    monkeypatch.setattr(subscription_module, "PROVIDERS", (spec,))
    module = SubscriptionModule()
    ConfigManager.set(module.full_config_key("provider_test_enabled"), True)
    assert module.detail()["providers"][0]["state"] == "pending"
    assert module.render() is None
    credential.assert_not_called()
    await module.refresh()
    credential.reset_mock()
    module._credentials_at = 0
    module.render()
    module.detail()
    credential.assert_not_called()


def test_missing_login_is_not_retried_until_credentials_change():
    revision = Mock(return_value="first")
    fetch = Mock(side_effect=bailian.ConsoleLoginRequired())
    spec = ProviderSpec("test", "Test", lambda: ("cli", "source"), fetch, "console", revision)
    module = SubscriptionModule()
    module._data["test"] = module._fetch_one(spec, "cli")
    assert module._fetch_one(spec, "cli")["needs_auth"]
    fetch.assert_called_once()
    revision.return_value = "second"
    fetch.side_effect = None
    fetch.return_value = {"windows": []}
    assert "needs_auth" not in module._fetch_one(spec, "cli")
    assert fetch.call_count == 2


def test_session_persistence_isolated_reused_and_secrets_not_in_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("ANELF_CONFIG_DIR", str(tmp_path))
    before = bailian.auth_revision()
    bailian.save_console_session({"access_token": "test-console-token", "api_key": "must-not-save",
                                 "console_site": "domestic", "console_switch_agent": "123"})
    target = bailian.console_config_dir() / "config.json"
    data = json.loads(target.read_text())
    assert data["access_token"] == "test-console-token" and "api_key" not in data
    assert data["console_switch_agent"] == 123
    assert bailian.auth_revision() != before
    assert "test-console-token" not in bailian.auth_revision()
    command = Mock(return_value=CommandResult(True, '{"per1MonthPercentage":0.2}', "", 0))
    monkeypatch.setattr(bailian, "run_command", command)
    assert bailian.fetch_usage("bl")["windows"][0]["remaining_percent"] == 80
    assert command.call_args.kwargs["env_vars"] == {"BAILIAN_CONFIG_DIR": str(target.parent)}
    assert "test-console-token" not in str(command.call_args)
    original = target.read_bytes()
    with pytest.raises(ValueError):
        bailian.save_console_session({"access_token": ""})
    assert target.read_bytes() == original


@pytest.fixture
def upstream(monkeypatch):
    state = {"status": "UNAUTHORIZED", "poll_failures": 0, "auth_status": 200, "requests": [], "clients": []}
    def handle(request):
        path = request.url.path
        state["requests"].append(path)
        if path == "/login/login.htm":
            return Response(200, text="window.loginContext = {CSRF_TOKEN: 'test-csrf'};",
                            headers={"set-cookie": "login_aliyunid_csrf=test-cookie; Path=/"})
        if path in {"/st/apply.htm", "/st/validate.htm"}:
            assert "login_aliyunid_csrf=test-cookie" in request.headers["cookie"]
            params = parse_qs(request.content.decode())
            assert params["_csrf_token_"] == ["test-csrf"]
            if path.endswith("apply.htm"):
                return Response(200, json={"success": True, "data": {"st": "test-ticket", "qrShortLink": True}})
            assert params["yst"] == ["test-ticket"]
            if state["poll_failures"]:
                state["poll_failures"] -= 1
                raise ConnectError("private network detail")
            return Response(200, json={"success": True, "data": {"status": state["status"]}})
        if path == "/login/st_login.htm":
            assert request.url.params["yst"] == "test-ticket"
            return Response(302, headers={"location": "https://bailian.console.aliyun.com/console-login",
                            "set-cookie": "console-session=test-session; Domain=.aliyun.com; Path=/"})
        if path == "/console-login":
            return Response(200, text="Console authorization")
        assert path == "/cli/authorize"
        assert request.method == "POST" and not request.content
        assert request.headers["origin"] == "https://bailian.console.aliyun.com"
        assert "console-session=test-session" in request.headers["cookie"]
        return Response(state["auth_status"], json={"data": {"access_token": "test-console-token"}})
    def client(**kwargs):
        instance = AsyncClient(transport=MockTransport(handle), **kwargs)
        state["clients"].append(instance)
        return instance
    monkeypatch.setattr(login_module, "async_network_client", client)
    monkeypatch.setattr(login_module, "cli_credential", lambda: ("bl", "CLI"))
    monkeypatch.setattr(login_module, "_POLL_INTERVAL", 0)
    return state


async def reach(service: ConsoleLogin, expected: str) -> dict:
    async with asyncio.timeout(1):
        while service.status(service._id)["state"] != expected:
            await asyncio.sleep(0)
    return service.status(service._id)


async def test_qr_login_cookie_exchange_confirmation_and_cleanup(upstream, tmp_path, monkeypatch):
    monkeypatch.setenv("ANELF_CONFIG_DIR", str(tmp_path))
    complete = AsyncMock()
    service = ConsoleLogin(complete)
    state = await service.start()
    await reach(service, "waiting")
    assert (await service.start())["id"] == state["id"]
    with pytest.raises(LookupError):
        await service.image("wrong-session")
    with pytest.raises(ValueError):
        await service.confirm(state["id"])
    image = await service.image(state["id"])
    assert image.startswith(b"\x89PNG") and image == await service.image(state["id"])
    upstream["status"] = "AUTHORIZING"
    await reach(service, "scanned")
    upstream["status"] = "AUTHORIZED"
    await reach(service, "confirming")
    assert "/cli/authorize" not in upstream["requests"]
    await service.confirm(state["id"])
    with pytest.raises(ValueError):
        await service.confirm(state["id"])
    await service._task
    assert service.status(state["id"])["state"] == "complete"
    assert "test-console-token" not in str(service.status(state["id"]))
    assert json.loads((bailian.console_config_dir() / "config.json").read_text())["access_token"] == "test-console-token"
    assert upstream["requests"].count("/st/apply.htm") == 1
    assert upstream["requests"].count("/cli/authorize") == 1
    assert all(c.is_closed for c in upstream["clients"])
    complete.assert_awaited_once()
    with pytest.raises(ValueError):
        await service.image(state["id"])
    await service.close()


@pytest.mark.parametrize("stop", ["cancel_before_start", "cancel", "timeout", "INVALID", "USER_IP_CHECK_FAILED", "UNKNOWN"])
async def test_terminal_states_do_not_authorize_and_release_resources(upstream, monkeypatch, stop):
    if stop == "timeout":
        monkeypatch.setattr(login_module, "_TTL", 0.03)
    complete = AsyncMock()
    service = ConsoleLogin(complete)
    state = await service.start()
    if stop != "cancel_before_start":
        await reach(service, "waiting")
    if stop in {"cancel", "cancel_before_start"}:
        await service.close(state["id"])
    else:
        if stop != "timeout":
            upstream["status"] = stop
        await service._task
    assert service.status(state["id"])["state"] == (
        "cancelled" if stop.startswith("cancel") else "expired" if stop in {"timeout", "INVALID"} else "error")
    complete.assert_not_awaited()
    assert "/cli/authorize" not in upstream["requests"]
    assert all(c.is_closed for c in upstream["clients"])
    assert not service._image


@pytest.mark.parametrize("failures", [1, 3])
async def test_poll_network_failure_is_bounded_and_never_leaks(upstream, failures):
    upstream["poll_failures"] = failures
    upstream["status"] = "AUTHORIZED"
    service = ConsoleLogin(AsyncMock())
    await service.start()
    state = await reach(service, "confirming" if failures == 1 else "error")
    assert "private" not in str(state)
    assert upstream["requests"].count("/st/validate.htm") == (2 if failures == 1 else 3)
    await service.close()


async def test_failed_authorization_preserves_existing_session_and_can_restart(upstream, tmp_path, monkeypatch):
    monkeypatch.setenv("ANELF_CONFIG_DIR", str(tmp_path))
    bailian.save_console_session({"access_token": "existing-test-token"})
    upstream["status"], upstream["auth_status"] = "SUCCESS", 401
    service = ConsoleLogin(AsyncMock())
    first = await service.start()
    await reach(service, "confirming")
    await service.confirm(first["id"])
    await service._task
    assert service.status(first["id"])["state"] == "error"
    assert json.loads((bailian.console_config_dir() / "config.json").read_text())["access_token"] == "existing-test-token"
    second = await service.start()
    assert second["id"] != first["id"]
    await service.close()


async def test_quota_failure_does_not_turn_saved_login_into_failed_login(upstream, tmp_path, monkeypatch):
    monkeypatch.setenv("ANELF_CONFIG_DIR", str(tmp_path))
    upstream["status"] = "AUTHORIZED"
    service = ConsoleLogin(AsyncMock(side_effect=ValueError("private quota detail")))
    state = await service.start()
    await reach(service, "confirming")
    await service.confirm(state["id"])
    await service._task
    assert service.status(state["id"])["state"] == "complete"
    assert service.status(state["id"])["error"] == "quota_refresh_failed"
    assert (bailian.console_config_dir() / "config.json").exists()


async def test_ai_configuration_controls_login_route_and_authorization(upstream, monkeypatch):
    from entities.ai_desktop import tools
    from entities.ai_desktop.modules.subscription import router

    service = ConsoleLogin(AsyncMock())
    monkeypatch.setattr(router, "get_console_login", lambda _: service)
    app = FastAPI()
    app.include_router(router.build_router())
    module = router.framework.get_module("subscription")
    assert isinstance(module, SubscriptionModule)
    monkeypatch.setattr(module, "last_refresh", 1000)
    ConfigManager.add_listener("ai_desktop_", router.framework.handle_config_changed)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/modules/subscription/login")).status_code == 409
        result = json.loads(tools.ai_desktop_modules(action="set_config", module="subscription",
                            name="provider_bailian_enabled", value="true"))
        assert result["success"] and result["value"] is True
        assert module.last_refresh == 0
        response = await client.post("/modules/subscription/login")
        assert response.status_code == 200
        sid = response.json()["id"]
        await reach(service, "waiting")
        image = await client.get(f"/modules/subscription/login/{sid}/image")
        assert image.status_code == 200 and image.headers["cache-control"] == "no-store"
        assert (await client.get("/modules/subscription/login/unknown")).status_code == 404
        assert (await client.post(f"/modules/subscription/login/{sid}/confirm")).status_code == 409
        assert (await client.delete(f"/modules/subscription/login/{sid}")).status_code == 200
        assert service._task is None and all(c.is_closed for c in upstream["clients"])
