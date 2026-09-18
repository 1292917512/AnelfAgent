"""人脸匹配引擎测试：阈值判定 / 临时建档 / 样本累积 / 分离度门 / 合并 / 重建。"""

from __future__ import annotations

import math

import numpy as np
import pytest

from agent.vision.face import matcher
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


class TestMatchAndIdentify:
    async def test_enroll_and_match_known(self, store: FaceStore) -> None:
        p = await matcher.enroll(store, "张三", vec(0), role="家人", det_score=0.9)
        candidates = await matcher.match_vector(store, tilted(0, 0.9))
        assert candidates[0]["id"] == p["id"]
        assert candidates[0]["matched"] is True
        assert candidates[0]["similarity"] == pytest.approx(0.9, abs=1e-3)

    async def test_identify_known_accumulates(self, store: FaceStore) -> None:
        p = await matcher.enroll(store, "张三", vec(0), det_score=0.9)
        result = await matcher.identify(store, tilted(0, 0.85), det_score=0.9)
        assert result["is_new"] is False
        assert result["person"]["id"] == p["id"]
        assert result["sample_added"] is True

    async def test_identify_unknown_creates_pending(self, store: FaceStore) -> None:
        await matcher.enroll(store, "张三", vec(0), det_score=0.9)
        result = await matcher.identify(store, vec(200), det_score=0.9)
        assert result["is_new"] is True
        assert result["person"]["status"] == "pending"
        assert result["person"]["person_key"].startswith("fc_tmp")

    async def test_identify_no_auto_create(self, store: FaceStore) -> None:
        await matcher.enroll(store, "张三", vec(0), det_score=0.9)
        result = await matcher.identify(store, vec(200), det_score=0.9, auto_create=False)
        assert result["is_new"] is True
        assert result["person"] is None

    async def test_below_threshold_not_matched(self, store: FaceStore) -> None:
        await matcher.enroll(store, "张三", vec(0), det_score=0.9)
        candidates = await matcher.match_vector(store, tilted(0, 0.2))
        assert candidates[0]["matched"] is False


class TestSeparationGate:
    def test_cohort_separation_none_when_small(self) -> None:
        sims = np.array([0.9, 0.3, 0.2])
        # cohort 仅 2 人（<3）→ 门不启用
        assert matcher._cohort_separation(0, sims) is None

    def test_cohort_separation_zscore(self) -> None:
        # 4 个冒充者得分集中在 0.2 附近，候选 0.9 → z 很大
        sims = np.array([0.9, 0.2, 0.21, 0.19, 0.2])
        z = matcher._cohort_separation(0, sims)
        assert z is not None and z > 2.0

    async def test_ambiguous_query_degraded(self, store: FaceStore, monkeypatch) -> None:
        """4 个都很像的档案 + 强分离门：模糊查询降级为不认亲。"""
        monkeypatch.setattr(matcher, "separation_floor", lambda: 5.0)
        # 建 4 个彼此接近的档案（都靠近 vec(0)）
        for i in range(4):
            await store.create_person(name=f"P{i}")
            await store.add_sample(1 + i, tilted(0, 0.95 - i * 0.01), det_score=0.9)
        # 查询与所有人相似度都高，但分离度不足 → 无人 matched
        candidates = await matcher.match_vector(store, tilted(0, 0.9))
        assert all(c["matched"] is False for c in candidates)


class TestMergeRefineCompare:
    async def test_merge_combines(self, store: FaceStore) -> None:
        a = await matcher.enroll(store, "A", vec(0), det_score=0.9)
        b = await matcher.enroll(store, "B", tilted(0, 0.8), det_score=0.9)
        result = await matcher.merge(store, int(b["id"]), int(a["id"]))
        assert result["target"]["id"] == a["id"]
        assert result["samples_moved"] >= 1
        assert await store.get_person(int(b["id"])) is None

    async def test_merge_same_raises(self, store: FaceStore) -> None:
        a = await matcher.enroll(store, "A", vec(0), det_score=0.9)
        with pytest.raises(ValueError):
            await matcher.merge(store, int(a["id"]), int(a["id"]))

    async def test_refine_rebuilds_anchor(self, store: FaceStore) -> None:
        p = await matcher.enroll(store, "A", vec(0), det_score=0.9)
        await store.add_sample(int(p["id"]), tilted(0, 0.9), det_score=0.9)
        result = await matcher.refine(store, int(p["id"]))
        assert result["samples"] >= 1

    async def test_refine_empty_raises(self, store: FaceStore) -> None:
        p = await store.create_person(name="空")
        with pytest.raises(ValueError):
            await matcher.refine(store, int(p["id"]))

    async def test_compare_similarity(self, store: FaceStore) -> None:
        a = await matcher.enroll(store, "A", vec(0), det_score=0.9)
        b = await matcher.enroll(store, "B", tilted(0, 0.85), det_score=0.9)
        result = await matcher.compare(store, int(a["id"]), int(b["id"]))
        assert result["anchor_similarity"] is not None
        assert "merge_hint" in result


class TestEnrollSamples:
    async def test_enroll_samples_one_profile(self, store: FaceStore) -> None:
        detections = [
            (vec(0), 0.9, [0, 0, 100, 100], {"yaw": 0}),
            (tilted(0, 0.9), 0.85, [0, 0, 100, 100], {"yaw": 5}),
        ]
        result = await matcher.enroll_samples(store, "张三", detections)
        assert result["samples_enrolled"] == 2
        samples = await store.list_samples(int(result["person"]["id"]))
        assert len(samples) == 2
