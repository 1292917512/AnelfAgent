"""核心音频路由测试（/api/audio）：说话人 API / 实体绑定 / 识别 / 片段 / 统计。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.audio import matcher


@pytest.fixture
def funasr_cred(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """FunASR 地址写入隔离的凭据中心存储（不碰真凭据文件）。"""
    from core import provider_keys as pk

    monkeypatch.setattr(pk, "_path", lambda: str(tmp_path / "keys.json"))
    monkeypatch.setattr(pk, "_cache", None)

    def _set(value: str) -> None:
        pk.set_provider_key("funasr", "funasr_endpoint", value)

    return _set
from web.routers.audio import router as audio_router


def vec(dim: int) -> list[float]:
    return [1.0 if i == dim else 0.0 for i in range(192)]


@pytest.fixture
def client():
    """挂载核心音频路由（库走 conftest 隔离的核心库单例）。"""
    app = FastAPI()
    app.include_router(audio_router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


class TestSpeakerApi:
    async def test_crud_flow(self, client: TestClient) -> None:
        # 注册
        resp = client.post("/api/audio/speakers", json={
            "name": "张三", "vector": vec(0), "role": "家人",
        })
        assert resp.status_code == 200
        speaker = resp.json()
        assert speaker["speaker_key"].startswith("spk_")

        # 列表 / 详情
        resp = client.get("/api/audio/speakers", params={"keyword": "张三"})
        assert resp.json()["total"] == 1
        resp = client.get(f"/api/audio/speakers/{speaker['id']}")
        assert resp.status_code == 200
        assert resp.json()["samples"][0]["dims"] == 192

        # 编辑（独立阈值）
        resp = client.patch(
            f"/api/audio/speakers/{speaker['id']}", json={"threshold": 0.8})
        assert resp.json()["speaker"]["threshold"] == 0.8

        # 向量识别（完全同向量 → 命中）
        resp = client.post("/api/audio/identify", json={"vector": vec(0)})
        candidates = resp.json()
        assert candidates[0]["matched"] is True
        assert candidates[0]["name"] == "张三"

        # 删除
        resp = client.delete(f"/api/audio/speakers/{speaker['id']}")
        assert resp.status_code == 200
        assert client.get("/api/audio/speakers").json()["total"] == 0

    async def test_confirm_and_merge_api(self, client: TestClient, _isolate_audio_library) -> None:
        store = _isolate_audio_library
        target = await matcher.enroll(store, "张三", vec(0))
        tmp = (await matcher.identify(store, vec(5)))["speaker"]

        resp = client.post(
            f"/api/audio/speakers/{tmp['id']}/confirm", json={"name": "王五"})
        assert resp.status_code == 200
        assert resp.json()["speaker"]["status"] == "confirmed"

        resp = client.post("/api/audio/speakers/merge", json={
            "source_id": tmp["id"], "target_id": target["id"],
        })
        assert resp.status_code == 200
        assert resp.json()["samples_moved"] == 1

    def test_404_on_missing_speaker(self, client: TestClient) -> None:
        assert client.get("/api/audio/speakers/999").status_code == 404
        assert client.delete("/api/audio/speakers/999").status_code == 404


class TestEntityBindingApi:
    async def test_bind_and_reverse_lookup(self, client: TestClient, _isolate_audio_library) -> None:
        store = _isolate_audio_library
        speaker = await matcher.enroll(store, "张三", vec(0))

        resp = client.post(
            f"/api/audio/speakers/{speaker['id']}/bind",
            json={"entity_scope": "user:webui:u1"})
        assert resp.status_code == 200
        assert resp.json()["speaker"]["entity_scope"] == "user:webui:u1"

        resp = client.get("/api/audio/speakers/by-entity/user:webui:u1")
        assert resp.status_code == 200
        assert [s["id"] for s in resp.json()["speakers"]] == [speaker["id"]]

        # 非法 scope → 400；解绑 → 清空
        resp = client.post(
            f"/api/audio/speakers/{speaker['id']}/bind",
            json={"entity_scope": "bogus"})
        assert resp.status_code == 400
        resp = client.post(
            f"/api/audio/speakers/{speaker['id']}/bind", json={"entity_scope": ""})
        assert resp.json()["speaker"]["entity_scope"] == ""


class TestSegmentApi:
    async def test_query_and_reassign(self, client: TestClient, _isolate_audio_library) -> None:
        store = _isolate_audio_library
        s = await matcher.enroll(store, "张三", vec(0))
        seg_id = await store.add_segment(
            speaker_id=s["id"], transcript="今晚一起吃饭", ts_ns=1_785_988_800_000_000_000)

        resp = client.get("/api/audio/segments", params={"q": "吃饭"})
        assert resp.status_code == 200
        assert resp.json()["items"][0]["speaker_name"] == "张三"

        resp = client.get("/api/audio/segments", params={"speaker_id": s["id"]})
        assert resp.json()["total"] == 1

        resp = client.patch(
            f"/api/audio/segments/{seg_id}", json={"speaker_id": None})
        assert resp.status_code == 200
        assert resp.json()["segment"]["speaker_id"] is None

        resp = client.post("/api/audio/segments/mark-read", json=None)
        assert resp.json()["marked_read"] == 1

    async def test_stats(self, client: TestClient, _isolate_audio_library) -> None:
        await matcher.enroll(_isolate_audio_library, "张三", vec(0))
        resp = client.get("/api/audio/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["speakers"] == 1
        assert body["match_threshold"] == 0.75


class TestRecordingsApi:
    async def test_list_and_delete(self, client: TestClient, _isolate_audio_library) -> None:
        store = _isolate_audio_library
        await store.mark_recording("/nas/a", kind="folder", started_ns=100)
        resp = client.get("/api/audio/recordings")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        resp = client.delete("/api/audio/recordings", params={"path": "/nas/a"})
        assert resp.status_code == 200
        assert await store.get_recording("/nas/a") is None

        resp = client.delete("/api/audio/recordings", params={"path": "/nas/a"})
        assert resp.status_code == 404


class TestErrorMapping:
    def test_enroll_audio_503_without_asr(
            self, client: TestClient, monkeypatch: pytest.MonkeyPatch, funasr_cred) -> None:
        """无可用 ASR 提供者时音频注册返回 503（而非 422 参数语义）。"""
        # 钉死百炼组件凭据（宿主机可能装有 dashscope SDK 且可解析真实 Key）
        from entities.dashscope import sdk as dashscope_sdk
        monkeypatch.setattr(dashscope_sdk, "resolve_api_key", lambda: "")
        funasr_cred("")
        resp = client.post(
            "/api/audio/enroll/audio",
            files={"file": ("a.wav", b"fake", "audio/wav")},
            data={"name": "张三"},
        )
        assert resp.status_code == 503

    def test_identify_audio_503_without_asr(
            self, client: TestClient, monkeypatch: pytest.MonkeyPatch, funasr_cred) -> None:
        from entities.dashscope import sdk as dashscope_sdk
        monkeypatch.setattr(dashscope_sdk, "resolve_api_key", lambda: "")
        funasr_cred("")
        resp = client.post(
            "/api/audio/identify/audio",
            files={"file": ("a.wav", b"fake", "audio/wav")},
        )
        assert resp.status_code == 503


class TestFunasrStatus:
    def test_funasr_status_shape(self, client: TestClient, monkeypatch, funasr_cred) -> None:
        async def fake_probe() -> bool:
            return True

        from entities.audiosync import client as funasr_client
        monkeypatch.setattr(funasr_client, "probe_available", fake_probe)
        funasr_cred("http://funasr.local")
        resp = client.get("/api/audio/funasr/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"configured": True, "reachable": True}
        funasr_cred("")


class TestVoicePresetApi:
    @pytest.fixture(autouse=True)
    def preset_env(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        """音色预设库与指派键隔离到临时域。"""
        from agent.tts import presets as tts_presets
        from core.config import ConfigManager

        monkeypatch.setattr(
            tts_presets, "_store_path", lambda: str(tmp_path / "voice_presets.json"))
        store: dict = {}
        monkeypatch.setattr(
            ConfigManager, "get", staticmethod(lambda k, d=None: store.get(k, d)))
        monkeypatch.setattr(
            ConfigManager, "set", staticmethod(lambda k, v: store.__setitem__(k, v)))
        monkeypatch.setattr(ConfigManager, "save", staticmethod(lambda: True))

    def test_crud_and_assign_flow(self, client: TestClient) -> None:
        resp = client.get("/api/audio/voice-presets")
        assert resp.status_code == 200
        assert resp.json()["presets"] == []

        resp = client.post("/api/audio/voice-presets", json={
            "name": "御姐", "voice_id": "female-yujie", "note": "沉稳",
        })
        assert resp.status_code == 200
        preset_id = resp.json()["id"]
        assert preset_id.startswith("vp_")

        resp = client.post("/api/audio/voice-presets/assign", json={
            "scene": "default", "preset_id": preset_id})
        assert resp.status_code == 200
        resp = client.get("/api/audio/voice-presets")
        assert resp.json()["assignments"]["default"] == preset_id

        # 被指派的预设拒绝删除；解除指派后可删
        resp = client.delete(f"/api/audio/voice-presets/{preset_id}")
        assert resp.status_code == 422
        client.post("/api/audio/voice-presets/assign",
                    json={"scene": "default", "preset_id": ""})
        resp = client.delete(f"/api/audio/voice-presets/{preset_id}")
        assert resp.status_code == 200

    def test_validation_422(self, client: TestClient) -> None:
        resp = client.post("/api/audio/voice-presets", json={
            "name": "x", "voice_id": "v",
            "reference_audio": "http://a/b.mp3", "reference_text": "t"})
        assert resp.status_code == 422
        resp = client.post("/api/audio/voice-presets/assign",
                           json={"scene": "night", "preset_id": ""})
        assert resp.status_code == 422


class TestSpeakerRefineApi:
    async def test_refine_flow(self, client: TestClient, _isolate_audio_library) -> None:
        resp = client.post("/api/audio/speakers", json={
            "name": "张三", "vector": vec(4)})
        speaker_id = resp.json()["id"]
        resp = client.post(f"/api/audio/speakers/{speaker_id}/refine")
        assert resp.status_code == 200
        assert resp.json()["samples"] == 1
        assert resp.json()["anchor_similarity"] is None

    async def test_refine_empty_pool_422(self, client: TestClient, _isolate_audio_library) -> None:
        speaker = await _isolate_audio_library.create_speaker(name="空池")
        resp = client.post(f"/api/audio/speakers/{speaker['id']}/refine")
        assert resp.status_code == 422
