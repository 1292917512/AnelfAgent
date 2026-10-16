"""浏览器按需准备与取消清理契约。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import Error

from entities.ai_desktop.modules.subscription import browser_runtime


async def test_installed_browser_is_reused_and_only_missing_executable_triggers_download(monkeypatch):
    install = AsyncMock()
    monkeypatch.setattr(browser_runtime, "_install_browser", install)
    browser, preparing = object(), Mock()
    launch = AsyncMock(return_value=browser)
    runtime = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    assert await browser_runtime.open_browser(runtime, preparing) is browser
    install.assert_not_awaited()
    launch.side_effect = [Error("Executable doesn't exist at /cache/browser"), browser]
    assert await browser_runtime.open_browser(runtime, preparing) is browser
    install.assert_awaited_once()
    preparing.assert_called_once()
    launch.side_effect = Error("Host system is missing dependencies")
    with pytest.raises(Error):
        await browser_runtime.open_browser(runtime, preparing)
    assert install.await_count == 1


async def test_cancelled_preparation_reaps_installer_tree(monkeypatch):
    entered = asyncio.Event()
    calls = 0
    async def wait():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        return 0
    process = SimpleNamespace(pid=123, returncode=None, wait=wait)
    spawn = AsyncMock(return_value=process)
    stop = Mock()
    monkeypatch.setattr(browser_runtime.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(browser_runtime, "_stop_download", stop)
    task = asyncio.create_task(browser_runtime._install_browser())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stop.assert_called_once_with(123)
    assert calls == 2
    assert spawn.call_args.args[1:] == ("-m", "playwright", "install", "chromium", "--only-shell")
