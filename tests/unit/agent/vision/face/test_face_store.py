"""FaceStore 测试：人物 CRUD / 实体绑定 / 样本池相干门与锚折叠 / 出现事件。"""

from __future__ import annotations

import math

import pytest

from agent.vision.face.store import FaceStore

DIM = 512


def vec(dim: int) -> list[float]:
    return [1.0 if i == dim else 0.0 for i in range(DIM)]


def tilted(dim: int, cosine: float) -> list[float]:
    """构造与 vec(dim) 余弦相似度为 cosine 的单位向量。"""
    v = vec(dim + 1)
    v[dim] = cosine
    v[dim + 1] = math.sqrt(max(0.0, 1 - cosine * cosine))
    return v


@pytest.fixture
async def store(tmp_path):
    s = FaceStore(str(tmp_path / "face.sqlite3"))
    await s.initialize()
    yield s
    await s.close()


class TestVolumeRegistration:
    def test_face_volume_registered(self) -> None:
        from core.storage_volume import get_volume_registry
        reg = get_volume_registry()
        assert "face" in {d.volume_id for d in reg.list()}


class TestPersonCrud:
    async def test_create_and_get(self, store: FaceStore) -> None:
        p = await store.create_person(name="张三", role="家人")
        assert p["person_key"].startswith("fc_")
        assert p["status"] == "confirmed"
        got = await store.get_person(int(p["id"]))
        assert got is not None and got["name"] == "张三"

    async def test_pending_key_prefix(self, store: FaceStore) -> None:
        p = await store.create_person(status="pending")
        assert p["person_key"].startswith("fc_tmp")

    async def test_confirm_rekeys(self, store: FaceStore) -> None:
        p = await store.create_person(status="pending")
        updated = await store.update_person(int(p["id"]), name="李四", status="confirmed")
        assert updated is not None
        assert updated["person_key"].startswith("fc_")
        assert not updated["person_key"].startswith("fc_tmp")

    async def test_find_by_name_and_key(self, store: FaceStore) -> None:
        p = await store.create_person(name="王五")
        assert await store.find_persons("王五")
        assert await store.find_persons(p["person_key"])
        assert await store.find_persons(str(p["id"]))

    async def test_bind_entity_validates_prefix(self, store: FaceStore) -> None:
        p = await store.create_person(name="赵六")
        with pytest.raises(ValueError):
            await store.bind_entity(int(p["id"]), "bogus:x")
        bound = await store.bind_entity(int(p["id"]), "user:qq:456")
        assert bound is not None and bound["entity_scope"] == "user:qq:456"
        back = await store.persons_for_entity("user:qq:456")
        assert any(x["id"] == p["id"] for x in back)

    async def test_delete_cascades_samples(self, store: FaceStore) -> None:
        p = await store.create_person(name="临时")
        await store.add_sample(int(p["id"]), vec(0), det_score=0.9)
        assert await store.list_samples(int(p["id"]))
        await store.delete_person(int(p["id"]))
        assert await store.get_person(int(p["id"])) is None
        assert await store.list_samples(int(p["id"])) == []


class TestSamplePool:
    async def test_first_sample_sets_anchor(self, store: FaceStore) -> None:
        p = await store.create_person(name="A")
        sid = await store.add_sample(int(p["id"]), vec(0), det_score=0.9)
        assert sid > 0
        anchor, weight = await store.get_person_anchor(int(p["id"]))
        assert anchor and weight > 0

    async def test_coherence_gate_rejects_poison(self, store: FaceStore, monkeypatch) -> None:
        monkeypatch.setattr("agent.vision.face.store.coherence_floor", lambda: 0.5)
        p = await store.create_person(name="A")
        await store.add_sample(int(p["id"]), vec(0), det_score=0.9)
        # 正交向量（余弦 0）低于相干门 0.5 → 拒入
        rejected = await store.add_sample(int(p["id"]), vec(100), det_score=0.9)
        assert rejected == -1

    async def test_pool_cap_evicts(self, store: FaceStore, monkeypatch) -> None:
        monkeypatch.setattr(
            "agent.vision.face.store.get_config", lambda k, d=10: 3)
        p = await store.create_person(name="A")
        for i in range(5):
            await store.add_sample(int(p["id"]), tilted(0, 0.9 - i * 0.01),
                                   det_score=0.9, source="chat")
        samples = await store.list_samples(int(p["id"]))
        assert len(samples) == 3


class TestEvents:
    async def test_add_and_filter_by_person(self, store: FaceStore) -> None:
        p = await store.create_person(name="A")
        eid = await store.add_event(
            image_path="/tmp/x.jpg", source="inbound",
            faces=[{"person_id": p["id"], "person_name": "A"}],
            person_ids=[int(p["id"])])
        assert eid > 0
        result = await store.list_events(person_id=int(p["id"]))
        assert result["total"] == 1
        assert result["items"][0]["source"] == "inbound"

    async def test_unread_and_mark_read(self, store: FaceStore) -> None:
        await store.add_event(image_path="/tmp/y.jpg", source="web", faces=[])
        assert await store.unread_count() == 1
        await store.mark_read(None)
        assert await store.unread_count() == 0

    async def test_retention_prune(self, store: FaceStore, monkeypatch) -> None:
        import time
        # 添加时保留期禁用（0=不限），旧事件得以留存（add_event 写后自动清理不触发）
        monkeypatch.setattr(
            "agent.vision.face.store.get_config_int", lambda k, d=30: 0)
        old = time.time_ns() - 5 * 86400 * 1_000_000_000
        await store.add_event(image_path="/tmp/old.jpg", faces=[], ts_ns=old)
        assert (await store.list_events())["total"] == 1
        # 启用 1 天保留期后清理
        monkeypatch.setattr(
            "agent.vision.face.store.get_config_int", lambda k, d=30: 1)
        pruned = await store.prune_expired_events()
        assert pruned == 1


class TestStatsSummary:
    async def test_stats_and_summary(self, store: FaceStore) -> None:
        await store.create_person(name="张三")
        p = await store.create_person(status="pending")
        await store.bind_entity(int(p["id"]), "user:qq:1")
        stats = await store.stats()
        assert stats["persons"] == 2
        assert stats["pending_persons"] == 1
        summary = await store.summary()
        assert "张三" in summary["confirmed_names"]


class TestImageGc:
    async def test_delete_event_gc_face_copy(
        self, store: FaceStore, tmp_path, monkeypatch,
    ) -> None:
        face_dir = tmp_path / "uploads_face"
        face_dir.mkdir()
        monkeypatch.setattr("agent.vision.face.store.face_upload_root",
                            lambda: str(face_dir))
        img = face_dir / "copy.jpg"
        img.write_bytes(b"x")
        eid = await store.add_event(image_path=str(img), faces=[])
        assert await store.delete_event(eid) is True
        assert not img.exists()  # 孤立副本已回收

    async def test_gc_leaves_referenced_and_outside(
        self, store: FaceStore, tmp_path, monkeypatch,
    ) -> None:
        face_dir = tmp_path / "uploads_face"
        face_dir.mkdir()
        monkeypatch.setattr("agent.vision.face.store.face_upload_root",
                            lambda: str(face_dir))
        img = face_dir / "kept.jpg"
        img.write_bytes(b"x")
        await store.add_event(image_path=str(img), faces=[])
        assert await store.gc_image(str(img)) is False  # 仍被引用
        assert img.exists()
        outside = tmp_path / "orig.jpg"
        outside.write_bytes(b"x")
        assert await store.gc_image(str(outside)) is False  # 目录外不碰
        assert outside.exists()
