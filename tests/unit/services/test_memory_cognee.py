"""Cognee Web 配置保存不得先覆盖不兼容的数据卷配置。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent.memory.cognee.client import CogneeClient
from agent.memory.cognee.config import CogneeConfig
from agent.memory.cognee.coordinator import CogneeCoordinator
from agent.memory.cognee.llm_bridge import CogneeConfigError
from services.memory import MemoryService


@pytest.mark.parametrize("prune_only", [False, True])
async def test_maintenance_http_forwards_mode_and_returns_background_acceptance(monkeypatch, prune_only):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from web.routers.memory import router

    request = AsyncMock(return_value={"ok": True, "scheduled": True})
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_coordinator",
                        lambda: SimpleNamespace(request_compact=request))
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
        response = await http.post("/memory/cognee/compact", params={"prune_only": str(prune_only).lower()})
    assert response.status_code == 200 and response.json() == {"ok": True, "scheduled": True}
    request.assert_awaited_once_with(prune_only=prune_only)


async def test_dataset_http_uses_observation_without_waiting_for_sync(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from web.routers.memory import router

    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    read = AsyncMock(return_value=[{"id": "old", "name": "anelf_global"}])
    monkeypatch.setattr(client, "_invoke", read)
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_client", lambda: client)
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
        before = await http.get("/memory/cognee/datasets")
        assert not before.json()["ready"] and before.json()["items"] == []
        read.assert_not_awaited()
        await client.list_datasets()
        client._fault = "previous batch failed"
        async with client._operation_lock:
            async with asyncio.timeout(5):
                result = await http.get("/memory/cognee/datasets")
            assert result.status_code == 200
            assert result.json()["items"] == [{"id": "old", "name": "anelf_global"}]
            assert result.json()["busy"] and result.json()["ready"]
            assert result.json()["updated_at"] > 0 and result.json()["error"] == client._fault
            read.assert_awaited_once()


@pytest.mark.parametrize("resource", ["datasets", "generations"])
async def test_retirement_http_returns_task_state_and_rejects_unsafe_targets(monkeypatch, resource):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from agent.memory.cognee.retirement import RetirementStatus
    from web.routers.memory import router

    request = AsyncMock(return_value=RetirementStatus(running=True, dataset_id="old", stage="checking"))
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_coordinator", lambda: SimpleNamespace(
        retirement=SimpleNamespace(request=request, request_generation=request)))
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
        response = await http.delete(f"/memory/cognee/{resource}/old")
        assert response.status_code == 200 and response.json()["running"]
        request.assert_awaited_once_with("old")
        request.side_effect = ValueError("current dataset protected")
        response = await http.delete(f"/memory/cognee/{resource}/current")
        assert response.status_code == 409 and response.json()["detail"] == "current dataset protected"


async def test_recall_http_preserves_source_diagnostics_and_bounds_origin_details(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from agent.memory.memory_types import MemorySearchResult, RetrievalPlan
    from web.routers.memory import router

    result = MemorySearchResult(
        id="mem:7", snippet="归属明确的事实", score=.8, tags=["user:telegram:2"],
        sensitivity="private", timestamp=1_700_000_000,
        provenance={"associated": True, "origins": [
            {"kind": "agent_note", "scope": f"group_qq:{i}"} for i in range(5)
        ]},
    )
    diagnostic = AsyncMock(return_value={
        "plan": RetrievalPlan(queries=["query"]), "cognee_config": CogneeConfig(), "deep": False,
        "datasets": [], "results": [result], "relations": [], "forgotten": [], "timings": {},
    })
    monkeypatch.setattr("services.memory.require_runtime", lambda: SimpleNamespace(
        retriever=SimpleNamespace(diagnostic_recall=diagnostic), memory_store=object(),
    ))
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
        response = await http.post("/memory/recall-test", json={"query": "query"})
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["tags"] == ["user:telegram:2"] and item["sensitivity"] == "private"
    assert item["timestamp"] == result.timestamp
    assert item["provenance"]["associated"] is True
    assert item["provenance"]["origin_count"] == 5 and len(item["provenance"]["origins"]) == 3
    assert len(result.provenance["origins"]) == 5


@pytest.mark.parametrize("compatible", [False, True])
async def test_save_validates_before_persisting_or_reconfiguring(monkeypatch, compatible):
    saved = Mock()
    validate = Mock(side_effect=None if compatible else CogneeConfigError("不兼容的向量模型"))
    coordinator = SimpleNamespace(
        client=SimpleNamespace(validate_configuration=validate), reconfigure=AsyncMock(),
        rebuild=SimpleNamespace(validate_config=Mock()),
    )
    monkeypatch.setattr("agent.memory.cognee.config.load_cognee_config", CogneeConfig)
    monkeypatch.setattr("agent.memory.cognee.config.save_cognee_config", saved)
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_coordinator", lambda: coordinator)
    if compatible:
        result = await MemoryService.save_cognee_config({"chat": {"max_completion_tokens": 4096}})
        assert result["chat"]["max_completion_tokens"] == 4096
        saved.assert_called_once()
        coordinator.reconfigure.assert_awaited_once()
    else:
        with pytest.raises(CogneeConfigError, match="不兼容"):
            await MemoryService.save_cognee_config({"embedding": {"model": "different"}})
        saved.assert_not_called()
        coordinator.reconfigure.assert_not_awaited()


async def test_pause_persists_even_with_invalid_model_but_resume_requires_validation(monkeypatch):
    saved = Mock()
    validate = Mock(side_effect=CogneeConfigError("invalid model"))
    coordinator = SimpleNamespace(
        client=SimpleNamespace(validate_configuration=validate), reconfigure=AsyncMock(),
        rebuild=SimpleNamespace(validate_config=Mock()),
    )
    monkeypatch.setattr("agent.memory.cognee.config.load_cognee_config", CogneeConfig)
    monkeypatch.setattr("agent.memory.cognee.config.save_cognee_config", saved)
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_coordinator", lambda: coordinator)
    result = await MemoryService.save_cognee_config({"sync_enabled": False})
    assert not result["sync_enabled"]
    validate.assert_not_called()
    coordinator.reconfigure.assert_awaited_once()
    assert not saved.call_args.args[0].sync_enabled
    with pytest.raises(CogneeConfigError, match="invalid model"):
        await MemoryService.save_cognee_config({"sync_enabled": True})
    assert saved.call_count == 1


async def test_http_save_during_projection_returns_and_preserves_latest_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from agent.memory.cognee.config import load_cognee_config
    from core.path import ConfigPaths
    from web.routers.memory import router

    monkeypatch.setattr(ConfigPaths, "COGNEE_CONFIG", str(tmp_path / "cognee.json"))
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: SimpleNamespace(
        get_all_by_type=lambda _kind: [], get_embedding_client=lambda: None,
    ))
    coordinator = CogneeCoordinator(Mock(), CogneeClient(CogneeConfig()), CogneeConfig())
    apply = AsyncMock()
    monkeypatch.setattr(coordinator, "_apply_configuration", apply)
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_coordinator", lambda: coordinator)
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
        async with coordinator._projection_lock:
            for effort in ("off", "low", "high"):
                response = await asyncio.wait_for(http.put("/memory/cognee/config", json={
                    "chat": {"source": "custom", "model": "openai/test", "reasoning_effort": effort},
                }), timeout=1)
                assert response.status_code == 200, response.text
                assert load_cognee_config().chat.reasoning_effort == effort
            response = await http.put("/memory/cognee/config", json={
                "chat": {"extra_args": {"timeout": 300, "top_p": 0.9}},
                "native_watchdog_restart_seconds": 900,
                "compact_cleanup_interval_seconds": 120,
                "compact_max_versions_per_run": 5000,
                "compact_cleanup_budget_seconds": 300,
                "project_graph_enabled": False,
            })
            assert response.status_code == 200, response.text
            persisted = load_cognee_config()
            assert persisted.chat.extra_args == {"timeout": 300, "top_p": 0.9}
            assert persisted.native_watchdog_restart_seconds == 900
            assert persisted.compact_cleanup_interval_seconds == 120
            assert persisted.compact_max_versions_per_run == 5000
            assert persisted.compact_cleanup_budget_seconds == 300
            assert not persisted.project_graph_enabled
            schema = {field["key"]: field for field in response.json()["options"]["settings_fields"]}
            assert set(schema) == set(persisted.to_dict()) - {"chat", "embedding"}
            assert schema["data_root"]["read_only"]
            assert schema["timeout_seconds"]["section"] == "recall"
            assert schema["pipeline_timeout_seconds"]["section"] == "sync"
            assert schema["compact_max_versions_per_run"]["section"] == "storage"
            assert schema["compact_max_versions_per_run"]["min"] == 1
            assert schema["compact_cleanup_budget_seconds"]["advanced"]
            response = await http.put("/memory/cognee/config", json={"chat": {"extra_args": {"top_p": 0.9}}})
            assert response.status_code == 200, response.text
            assert load_cognee_config().chat.extra_args == {"top_p": 0.9}
            for invalid in (0, -1):
                response = await http.put("/memory/cognee/config", json={"chat": {"extra_args": {"timeout": invalid}}})
                assert response.status_code == 400
                assert load_cognee_config().chat.extra_args == {"top_p": 0.9}
            apply.assert_not_awaited()
            assert coordinator.configuration_pending
        assert coordinator._configuration_task is not None
        await coordinator._configuration_task
        apply.assert_awaited_once()
        assert apply.call_args.args[0].chat.reasoning_effort == "high"
        response = await http.put("/memory/cognee/config", json={"chat": {"model": ""}})
        assert response.status_code == 400
        assert "必须填写模型" in response.json()["detail"]
        assert load_cognee_config().chat.model == "openai/test"
    await coordinator.close()
