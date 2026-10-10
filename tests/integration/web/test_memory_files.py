"""记忆文件 API 的索引路径与数据目录契约。"""

from pathlib import Path

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
