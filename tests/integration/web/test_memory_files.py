"""记忆文件 API 的索引路径与数据目录契约。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from web.server import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("agent.memory.notes._memory_dir", tmp_path)
    (tmp_path / "heartbeat.md").write_text("### 心跳\n- 已执行检查\n", encoding="utf-8")
    with TestClient(create_app()) as client:
        yield client


def test_heartbeat_log_uses_memory_index_path(client: TestClient) -> None:
    response = client.get("/api/memory/files/content", params={"path": "memory/heartbeat.md"})
    assert response.status_code == 200
    assert "已执行检查" in response.json()["content"]


@pytest.mark.parametrize("path", ["config/memory/heartbeat.md", "memory/../outside.md", "memory/data.db"])
def test_invalid_paths_are_client_errors(client: TestClient, path: str) -> None:
    response = client.get("/api/memory/files/content", params={"path": path})
    assert response.status_code == 400
    response = client.put("/api/memory/files/content", json={"path": path, "content": "invalid"})
    assert response.status_code == 400


def test_missing_log_has_empty_content(client: TestClient) -> None:
    response = client.get("/api/memory/files/content", params={"path": "memory/missing.md"})
    assert response.status_code == 200
    assert response.json() == {"content": ""}


def test_document_index_failure_is_reported_and_upload_removed(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = tmp_path / "docs"
    monkeypatch.setattr("services.memory._docs_dir", lambda: docs)
    monkeypatch.setattr("services.memory.require_runtime", lambda: SimpleNamespace(
        memory_store=object(), embedder=None,
    ))
    monkeypatch.setattr("agent.memory.memory_sync.index_single_file", AsyncMock(side_effect=RuntimeError("write failed")))
    response = client.post("/api/memory/documents/upload", files={"file": ("test.pdf", b"invalid", "application/pdf")})
    assert response.status_code == 200
    assert "error" in response.json()
    assert not list(docs.iterdir())


def test_goal_terminal_cleanup_exposes_auditable_destination(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.memory.memory_store import MemoryStore

    store = MemoryStore(str(tmp_path / "goals.sqlite3"))
    monkeypatch.setattr("services.memory.require_runtime", lambda: SimpleNamespace(memory_store=store))
    assert client.portal is not None
    try:
        created = client.post("/api/memory/goals", json={"title": "API 目标", "steps": ["执行"]})
        assert created.status_code == 200
        goal = created.json()
        response = client.put(f"/api/memory/goals/{goal['goal_id']}", json={"status": "completed"})
        assert response.status_code == 200 and response.json()["status"] == "completed"
        assert client.get("/api/memory/goals", params={"include_archived": True}).json()["total"] == 0
        timeline = client.get("/api/memory/timeline", params={"memory_id": goal["memory_id"]})
        assert timeline.status_code == 200
        events = timeline.json()["items"]
        deletion = next(item for item in events if item["action"] == "delete")
        assert deletion["data"]["goal_id"] == goal["goal_id"]
        assert deletion["data"]["destination"] == "deleted"
        assert deletion["data"]["status"] == "completed"
    finally:
        client.portal.call(store.close)
