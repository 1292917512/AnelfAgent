from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.memory.cognee.client import CogneeClient
from agent.memory.cognee.config import CogneeConfig


class _FakeConfig:
    def system_root_directory(self, _path: str) -> None:
        return None

    def data_root_directory(self, _path: str) -> None:
        return None

    def set_llm_config(self, _config: dict) -> None:
        return None

    def set_embedding_config(self, _config: dict) -> None:
        return None


@pytest.mark.asyncio
async def test_client_normalizes_public_recall(monkeypatch, tmp_path) -> None:
    async def recall(_query: str, **_kwargs):
        return [{
            "source": "graph",
            "text": "graph answer",
            "score": 0.9,
            "dataset_name": "anelf_global",
            "metadata": {"chunk_id": "chunk-1"},
        }]

    fake = SimpleNamespace(
        __version__="1.3.0",
        config=_FakeConfig(),
        recall=recall,
        SearchType=SimpleNamespace(CHUNKS="CHUNKS"),
    )
    client = CogneeClient(CogneeConfig(
        enabled=True,
        data_root=str(tmp_path),
    ))
    monkeypatch.setattr(
        CogneeClient,
        "installed",
        property(lambda _self: True),
    )
    monkeypatch.setattr(client, "_import_cognee", lambda: fake)
    monkeypatch.setattr(client, "_configure", _configure_without_models(client))

    availability = await client.initialize()
    results = await client.recall("query")

    assert availability.ready
    assert availability.version == "1.3.0"
    assert results[0].id == "cognee:chunk-1"
    assert results[0].content == "graph answer"


def test_client_exposes_documented_public_boundary() -> None:
    expected = {
        "remember", "recall", "improve", "forget", "serve", "disconnect",
        "push", "export", "add", "cognify", "search", "memify", "update",
        "run_custom_pipeline", "run_migrations", "list_datasets",
        "discover_datasets", "list_data", "has_data", "get_dataset_status",
        "empty_dataset", "delete_data", "delete_all", "prune_data",
        "prune_system", "visualize", "visualize_graph",
        "get_schema_inventory", "get_memory_provenance_graph",
        "visualize_memory_provenance", "enable_tracing", "disable_tracing",
        "get_last_trace", "get_all_traces", "clear_traces",
    }
    assert expected.issubset(set(dir(CogneeClient)))


@pytest.mark.asyncio
async def test_disabled_client_never_imports_optional_package(monkeypatch) -> None:
    client = CogneeClient(CogneeConfig(enabled=False))

    def fail_import():
        raise AssertionError("disabled client must remain lazy")

    monkeypatch.setattr(client, "_import_cognee", fail_import)
    availability = await client.initialize()

    assert not availability.ready
    assert not availability.enabled


def test_config_resolves_absolute_windows_storage_path(tmp_path) -> None:
    config = CogneeConfig(data_root=str(tmp_path))
    assert config.absolute_data_root == str(tmp_path.resolve())


def _configure_without_models(client: CogneeClient):
    async def configure(module) -> None:
        client._module = module
        client._configured = True

    return configure
