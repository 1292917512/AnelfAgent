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
    (dist / "assets" / "app-abCD1234.js").write_text("const message = 'hello';\n" * 200, encoding="utf-8")
    (dist / "assets" / "plain.js").write_text("plain", encoding="utf-8")
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


def test_fingerprinted_assets_are_compressed_and_reused(client: TestClient):
    path = "/webui/assets/app-abCD1234.js"
    plain = client.get(path, headers={"Accept-Encoding": "identity"})
    compressed = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert compressed.text == plain.text
    assert "Content-Encoding" not in plain.headers
    assert compressed.headers["Content-Encoding"] == "gzip"
    assert "accept-encoding" in compressed.headers["Vary"].lower()
    assert compressed.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    cached = client.get(path, headers={"If-None-Match": plain.headers["ETag"]})
    assert cached.status_code == 304
    assert cached.headers["Cache-Control"] == compressed.headers["Cache-Control"]
    partial = client.get(path, headers={"Range": "bytes=0-9", "Accept-Encoding": "identity"})
    assert partial.status_code == 206
    assert partial.content == plain.content[:10]


def test_only_fingerprinted_assets_receive_immutable_caching(client: TestClient):
    assert client.get("/webui/assets/plain.js").headers["Cache-Control"] == "no-cache"
    for path in ("/webui/", "/webui/models", "/webui"):
        response = client.get(path)
        assert "no-store" in response.headers["Cache-Control"]
        assert "Content-Encoding" not in response.headers
    missing = client.get("/webui/assets/missing-abCD1234.js")
    assert missing.status_code == 404
    assert "immutable" not in missing.headers.get("Cache-Control", "")


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


def test_module_auth_applies_only_to_exact_registered_method(client: TestClient):
    from fastapi import APIRouter, HTTPException, Request

    from core.http_endpoints import self_authenticated

    router = APIRouter()

    @router.post("/ingest")
    @self_authenticated
    async def ingest(request: Request) -> dict[str, bool]:
        if request.headers.get("X-Module-Token") != "module-secret":
            raise HTTPException(401, "Invalid module token")
        return {"ok": True}

    @router.get("/settings")
    async def settings() -> dict[str, bool]:
        return {"private": True}

    server._include_module_router(client.app, router, prefix="/api/entity/test", tags=["test"])
    assert client.post("/api/entity/test/ingest").status_code == 401
    assert client.post("/api/entity/test/ingest", headers={"X-Module-Token": "module-secret"}).status_code == 200
    assert client.get("/api/entity/test/ingest").status_code == 401
    assert client.get("/api/entity/test/settings").status_code == 401
    server._unmount_module_router(client.app, "/api/entity/test")
    assert client.post("/api/entity/test/ingest", headers={"X-Module-Token": "module-secret"}).status_code == 401


def test_audio_ingest_keeps_its_own_auth_under_web_password(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from entities.audiosync import router

    monkeypatch.setattr(router, "get_config", lambda key, default=None: "module-secret" if key == "audiosync_ingest_token" else default)
    server._include_module_router(client.app, router.build_router(), prefix="/api/entity/audiosync", tags=["audio"])
    denied = client.post("/api/entity/audiosync/ingest", json={"segments": []})
    assert denied.status_code == 401 and denied.json()["detail"] == "ingest token 无效"
    assert client.get("/api/entity/audiosync/config", headers={"X-Ingest-Token": "module-secret"}).status_code == 401


def test_share_token_does_not_unlock_management(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from entities.share import router

    monkeypatch.setattr(router, "get_share_store", lambda: SimpleNamespace(get_by_token=AsyncMock(return_value=None)))
    server._include_module_router(client.app, router.build_router(), prefix="/api/entity/share", tags=["share"])
    for path in ("/d/invalid", "/raw/invalid", "/v/invalid"):
        assert client.get("/api/entity/share" + path).status_code == 404
    for path in ("/links", "/stats", "/logs"):
        assert client.get("/api/entity/share" + path).status_code == 401
