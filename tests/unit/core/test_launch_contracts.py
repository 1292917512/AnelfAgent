"""组合根的启动失败传播与共享资源注册契约。"""

from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.flow import FlowMachine
from core.lifecycle import Lifecycle


async def test_nested_bootstrap_failure_stops_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    import launch
    from agent.runtime import bootstrap
    from agent.storage import migration

    child = FlowMachine()

    @child.node(skip_on_error=False)
    async def failed_storage() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(bootstrap, "create_bootstrap", lambda: child)
    monkeypatch.setattr(migration, "finalize_pending_migration", lambda _assign: None)
    monkeypatch.setattr(launch.ConfigManager, "initialize", lambda: None)
    monkeypatch.setattr(launch, "enable_file_logging", lambda: None)
    monkeypatch.setattr(launch, "acquire_instance", lambda *_args: None)
    Lifecycle.reset()
    try:
        app = launch.create_application(Namespace(no_webui=True))
        result = await app.startup.execute()
        assert not result.success
        assert result.results[-1].name == "run_bootstrap"
        assert "failed_storage" in str(result.results[-1].error)
        assert Lifecycle.get("async_helper_executor") is not None
        assert "init_approval_rules" not in [node.name for node in result.results]
    finally:
        Lifecycle.reset()


async def test_shutdown_flush_uses_forced_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    import launch
    from agent.memory import auto_capture

    run = AsyncMock()
    monkeypatch.setattr(auto_capture, "_pipeline", lambda: SimpleNamespace(run=run))
    monkeypatch.setattr("services._runtime.require_runtime", lambda: SimpleNamespace(mind=object()))
    app = launch.create_application(Namespace(no_webui=True))
    flush = dict(app._pre_shutdown_hooks)["flush_pending_memory"]
    await flush()
    run.assert_awaited_once_with(force=True)


def test_storage_registration_precedes_consumers() -> None:
    from agent.runtime.bootstrap import create_bootstrap

    layers = create_bootstrap()._resolve_layers()
    order = {node.name: index for index, layer in enumerate(layers) for node in layer}
    assert order["init_memory"] < order["register_entity_lifecycles"]
    assert order["register_entity_lifecycles"] < order["register_internal_tools"]
    assert order["register_internal_tools"] < order["assemble_runtime"] < order["start_agent"]
