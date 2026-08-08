"""声纹匹配引擎与 ingest 管线测试：阈值判定/临时 ID/样本累积/身份合并/入库。"""

from __future__ import annotations

import math

import pytest

from entities.voiceprint import matcher
from entities.voiceprint.ingest import ingest_payload
from entities.voiceprint.schemas import IngestPayload, SegmentIn
from entities.voiceprint.store import VoiceprintStore


def vec(dim: int) -> list[float]:
    return [1.0 if i == dim else 0.0 for i in range(192)]


def tilted(dim: int, cosine: float) -> list[float]:
    """构造与 vec(dim) 余弦相似度为 cosine 的向量。"""
    v = vec(dim + 1)
    v[dim] = cosine
    v[dim + 1] = math.sqrt(1 - cosine * cosine)
    return v


@pytest.fixture
async def store(tmp_path):
    s = VoiceprintStore(str(tmp_path / "voiceprints.sqlite3"))
    yield s
    await s.close()


class TestMatchAndIdentify:
    async def test_enroll_and_match_known(self, store: VoiceprintStore) -> None:
        s = await matcher.enroll(store, "张三", vec(0), role="家人")
        candidates = await matcher.match_vector(store, tilted(0, 0.9))
        assert candidates[0]["id"] == s["id"]
        assert candidates[0]["matched"] is True
        assert candidates[0]["similarity"] == pytest.approx(0.9, abs=1e-3)

    async def test_identify_known_accumulates(self, store: VoiceprintStore) -> None:
        s = await matcher.enroll(store, "张三", vec(0))
        result = await matcher.identify(store, tilted(0, 0.95), audio_ms=3000)
        assert result["is_new"] is False
        assert result["speaker"]["id"] == s["id"]
        # 自动累积样本 + 命中回写
        assert len(await store.list_samples(s["id"])) == 2
        speaker = await store.get_speaker(s["id"])
        assert speaker is not None
        assert speaker["match_count"] == 1
        assert speaker["total_audio_ms"] == 3000

    async def test_identify_unknown_creates_pending(self, store: VoiceprintStore) -> None:
        await matcher.enroll(store, "张三", vec(0))
        result = await matcher.identify(store, vec(5))
        assert result["is_new"] is True
        speaker = result["speaker"]
        assert speaker is not None
        assert speaker["status"] == "pending"
        assert speaker["speaker_key"].startswith("spk_tmp_")
        # 新人样本已入池
        assert len(await store.list_samples(speaker["id"])) == 1

    async def test_per_speaker_threshold_overrides_global(self, store: VoiceprintStore) -> None:
        s = await matcher.enroll(store, "张三", vec(0))
        await store.update_speaker(s["id"], threshold=0.95)
        # 0.9 ≥ 全局 0.75 但 < 独立 0.95 → 判为新人
        result = await matcher.identify(store, tilted(0, 0.9))
        assert result["is_new"] is True
        candidates = result["candidates"]
        assert candidates[0]["matched"] is False
        assert candidates[0]["threshold"] == 0.95

    async def test_match_topk_candidates(self, store: VoiceprintStore) -> None:
        await matcher.enroll(store, "张三", vec(0))
        await matcher.enroll(store, "李四", vec(1))
        candidates = await matcher.match_vector(store, tilted(0, 0.8), top_k=5)
        assert len(candidates) == 2
        assert candidates[0]["name"] == "张三"
        # 正交向量相似度为 0，李四未达标
        assert candidates[1]["matched"] is False


class TestConfirmAndMerge:
    async def test_confirm_pending(self, store: VoiceprintStore) -> None:
        created = await matcher.identify(store, vec(3))
        tmp = created["speaker"]
        confirmed = await matcher.confirm(store, tmp["id"], "王五", role="朋友")
        assert confirmed is not None
        assert confirmed["name"] == "王五"
        assert confirmed["status"] == "confirmed"
        assert confirmed["speaker_key"].startswith("spk_")

    async def test_merge_fuses_pool_and_segments(self, store: VoiceprintStore) -> None:
        target = await matcher.enroll(store, "张三", vec(0))
        tmp = (await matcher.identify(store, vec(3)))["speaker"]
        seg_id = await store.add_segment(speaker_id=tmp["id"], transcript="我是张三")
        result = await matcher.merge(store, tmp["id"], target["id"])
        assert result["samples_moved"] == 1
        assert result["target"]["id"] == target["id"]
        # 源档案删除，片段重指向，样本池融合
        assert await store.get_speaker(tmp["id"]) is None
        seg = await store.get_segment(seg_id)
        assert seg is not None and seg["speaker_id"] == target["id"]
        assert len(await store.list_samples(target["id"])) == 2

    async def test_merge_same_id_rejected(self, store: VoiceprintStore) -> None:
        s = await matcher.enroll(store, "张三", vec(0))
        with pytest.raises(ValueError):
            await matcher.merge(store, s["id"], s["id"])


