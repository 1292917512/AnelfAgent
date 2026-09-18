"""核心人脸路由测试（/api/face）：状态 / 人物 API / 实体绑定 / 事件 / 图片守卫。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.vision.face.store import get_face_store
from web.routers.face import router as face_router


@pytest.fixture
def face_cred(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """人脸引擎地址写入隔离的凭据中心存储（不碰真凭据文件）。"""
    from core import provider_keys as pk

    monkeypatch.setattr(pk, "_path", lambda: str(tmp_path / "keys.json"))
    monkeypatch.setattr(pk, "_cache", None)

    def _set(value: str) -> None:
        pk.set_provider_key("face", "face_endpoint", value)

    return _set


@pytest.fixture
def client():
    """挂载核心人脸路由（库走 conftest 隔离的核心库单例）。"""
    app = FastAPI()
    app.include_router(face_router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


class TestStatus:
    async def test_status_not_configured(
        self, client: TestClient, face_cred,
    ) -> None:
        # face_cred 隔离凭据存储（不写入 → 未配置态），不读用户真实凭据
        resp = client.get("/api/face/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["engine"]["configured"] is False
        assert body["engine"]["reachable"] is False
        assert "stats" in body and "thresholds" in body

    async def test_status_configured_endpoint(self, client: TestClient, face_cred) -> None:
        face_cred("http://gpu-host:10097")
        from agent.vision.face import engine as face_engine
        face_engine.reset_probe_cache()
        resp = client.get("/api/face/status")
        body = resp.json()
        assert body["engine"]["configured"] is True
        assert body["engine"]["endpoint"] == "http://gpu-host:10097"


class TestPersonApi:
    async def test_crud_flow(self, client: TestClient) -> None:
        store = get_face_store()
        person = await store.create_person(name="张三", role="家人")
        pid = int(person["id"])

        resp = client.get("/api/face/persons", params={"keyword": "张三"})
        assert resp.json()["total"] == 1

        resp = client.get(f"/api/face/persons/{pid}")
        assert resp.status_code == 200
        assert resp.json()["person"]["name"] == "张三"

        resp = client.patch(f"/api/face/persons/{pid}", json={"role": "同事"})
        assert resp.json()["person"]["role"] == "同事"

        resp = client.post(f"/api/face/persons/{pid}/bind",
                           json={"entity_scope": "user:qq:456"})
        assert resp.json()["person"]["entity_scope"] == "user:qq:456"

        resp = client.get("/api/face/persons/by-entity/user:qq:456")
        assert any(x["id"] == pid for x in resp.json()["items"])

        resp = client.delete(f"/api/face/persons/{pid}")
        assert resp.status_code == 200
        resp = client.get(f"/api/face/persons/{pid}")
        assert resp.status_code == 404

    async def test_bind_invalid_scope(self, client: TestClient) -> None:
        store = get_face_store()
        person = await store.create_person(name="李四")
        resp = client.post(f"/api/face/persons/{person['id']}/bind",
                           json={"entity_scope": "bogus:x"})
        assert resp.status_code == 400

    async def test_confirm_pending(self, client: TestClient) -> None:
        store = get_face_store()
        person = await store.create_person(status="pending")
        resp = client.post(f"/api/face/persons/{person['id']}/confirm",
                           json={"name": "王五", "role": ""})
        assert resp.status_code == 200
        assert resp.json()["person"]["status"] == "confirmed"
        assert resp.json()["person"]["person_key"].startswith("fc_")


class TestEvents:
    async def test_events_empty(self, client: TestClient) -> None:
        resp = client.get("/api/face/events")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_event_lifecycle(self, client: TestClient) -> None:
        store = get_face_store()
        eid = await store.add_event(image_path="/tmp/x.jpg", source="inbound", faces=[])
        resp = client.get("/api/face/events", params={"unread_only": True})
        assert resp.json()["total"] == 1
        resp = client.post("/api/face/events/mark-read", json={"event_ids": None, "read": True})
        assert resp.json()["affected"] == 1
        resp = client.delete(f"/api/face/events/{eid}")
        assert resp.status_code == 200


class TestImageGuard:
    async def test_path_traversal_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/face/image", params={"path": "/etc/passwd"})
        assert resp.status_code == 400

    async def test_missing_file_404(self, client: TestClient, tmp_path) -> None:
        import os

        from core.path import ConfigPaths
        upload_root = str(ConfigPaths.UPLOAD_DIR)
        missing = os.path.join(upload_root, "face", "nope.jpg")
        resp = client.get("/api/face/image", params={"path": missing})
        assert resp.status_code == 404
