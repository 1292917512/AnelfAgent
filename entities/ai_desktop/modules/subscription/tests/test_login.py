"""额度平台选择、授权复用和扫码会话生命周期。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

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
def browser(monkeypatch):
    scanned = asyncio.Event()
    qr = Mock(screenshot=AsyncMock(return_value=b"png"))
    qr.locator.return_value.wait_for = AsyncMock()
    authorize = Mock(click=AsyncMock(), wait_for=AsyncMock(side_effect=lambda **_: scanned.wait()))
    async def wait_for_scan(**_):
        await scanned.wait()
    authorize.wait_for = wait_for_scan
    enter = Mock(click=AsyncMock())
    page = SimpleNamespace(goto=AsyncMock(), locator=Mock(return_value=qr),
                           get_by_role=Mock(side_effect=lambda _, name, exact: enter if name == "前往登录" else authorize))
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), route=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    manager = AsyncMock()
    monkeypatch.setattr(login_module.importlib, "import_module", Mock(return_value=SimpleNamespace(async_playwright=lambda: manager)))
    monkeypatch.setattr(login_module, "open_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr(login_module, "cli_credential", lambda: ("bl", "CLI"))
    return SimpleNamespace(page=page, context=context, browser=browser, scanned=scanned, authorize=authorize, enter=enter)


async def waiting(service: ConsoleLogin) -> dict:
    state = await service.start()
    async with asyncio.timeout(1):
        while service.status(state["id"])["state"] == "starting":
            await asyncio.sleep(0)
    return service.status(state["id"])


async def test_login_coalesces_validates_callback_and_releases_browser(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("ANELF_CONFIG_DIR", str(tmp_path))
    complete = AsyncMock()
    service = ConsoleLogin(complete)
    state = await waiting(service)
    assert (await service.start())["id"] == state["id"]
    with pytest.raises(LookupError):
        await service.image("wrong-session")
    with pytest.raises(ValueError):
        await service.confirm(state["id"])
    assert await service.image(state["id"]) == b"png"
    callback, handler = browser.context.route.call_args.args
    assert "needapikey" not in browser.page.goto.call_args.args[0]
    route = SimpleNamespace(request=SimpleNamespace(headers={"origin": "https://wrong.test"}, method="POST",
                            post_data='{}', post_data_json={"access_token": "test-token"}),
                            abort=AsyncMock(), fulfill=AsyncMock())
    await handler(route)
    route.abort.assert_awaited_once()
    assert not (bailian.console_config_dir() / "config.json").exists()
    route.request.headers["origin"] = "https://bailian.console.aliyun.com"
    browser.enter.click.assert_awaited_once()
    browser.scanned.set()
    async with asyncio.timeout(1):
        while service.status(state["id"])["state"] != "confirming":
            await asyncio.sleep(0)
    await service.confirm(state["id"])
    browser.authorize.click.assert_awaited_once()
    await handler(route)
    await service._task
    assert service.status(state["id"])["state"] == "complete"
    assert "test-token" not in str(service.status(state["id"]))
    assert "state=" in callback
    complete.assert_awaited_once()
    browser.browser.close.assert_awaited_once()
    await service.close()


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_cancel_and_timeout_release_resources(browser, monkeypatch, stop):
    if stop == "timeout":
        monkeypatch.setattr(login_module, "_TTL", 0.03)
    complete = AsyncMock()
    service = ConsoleLogin(complete)
    state = await waiting(service)
    if stop == "cancel":
        await service.close(state["id"])
    else:
        await service._task
    assert service.status(state["id"])["state"] == ("cancelled" if stop == "cancel" else "expired")
    complete.assert_not_awaited()
    browser.browser.close.assert_awaited_once()


async def test_preparation_is_visible_and_failure_can_restart(browser, monkeypatch):
    service = ConsoleLogin(AsyncMock())
    async def prepare(runtime, on_prepare):
        on_prepare()
        assert service.status(service._id)["state"] == "preparing"
        assert service.status(service._id)["expires_at"] == 0
        raise RuntimeError("private download detail")
    monkeypatch.setattr(login_module, "open_browser", prepare)
    state = await service.start()
    await service._task
    assert service.status(state["id"])["error"] == "browser_prepare_failed"
    assert "private" not in str(service.status(state["id"]))
    monkeypatch.setattr(login_module, "open_browser", AsyncMock(return_value=browser.browser))
    replacement = await waiting(service)
    assert replacement["id"] != state["id"]
    assert replacement["state"] == "waiting"
    await service.close()


async def test_ai_configuration_controls_login_route_and_authorization(browser, monkeypatch):
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
        assert (await client.get("/modules/subscription/login/unknown")).status_code == 404
        assert (await client.post(f"/modules/subscription/login/{sid}/confirm")).status_code == 409
        assert (await client.delete(f"/modules/subscription/login/{sid}")).status_code == 200
        assert service._task is None