class TestIngest:
    async def test_ingest_known_and_new(self, store: VoiceprintStore) -> None:
        s = await matcher.enroll(store, "张三", vec(0))
        payload = IngestPayload(
            source_file="/nas/a.wav",
            device_source="客厅麦克风",
            ts=1_785_988_800,
            segments=[
                SegmentIn(start_ms=0, end_ms=3000, text="今天天气不错", vector=tilted(0, 0.95)),
                SegmentIn(start_ms=3000, end_ms=6000, text="你是谁", vector=vec(7)),
                SegmentIn(start_ms=6000, end_ms=7000, text="无声纹段", vector=None),
            ],
        )
        result = await ingest_payload(payload, store=store)
        assert result.ingested == 3
        known, new, no_vec = result.results
        assert known.speaker_id == s["id"] and known.is_new_speaker is False
        assert known.similarity == pytest.approx(0.95, abs=1e-3)
        assert new.is_new_speaker is True and new.speaker_key.startswith("spk_tmp_")
        # 默认挂接（voiceprint_attach_unidentified）：无声纹段归入前一段的说话人
        assert no_vec.speaker_id == new.speaker_id
        seg_no_vec = await store.get_segment(no_vec.segment_id)
        assert seg_no_vec is not None and seg_no_vec["speaker_id"] == new.speaker_id
        # 片段落库 + 未读计数 + 说话人时长累计
        assert await store.unread_count() == 3
        speaker = await store.get_speaker(s["id"])
        assert speaker is not None and speaker["total_audio_ms"] == 3000
        segments = await store.list_segments(limit=10)
        assert segments["total"] == 3
        # 转写文本可被检索
        hits = await store.search_segments("天气")
        assert len(hits) == 1

    async def test_ingest_empty_payload(self, store: VoiceprintStore) -> None:
        result = await ingest_payload(IngestPayload(), store=store)
        assert result.ingested == 0
        assert result.results == []

    async def test_abs_time_overrides_base_offset(self, store: VoiceprintStore) -> None:
        """FunASR 回传的 abs_start_ms（source_time 换算）优先于 基准+偏移。"""
        abs_ms = 1_786_005_000_000  # 录制当天的绝对时刻（epoch 毫秒）
        payload = IngestPayload(
            source_file="/nas/a.wav",
            ts=1_785_988_800,  # 处理时刻（与录制时刻不同）
            segments=[
                SegmentIn(start_ms=5000, end_ms=8000, text="带绝对时间",
                          vector=vec(0), abs_start_ms=abs_ms, abs_end_ms=abs_ms + 3000),
                SegmentIn(start_ms=9000, end_ms=10000, text="旧契约", vector=vec(0)),
            ],
        )
        result = await ingest_payload(payload, store=store)
        assert result.ingested == 2
        segs = await store.list_segments(limit=10)
        by_text = {s["transcript"]: s["ts_ns"] for s in segs["items"]}
        # 绝对时间直接采用（不被处理时刻污染）
        assert by_text["带绝对时间"] == abs_ms * 1_000_000
        # 旧契约回退 基准 + 段内偏移
        assert by_text["旧契约"] == 1_785_988_800_000_000_000 + 9000 * 1_000_000


