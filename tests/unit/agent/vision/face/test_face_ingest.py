"""人脸入库管线测试：过滤 / 建档 / 命中 / fail-open（fake 引擎，无网络）。"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from agent.vision.face import ingest as ingest_mod
from agent.vision.face import matcher
from agent.vision.face.schemas import ExtractResult, FaceDetection, FacePose
from agent.vision.face.store import FaceStore

DIM = 512


def vec(dim: int) -> list[float]:
    return [1.0 if i == dim else 0.0 for i in range(DIM)]


def tilted(dim: int, cosine: float) -> list[float]:
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


def _fake_extract(faces):
    async def _f(path, *, min_det_score=0.0, max_faces=0):
        return ExtractResult(width=640, height=480, faces=faces)
    return _f


def _face(vector, *, bbox=(0, 0, 200, 200), det_score=0.9):
    return FaceDetection(bbox=list(bbox), det_score=det_score,
                         pose=FacePose(), vector=vector)


class TestIngest:
    async def test_skip_when_not_configured(self, store, monkeypatch) -> None:
        monkeypatch.setattr(ingest_mod.engine, "is_configured", lambda: False)
        r = await ingest_mod.ingest_image("/tmp/x.jpg", "manual", store=store)
        assert r.skipped and "未配置" in r.error

    async def test_single_face_creates_event_and_person(self, store, monkeypatch) -> None:
        monkeypatch.setattr(ingest_mod.engine, "is_configured", lambda: True)
        monkeypatch.setattr(ingest_mod.engine, "extract_faces",
                            _fake_extract([_face(vec(0))]))
        r = await ingest_mod.ingest_image("/tmp/x.jpg", "manual", store=store)
        assert not r.skipped
        assert r.faces_detected == 1
        assert r.event_id is not None
        assert len(r.hits) == 1
        assert r.hits[0].is_new  # 空库 → 新临时人物

    async def test_low_det_score_filtered(self, store, monkeypatch) -> None:
        monkeypatch.setattr(ingest_mod.engine, "is_configured", lambda: True)
        monkeypatch.setattr(ingest_mod.engine, "extract_faces",
                            _fake_extract([_face(vec(0), det_score=0.3)]))
        r = await ingest_mod.ingest_image("/tmp/x.jpg", "manual", store=store)
        assert r.faces_detected == 1
        assert r.faces_filtered == 1
        assert r.skipped  # 无合格脸

    async def test_small_face_filtered(self, store, monkeypatch) -> None:
        monkeypatch.setattr(ingest_mod.engine, "is_configured", lambda: True)
        monkeypatch.setattr(ingest_mod.engine, "extract_faces",
                            _fake_extract([_face(vec(0), bbox=(0, 0, 30, 30))]))
        r = await ingest_mod.ingest_image("/tmp/x.jpg", "manual", store=store)
        assert r.faces_filtered == 1
        assert r.skipped

    async def test_engine_error_failopen(self, store, monkeypatch) -> None:
        monkeypatch.setattr(ingest_mod.engine, "is_configured", lambda: True)

        async def boom(path, **kw):
            raise ingest_mod.engine.FaceEngineError("down", code="ENGINE_ERROR")

        monkeypatch.setattr(ingest_mod.engine, "extract_faces", boom)
        r = await ingest_mod.ingest_image("/tmp/x.jpg", "manual", store=store)
        assert r.skipped and r.error

    async def test_known_person_matched(self, store, monkeypatch) -> None:
        await matcher.enroll(store, "张三", vec(0), det_score=0.9)
        monkeypatch.setattr(ingest_mod.engine, "is_configured", lambda: True)
        monkeypatch.setattr(ingest_mod.engine, "extract_faces",
                            _fake_extract([_face(tilted(0, 0.85))]))
        r = await ingest_mod.ingest_image("/tmp/x.jpg", "manual", store=store)
        assert r.hits[0].matched
        assert r.hits[0].person_name == "张三"

    async def test_multi_face_event(self, store, monkeypatch) -> None:
        monkeypatch.setattr(ingest_mod.engine, "is_configured", lambda: True)
        monkeypatch.setattr(ingest_mod.engine, "extract_faces",
                            _fake_extract([_face(vec(0)), _face(vec(50))]))
        r = await ingest_mod.ingest_image("/tmp/x.jpg", "manual", store=store)
        assert r.faces_detected == 2
        assert len(r.hits) == 2
        event = await store.get_event(int(r.event_id))
        assert event is not None and event["faces_count"] == 2


class TestPersistImage:
    async def test_copies_outside_source_into_face_dir(
        self, store, tmp_path, monkeypatch,
    ) -> None:
        face_dir = tmp_path / "uploads_face"
        face_dir.mkdir()
        monkeypatch.setattr(ingest_mod, "face_upload_dir", lambda: face_dir)
        src = tmp_path / "photo.jpg"
        src.write_bytes(b"fakeimage")
        persisted = ingest_mod.persist_image(str(src))
        assert persisted.startswith(str(face_dir))
        assert Path(persisted).is_file()

    async def test_already_in_face_dir_is_noop(
        self, tmp_path, monkeypatch,
    ) -> None:
        face_dir = tmp_path / "uploads_face"
        face_dir.mkdir()
        monkeypatch.setattr(ingest_mod, "face_upload_dir", lambda: face_dir)
        src = face_dir / "x.jpg"
        src.write_bytes(b"fakeimage")
        assert ingest_mod.persist_image(str(src)) == str(src)

    async def test_ingest_stores_persisted_reference(
        self, store, tmp_path, monkeypatch,
    ) -> None:
        face_dir = tmp_path / "uploads_face"
        face_dir.mkdir()
        monkeypatch.setattr(ingest_mod, "face_upload_dir", lambda: face_dir)
        monkeypatch.setattr(ingest_mod.engine, "is_configured", lambda: True)
        src = tmp_path / "photo.jpg"
        src.write_bytes(b"fakeimage")
        monkeypatch.setattr(ingest_mod.engine, "extract_faces",
                            _fake_extract([_face(vec(0))]))
        r = await ingest_mod.ingest_image(str(src), "manual", store=store)
        assert r.image_path.startswith(str(face_dir))
        event = await store.get_event(int(r.event_id))
        assert event is not None
        assert event["image_path"].startswith(str(face_dir))
