"""音源同步实体路由测试：ingest 令牌鉴权 / 实体配置读写 / 同步端点。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import entities.audiosync.router as router_mod
from core.config import ConfigManager


def vec(dim: int) -> list[float]:
    return [1.0 if i == dim else 0.0 for i in range(192)]


@pytest.fixture
def client():
    """挂载实体路由（音频库走 conftest 隔离的核心库单例）。"""
    app = FastAPI()
    app.include_router(router_mod.build_router(), prefix="/api/entity/audiosync")
    with TestClient(app) as test_client:
        yield test_client


class TestIngestAuth:
    def test_fail_closed_without_token_config(self, client: TestClient) -> None:
        resp = client.post("/api/entity/audiosync/ingest", json={"segments": []})
        assert resp.status_code == 503

    def test_rejects_wrong_token(self, client: TestClient) -> None:
        ConfigManager.set("audiosync_ingest_token", "secret")
        resp = client.post(
            "/api/entity/audiosync/ingest",
            json={"segments": []},
            headers={"X-Ingest-Token": "wrong"},
        )
        assert resp.status_code == 401

    async def test_accepts_valid_token(self, client: TestClient, _isolate_audio_library) -> None:
        ConfigManager.set("audiosync_ingest_token", "secret")
        resp = client.post(
            "/api/entity/audiosync/ingest",
            json={
                "source_file": "/nas/a.wav",
                "segments": [{"start_ms": 0, "end_ms": 3000, "text": "你好", "vector": vec(0)}],
            },
            headers={"X-Ingest-Token": "secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ingested"] == 1
        assert body["results"][0]["is_new_speaker"] is True
        # 片段落入核心音频库（隔离单例）
        assert (await _isolate_audio_library.list_segments())["total"] == 1


class TestConfig:
    def test_config_roundtrip(self, client: TestClient) -> None:
        resp = client.get("/api/entity/audiosync/config")
        assert resp.status_code == 200
        keys = {item["key"] for item in resp.json()["items"]}
        assert "audiosync_watch_enabled" in keys

        resp = client.put("/api/entity/audiosync/config", json={
            "updates": {"audiosync_watch_paused": True, "bogus_key": 1},
        })
        assert resp.json()["updated"] == 1
        assert ConfigManager.get("audiosync_watch_paused") is True


class TestSourceEndpoints:
    def test_source_list(self, client: TestClient) -> None:
        resp = client.get("/api/entity/audiosync/source/list")
        assert resp.status_code == 200
        sources = {s["key"] for s in resp.json()["sources"]}
        assert {"local_dir", "openlist"} <= sources

    def test_source_status_unconfigured(self, client: TestClient) -> None:
        ConfigManager.set("audiosync_watch_dir", "")
        ConfigManager.set("audiosync_openlist_endpoint", "")
        resp = client.get("/api/entity/audiosync/source/status")
        assert resp.status_code == 200
        assert resp.json()["configured"] is False