class TestIngestQualityGate:
    async def test_noise_segments_skipped(self, store: VoiceprintStore) -> None:
        """纯标点/空白段：跳过不建档不计片段。"""
        from entities.voiceprint.ingest import _is_noise_text
        assert _is_noise_text("，") and _is_noise_text("。 ")
        assert _is_noise_text("...?!") and _is_noise_text("  ")
        assert not _is_noise_text("你好") and not _is_noise_text("嗯。")

        result = await ingest_payload(IngestPayload(
            source_file="/nas/a.wav",
            segments=[
                SegmentIn(start_ms=0, end_ms=500, text="，", vector=vec(0)),
                SegmentIn(start_ms=500, end_ms=3500, text="正经内容", vector=vec(0)),
            ],
        ), store=store)
        assert result.skipped == 1
        assert result.ingested == 1
        assert result.results[0].is_new_speaker is True
        speakers = await store.list_speakers()
        assert speakers["total"] == 1  # 噪音段没有建第二个说话人

    async def test_short_segments_not_enrolled(self, store: VoiceprintStore) -> None:
        """短于 min_voiceprint_ms 的段：不匹配不建档（防过度分裂）；
        关闭挂接配置时归属为未知。"""
        from core.config import ConfigManager
        ConfigManager.set("voiceprint_attach_unidentified", False)
        result = await ingest_payload(IngestPayload(
            source_file="/nas/a.wav",
            segments=[
                SegmentIn(start_ms=0, end_ms=800, text="嗯", vector=vec(0)),       # 0.8s 短段
                SegmentIn(start_ms=800, end_ms=4800, text="完整的一句话内容", vector=vec(1)),
            ],
        ), store=store)
        assert result.ingested == 2
        short, normal = result.results
        assert short.speaker_id is None  # 短段未识别（挂接已关）
        assert normal.is_new_speaker is True
        speakers = await store.list_speakers()
        assert speakers["total"] == 1  # 短段没建档
        segs = await store.list_segments(limit=10)
        by_text = {s["transcript"]: s for s in segs["items"]}
        assert by_text["嗯"]["transcript"] == "嗯"  # 文本仍留存可检索
        assert by_text["嗯"]["speaker_id"] is None

    async def test_attach_unidentified_default_on(self, store: VoiceprintStore) -> None:
        """默认挂接：短段/无声纹段归入同录制最近的已归属段（不采样不建档）。"""
        result = await ingest_payload(IngestPayload(
            source_file="/nas/a.wav", recording_path="/nas/rec1",
            segments=[
                SegmentIn(start_ms=0, end_ms=1000, text="前置短段", vector=None),  # 无向量
                SegmentIn(start_ms=1000, end_ms=5000, text="长段", vector=vec(0)),
                SegmentIn(start_ms=5000, end_ms=6000, text="对", vector=vec(1)),  # 1s 短段
            ],
        ), store=store)
        first, long_seg, short_seg = result.results
        assert long_seg.speaker_id is not None
        # 前置无向量段挂到后面最近的已归属段；短段挂到前一段
        assert first.speaker_id == long_seg.speaker_id
        assert short_seg.speaker_id == long_seg.speaker_id
        speakers = await store.list_speakers()
        assert speakers["total"] == 1  # 都没新建档
        # 挂接不采样：池中只有长段识别产生的 1 条样本
        assert len(await store.list_samples(long_seg.speaker_id)) == 1


class TestPrunePending:
    async def test_prune_only_empty_pending(self, store: VoiceprintStore) -> None:
        """只清理无样本的临时档案；有样本的 pending 与 confirmed 保留。"""
        s1 = await store.create_speaker(status="pending")  # 无样本 → 清理
        tmp = (await matcher.identify(store, vec(0)))["speaker"]  # 有样本 → 保留
        confirmed = await matcher.enroll(store, "张三", vec(1))   # confirmed → 保留
        deleted = await store.prune_pending_speakers()
        assert [d["id"] for d in deleted] == [s1["id"]]
        assert await store.get_speaker(tmp["id"]) is not None
        assert await store.get_speaker(confirmed["id"]) is not None

    async def test_prune_all_pending_with_samples(self, store: VoiceprintStore) -> None:
        """include_with_samples=True：级联剔除全部 pending（样本+片段一并删除），
        confirmed 保留。"""
        tmp = (await matcher.identify(store, vec(0)))["speaker"]  # pending 有样本
        seg_id = await store.add_segment(speaker_id=tmp["id"], transcript="片段")
        confirmed = await matcher.enroll(store, "张三", vec(1))
        deleted = await store.prune_pending_speakers(include_with_samples=True)
        assert [d["id"] for d in deleted] == [tmp["id"]]
        assert await store.get_speaker(tmp["id"]) is None
        assert await store.list_samples(tmp["id"]) == []
        assert await store.get_segment(seg_id) is None  # 片段级联删除
        assert await store.get_speaker(confirmed["id"]) is not None
