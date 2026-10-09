"""静态文件路径与鉴权配置故障的 HTTP/WS 回归。"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.path import ConfigPaths
from web import server


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<h1>app</h1>", encoding="utf-8")
    (dist / "test.txt").write_text("public", encoding="utf-8")
    (tmp_path / "private.txt").write_text("PRIVATE_MARKER", encoding="utf-8")
    monkeypatch.setattr(server, "FRONTEND_DIST", dist)
    monkeypatch.setattr(server, "_auth_cache", {})
    monkeypatch.setattr(ConfigPaths, "WEBUI_CONFIG", str(tmp_path / "webui.json"))
    for name in ("_mount_channel_routers", "_mount_entity_routers", "_subscribe_module_hotplug"):
        monkeypatch.setattr(server, name, lambda app: None)
    Path(ConfigPaths.WEBUI_CONFIG).write_text(
        json.dumps({"auth": {"password": "test-secret", "strict": True}}), encoding="utf-8",
    )
    with TestClient(server.create_app()) as client:
        yield client


@pytest.mark.parametrize("path", ["%2e%2e/private.txt", "%2e%2e%5cprivate.txt"])
def test_static_traversal_never_discloses_file(client: TestClient, path: str):
    response = client.get(f"/webui/{path}")
    assert "PRIVATE_MARKER" not in response.text
    assert client.get("/webui/test.txt").text == "public"
    assert "<h1>app</h1>" in client.get("/webui/chat").text


def test_corrupt_auth_config_closes_all_auth_entrypoints(client: TestClient):
    assert client.get("/api/status").status_code == 401
    Path(ConfigPaths.WEBUI_CONFIG).write_text("{", encoding="utf-8")
    assert client.get("/api/status").status_code == 503
    assert client.get("/api/auth/check").status_code == 503
    assert client.post("/api/auth/login", json={"password": ""}).status_code == 503
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/chat/ws"):
            pass


def test_corrupt_auth_at_first_read_is_not_anonymous(client: TestClient):
    Path(ConfigPaths.WEBUI_CONFIG).write_text('[]', encoding="utf-8")
    assert client.get("/api/status").status_code == 503


def test_missing_previously_protected_config_stays_closed(client: TestClient):
    assert client.get("/api/status").status_code == 401
    Path(ConfigPaths.WEBUI_CONFIG).unlink()
    assert client.get("/api/status").status_code == 503
