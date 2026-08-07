"""说话人相似度合并（consolidate）测试：质心聚类 / 保留最长时长 / dry_run。"""

from __future__ import annotations

import pytest

from entities.voiceprint import matcher
from entities.voiceprint.consolidate import consolidate, find_merge_clusters
from entities.voiceprint.store import VoiceprintStore


def vec(dim: int) -> list[float]:
    return [1.0 if i == dim else 0.0 for i in range(192)]


def near(dim: int, noise: float = 0.01) -> list[float]:
    """与 vec(dim) 高度相似的向量（cosine ≈ 0.9999）。"""
    v = vec(dim)
    v[dim] = 1.0 - noise
    v[(dim + 1) % 192] = noise ** 0.5
    return v


@pytest.fixture
async def store(tmp_path):
    s = VoiceprintStore(str(tmp_path / "voiceprints.sqlite3"))
    yield s
    await s.close()


async def _mk_pending(store: VoiceprintStore, vector: list[float], audio_ms: int) -> dict:
    speaker = await store.create_speaker(status="pending")
    await store.add_sample(speaker["id"], vector)
    await store.touch_speaker_match(speaker["id"], audio_ms, 1000)
    return speaker


class TestClusters:
    async def test_similar_pending_clustered(self, store: VoiceprintStore) -> None:
        """两个质心高度相似的临时说话人被聚为一簇，正交的各成一坑。"""
        a = await _mk_pending(store, vec(0), 5000)
        b = await _mk_pending(store, near(0), 3000)   # 与 a 同人分裂
        await _mk_pending(store, vec(5), 4000)        # 不同人
        clusters = await find_merge_clusters(store)
        assert len(clusters) == 1
        members = {m["id"] for m in clusters[0]["members"]}
        assert members == {a["id"], b["id"]}
        # 时长最长者保留
        assert clusters[0]["keep_id"] == a["id"]

    async def test_below_threshold_not_clustered(self, store: VoiceprintStore) -> None:
        await _mk_pending(store, vec(0), 1000)
        await _mk_pending(store, vec(1), 1000)
        assert await find_merge_clusters(store) == []

    async def test_dry_run_then_execute(self, store: VoiceprintStore) -> None:
        a = await _mk_pending(store, vec(0), 5000)
        b = await _mk_pending(store, near(0), 3000)
        preview = await consolidate(store, dry_run=True)
        assert preview["cluster_count"] == 1
        assert preview["merges"] == []  # 预览不执行
        assert await store.get_speaker(b["id"]) is not None

        result = await consolidate(store, dry_run=False)
        assert len(result["merges"]) == 1
        assert result["merges"][0]["samples_moved"] == 1
        # b 已并入 a，b 档案删除
        assert await store.get_speaker(b["id"]) is None
        merged = await store.get_speaker(a["id"])
        assert merged is not None
        assert merged["total_audio_ms"] == 8000  # 时长累加
        assert len(await store.list_samples(a["id"])) == 2  # 样本池融合

    async def test_confirmed_excluded_by_default(self, store: VoiceprintStore) -> None:
        confirmed = await matcher.enroll(store, "张三", vec(0))
        await _mk_pending(store, near(0), 1000)
        # 默认只整理 pending：confirmed 与 pending 不聚类
        assert await find_merge_clusters(store) == []
        # include confirmed（status=""）后二者成一簇，confirmed 时长不明时按时长保留
        clusters = await find_merge_clusters(store, status="")
        assert len(clusters) == 1
        assert any(m["id"] == confirmed["id"] for m in clusters[0]["members"])
