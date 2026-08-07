"""音源库路由与 AI 工具面测试：ingest 令牌鉴权 / 说话人 API / 工具错误归因。"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import entities.voiceprint.ingest as ingest_mod
import entities.voiceprint.router as router_mod
import entities.voiceprint.tools as tools_mod
from core.config import ConfigManager
from entities.voiceprint import matcher
from entities.voiceprint.store import VoiceprintStore


def vec(dim: int) -> list[float]:
    return [1.0 if i == dim else 0.0 for i in range(192)]


@pytest.fixture
async def store(tmp_path):
    s = VoiceprintStore(str(tmp_path / "voiceprints.sqlite3"))
    yield s
    await s.close()


@pytest.fixture
def client(store: VoiceprintStore, monkeypatch: pytest.MonkeyPatch):
    """挂载实体路由并注入临时库（router/ingest 两处单例引用都替换）。"""
    monkeypatch.setattr(router_mod, "get_voiceprint_store", lambda: store)
    monkeypatch.setattr(ingest_mod, "get_voiceprint_store", lambda: store)
    app = FastAPI()
    app.include_router(router_mod.build_router(), prefix="/api/entity/voiceprint")
    with TestClient(app) as test_client:
        yield test_client


class TestIngestAuth:
    def test_fail_closed_without_token_config(self, client: TestClient) -> None:
        resp = client.post("/api/entity/voiceprint/ingest", json={"segments": []})
        assert resp.status_code == 503

    def test_rejects_wrong_token(self, client: TestClient) -> None:
        ConfigManager.set("voiceprint_ingest_token", "secret")
        resp = client.post(
            "/api/entity/voiceprint/ingest",
            json={"segments": []},
            headers={"X-Ingest-Token": "wrong"},
        )
        assert resp.status_code == 401

    def test_accepts_valid_token(self, client: TestClient, store: VoiceprintStore) -> None:
        ConfigManager.set("voiceprint_ingest_token", "secret")
        resp = client.post(
            "/api/entity/voiceprint/ingest",
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


class TestSpeakerApi:
    async def test_crud_flow(self, client: TestClient, store: VoiceprintStore) -> None:
        # 注册
        resp = client.post("/api/entity/voiceprint/speakers", json={
            "name": "张三", "vector": vec(0), "role": "家人",
        })
        assert resp.status_code == 200
        speaker = resp.json()
        assert speaker["speaker_key"].startswith("spk_")

        # 列表 / 详情
        resp = client.get("/api/entity/voiceprint/speakers", params={"keyword": "张三"})
        assert resp.json()["total"] == 1
        resp = client.get(f"/api/entity/voiceprint/speakers/{speaker['id']}")
        assert resp.status_code == 200
        assert resp.json()["samples"][0]["dims"] == 192

        # 编辑（独立阈值）
        resp = client.patch(
            f"/api/entity/voiceprint/speakers/{speaker['id']}", json={"threshold": 0.8})
        assert resp.json()["speaker"]["threshold"] == 0.8

        # 向量识别（完全同向量 → 命中）
        resp = client.post("/api/entity/voiceprint/identify", json={"vector": vec(0)})
        candidates = resp.json()
        assert candidates[0]["matched"] is True
        assert candidates[0]["name"] == "张三"

        # 删除
        resp = client.delete(f"/api/entity/voiceprint/speakers/{speaker['id']}")
        assert resp.status_code == 200
        assert client.get("/api/entity/voiceprint/speakers").json()["total"] == 0

    async def test_confirm_and_merge_api(self, client: TestClient, store: VoiceprintStore) -> None:
        target = await matcher.enroll(store, "张三", vec(0))
        tmp = (await matcher.identify(store, vec(5)))["speaker"]

        resp = client.post(
            f"/api/entity/voiceprint/speakers/{tmp['id']}/confirm", json={"name": "王五"})
        assert resp.status_code == 200
        assert resp.json()["speaker"]["status"] == "confirmed"

        resp = client.post("/api/entity/voiceprint/speakers/merge", json={
            "source_id": tmp["id"], "target_id": target["id"],
        })
        assert resp.status_code == 200
        assert resp.json()["samples_moved"] == 1

    def test_404_on_missing_speaker(self, client: TestClient) -> None:
        assert client.get("/api/entity/voiceprint/speakers/999").status_code == 404
        assert client.delete("/api/entity/voiceprint/speakers/999").status_code == 404


class TestSegmentApi:
    async def test_query_and_reassign(self, client: TestClient, store: VoiceprintStore) -> None:
        s = await matcher.enroll(store, "张三", vec(0))
        seg_id = await store.add_segment(
            speaker_id=s["id"], transcript="今晚一起吃饭", ts_ns=1_785_988_800_000_000_000)

        resp = client.get("/api/entity/voiceprint/segments", params={"q": "吃饭"})
        assert resp.status_code == 200
        assert resp.json()["items"][0]["speaker_name"] == "张三"

        resp = client.get("/api/entity/voiceprint/segments", params={"speaker_id": s["id"]})
        assert resp.json()["total"] == 1

        resp = client.patch(
            f"/api/entity/voiceprint/segments/{seg_id}", json={"speaker_id": None})
        assert resp.status_code == 200
        assert resp.json()["segment"]["speaker_id"] is None

        resp = client.post("/api/entity/voiceprint/segments/mark-read", json=None)
        assert resp.json()["marked_read"] == 1

    async def test_stats(self, client: TestClient, store: VoiceprintStore) -> None:
        await matcher.enroll(store, "张三", vec(0))
        resp = client.get("/api/entity/voiceprint/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["speakers"] == 1
        assert body["match_threshold"] == 0.75
        assert body["ingest_enabled"] is False


@pytest.fixture
def tool_store(store: VoiceprintStore, monkeypatch: pytest.MonkeyPatch) -> VoiceprintStore:
    """把工具面的库单例替换为临时库。"""
    monkeypatch.setattr(tools_mod, "get_voiceprint_store", lambda: store)
    return store


class TestTools:
    async def test_speaker_update_confirm_flow(self, tool_store: VoiceprintStore) -> None:
        tmp = (await matcher.identify(tool_store, vec(2)))["speaker"]
        raw = await tools_mod.speaker_update(str(tmp["id"]), name="王五", status="confirmed")
        body = json.loads(raw)
        assert body["speaker"]["name"] == "王五"
        assert body["speaker"]["status"] == "confirmed"

    async def test_resolve_ambiguous_returns_candidates(
        self, tool_store: VoiceprintStore,
    ) -> None:
        await matcher.enroll(tool_store, "张伟", vec(0))
        await matcher.enroll(tool_store, "张强", vec(1))
        raw = await tools_mod.speaker_get("张")
        body = json.loads(raw)
        assert "error" in body
        assert len(body["candidates"]) == 2

    async def test_not_found_error_attribution(self, tool_store: VoiceprintStore) -> None:
        raw = await tools_mod.speaker_get("不存在")
        body = json.loads(raw)
        assert body["cause"] == "not_found"

    async def test_transcript_search_bad_time(self, tool_store: VoiceprintStore) -> None:
        raw = await tools_mod.transcript_search(query="", time_from="垃圾时间")
        body = json.loads(raw)
        assert body["cause"] == "param"

    async def test_set_threshold(self, tool_store: VoiceprintStore) -> None:
        raw = await tools_mod.voiceprint_set_threshold(0.8)
        assert json.loads(raw)["match_threshold"] == 0.8
        assert matcher.global_threshold() == 0.8
        raw = await tools_mod.voiceprint_set_threshold(1.5)
        assert "error" in json.loads(raw)

    async def test_speaker_list_and_stats(self, tool_store: VoiceprintStore) -> None:
        await matcher.enroll(tool_store, "张三", vec(0))
        body = json.loads(await tools_mod.speaker_list())
        assert body["total"] == 1
        stats = json.loads(await tools_mod.voiceprint_stats())
        assert stats["speakers"] == 1
