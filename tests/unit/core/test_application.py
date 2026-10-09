"""Application 应用宿主单元测试。"""
from __future__ import annotations

import asyncio
import signal

import pytest

from core.application import Application
from core.lifecycle import Lifecycle


@pytest.fixture(autouse=True)
def _clean_registry():
    """用例前后复位，隔离注册表全局副作用。"""
    Lifecycle.reset()
    yield
    Lifecycle.reset()


def _stub_signals(app: Application) -> None:
    """以直接置位关停事件替代真实信号布防（测试不触碰进程信号处理器）。"""
    def fake_arm(loop: asyncio.AbstractEventLoop) -> None:
        assert app._shutdown_event is not None
        Lifecycle.set_shutdown_requester(app._shutdown_event.set)

    app._arm_signals = fake_arm  # type: ignore[method-assign]


def _request_shutdown_soon(delay: float = 0.05) -> asyncio.Task[None]:
    async def _later() -> None:
        await asyncio.sleep(delay)
        Lifecycle.request_shutdown()

    return asyncio.create_task(_later())


async def test_run_orchestrates_startup_services_and_shutdown():
    app = Application()
    _stub_signals(app)
    events: list[str] = []

    @app.startup.node()
    async def boot() -> None:
        events.append("startup")

    Lifecycle.register(
        "svc", None,
        on_start=lambda: events.append("on_start"),
        cleanup=lambda: events.append("cleanup"),
    )
    app.on_pre_shutdown("hook", lambda: events.append("pre_shutdown"))

    _request_shutdown_soon()
    await app.run()

    assert events == ["startup", "on_start", "pre_shutdown", "cleanup"]
    assert app.last_startup is not None and app.last_startup.success


async def test_startup_failure_skips_wait_but_still_shuts_down():
    app = Application()
    _stub_signals(app)
    events: list[str] = []

    @app.startup.node()
    async def bad_boot() -> None:
        raise RuntimeError("boom")

    Lifecycle.register("svc", None, on_start=lambda: events.append("on_start"))
    app.on_pre_shutdown("hook", lambda: events.append("pre_shutdown"))

    await app.run()

    assert app.last_startup is not None and not app.last_startup.success
    assert "on_start" not in events
    assert events == ["pre_shutdown"]


async def test_pre_shutdown_hook_failure_degrades_to_log():
    app = Application()
    _stub_signals(app)
    events: list[str] = []

    @app.startup.node()
    async def boot() -> None:
        pass

    def boom() -> None:
        raise RuntimeError("hook failed")

    app.on_pre_shutdown("bad", boom)
    app.on_pre_shutdown("good", lambda: events.append("good"))

    _request_shutdown_soon()
    await app.run()

    assert events == ["good"]


async def test_startup_timeline_serialization():
    app = Application()
    _stub_signals(app)

    @app.startup.node()
    async def boot() -> None:
        pass

    _request_shutdown_soon()
    await app.run()

    timeline = app.startup_timeline()
    assert len(timeline) == 1
    assert timeline[0]["name"] == "boot"
    assert timeline[0]["state"] == "success"
    assert timeline[0]["error"] is None


async def test_arm_signals_registers_handlers_and_requester(monkeypatch):
    """信号布防：注册 SIGINT/SIGTERM 处理器并接线 Lifecycle 关停请求。"""
    app = Application()
    app._shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    registered = {}
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, fn: registered.update({sig: fn}))
    app._arm_signals(loop)
    assert set(registered) == {signal.SIGINT, signal.SIGTERM}
    Lifecycle.request_shutdown()
    await asyncio.sleep(0)
    assert app._shutdown_event.is_set()


@pytest.mark.parametrize("during_startup", [True, False])
async def test_cancel_always_cleans_resources(during_startup):
    app = Application()
    _stub_signals(app)
    entered = asyncio.Event()
    cleaned = []
    Lifecycle.register("resource", None, cleanup=lambda: cleaned.append("resource"))
    app.on_post_shutdown("post", lambda: cleaned.append("post"))

    @app.startup.node()
    async def startup():
        entered.set()
        if during_startup:
            await asyncio.Event().wait()

    task = asyncio.create_task(app.run())
    await entered.wait()
    if not during_startup:
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned == ["resource", "post"]
