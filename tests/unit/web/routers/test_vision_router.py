"""核心视觉路由测试（/api/vision）：状态 / 源清单 / 监视开关 / 外部推送。"""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent.vision.buffer as buffer_mod
import agent.vision.framework as framework_mod
from agent.vision import VisualSource, get_vision_buffer
from web.routers.vision import router as vision_router


class FakeSource(VisualSource):
    key = "fake"
    display_name = "假源"
    description = "测试源"
    poll_interval = 1.0
    can_capture = True


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(framework_mod._SOURCES)
    framework_mod._SOURCES.clear()
    get_vision_buffer().reset()
    yield
    framework_mod._SOURCES.clear()
    framework_mod._SOURCES.update(saved)
    get_vision_buffer().reset()
    from core.config import ConfigManager
    ConfigManager.set("vision_disabled_sources", "")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(vision_router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_hash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(buffer_mod, "grid_dhash", lambda path, grid=4: [0] * (grid * grid))


class TestStatusAndSources:
    def test_status_shape(self, client: TestClient) -> None:
        resp = client.get("/api/vision/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "watching" in body and "latest" in body and "injection" in body

    def test_sources_list(self, client: TestClient) -> None:
        framework_mod.register_source(FakeSource())
        resp = client.get("/api/vision/sources")
        assert resp.status_code == 200
        keys = {s["key"] for s in resp.json()["sources"]}
        assert "fake" in keys


class TestWatch:
    def test_start_stop_watch(self, client: TestClient, fake_hash) -> None:
        framework_mod.register_source(FakeSource())
        resp = client.post("/api/vision/watch", json={"action": "start", "source": "fake"})
        assert resp.status_code == 200
        assert "fake" in resp.json()["watching"]
        resp = client.post("/api/vision/watch", json={"action": "stop", "source": "fake"})
        assert "fake" not in resp.json()["watching"]

    def test_watch_unknown_source_400(self, client: TestClient) -> None:
        resp = client.post("/api/vision/watch", json={"action": "start", "source": "不存在"})
        assert resp.status_code == 400


class TestPush:
    def test_push_frame(self, client: TestClient, fake_hash, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False)
        resp = client.post("/api/vision/push", json={
            "source": "doorbell",
            "image_base64": base64.b64encode(b"\xff\xd8\xff\xd9").decode(),
            "mime_type": "image/jpeg",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["source"] == "external:doorbell"
        assert get_vision_buffer().latest_by_source.get("external:doorbell") is not None

    def test_push_rejects_bad_source(self, client: TestClient) -> None:
        resp = client.post("/api/vision/push", json={
            "source": "../evil", "image_base64": "eA==", "mime_type": "image/png",
        })
        assert resp.status_code == 400

    def test_push_rejects_bad_base64(self, client: TestClient) -> None:
        resp = client.post("/api/vision/push", json={
            "source": "cam", "image_base64": "!!!not-base64!!!", "mime_type": "image/png",
        })
        assert resp.status_code == 400


class TestLatest:
    def test_latest_404_without_frames(self, client: TestClient) -> None:
        assert client.get("/api/vision/latest").status_code == 404


class TestSourceActivationApi:
    def test_disable_then_watch_rejected(self, client: TestClient, fake_hash) -> None:
        framework_mod.register_source(FakeSource())
        resp = client.post("/api/vision/watch", json={"action": "disable", "source": "fake"})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        resp = client.post("/api/vision/watch", json={"action": "start", "source": "fake"})
        assert resp.status_code == 400
        resp = client.post("/api/vision/watch", json={"action": "enable", "source": "fake"})
        assert resp.json()["enabled"] is True
        resp = client.post("/api/vision/watch", json={"action": "start", "source": "fake"})
        assert resp.status_code == 200
        client.post("/api/vision/watch", json={"action": "stop", "source": "fake"})

    def test_sources_expose_enabled_flag(self, client: TestClient) -> None:
        framework_mod.register_source(FakeSource())
        framework_mod.set_enabled("fake", False)
        entry = next(
            s for s in client.get("/api/vision/sources").json()["sources"]
            if s["key"] == "fake")
        assert entry["enabled"] is False

    def test_push_rejected_when_external_source_disabled(
        self, client: TestClient, fake_hash,
    ) -> None:
        framework_mod.set_enabled("external:doorbell", False)
        resp = client.post("/api/vision/push", json={
            "source": "doorbell",
            "image_base64": "eA==", "mime_type": "image/jpeg",
        })
        assert resp.status_code == 400
        framework_mod.set_enabled("external:doorbell", True)
