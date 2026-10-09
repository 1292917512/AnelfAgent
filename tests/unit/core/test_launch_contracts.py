"""组合根的启动失败传播与共享资源注册契约。"""

from argparse import Namespace

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
