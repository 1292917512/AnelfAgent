"""音频核心层测试：提供者注册表/优先级链、服务解析、入库管线、声纹锚。"""

from __future__ import annotations

import time

import pytest

from agent.audio import (
    KIND_ASR,
    KIND_VOICEPRINT,
    AudioNotConfigured,
    get_audio_registry,
    get_audio_service,
    matcher,
)
from agent.audio.store import AudioStore


def vec(dim: int) -> list[float]:
    """192 维单位向量基（dim 位为 1），向量间两两正交。"""
    v = [0.0] * 192
    v[dim] = 1.0
    return v


def tilted(dim: int, cosine: float) -> list[float]:
    """构造与 vec(dim) 余弦相似度为 cosine 的向量。"""
    import math
    v = vec(dim + 1)
    v[dim] = cosine
    v[dim + 1] = math.sqrt(1 - cosine * cosine)
    return v


class FakeAsr:
    def __init__(self, name: str, priority: int, available: bool = True):
        self.name = name
        self.kind = KIND_ASR
        self.priority = priority
        self._available = available
        self.calls = 0

    async def check_available(self) -> bool:
        return self._available

    async def transcribe(self, path: str, source_time: str = ""):
        self.calls += 1
        return [{"start_ms": 0, "end_ms": 3000, "text": f"by {self.name}",
                 "vector": None, "abs_start_ms": None, "abs_end_ms": None}]


class FakeVp:
    def __init__(self, name: str, priority: int):
        self.name = name
        self.kind = KIND_VOICEPRINT
        self.priority = priority

    async def check_available(self) -> bool:
        return True

    async def embed(self, path: str):
        return [0.1, 0.2, 0.3]


@pytest.fixture(autouse=True)
def clean_registry():
    """注册表隔离：快照既有提供者，用后恢复（reset 只清不恢复会饿死
    同进程内依赖导入期注册的提供者——如 FunASR 组件）的其他测试。"""
    saved = get_audio_registry().list()
    get_audio_registry().reset()
    yield
    get_audio_registry().reset()
    for p in saved:
        get_audio_registry().register(p)


@pytest.fixture
async def store(tmp_path):
    s = AudioStore(str(tmp_path / "audio.sqlite3"))
    await s.initialize()
    yield s
    await s.close()


class TestRegistry:
    async def test_resolve_by_priority(self):
        reg = get_audio_registry()
        low = FakeAsr("slow", 20)
        high = FakeAsr("fast", 10)
        reg.register(low)
        reg.register(high)
        assert await reg.resolve(KIND_ASR) is high

    async def test_unavailable_falls_back(self):
        reg = get_audio_registry()
        down = FakeAsr("down", 10, available=False)
        up = FakeAsr("up", 20)
        reg.register(down)
        reg.register(up)
        assert await reg.resolve(KIND_ASR) is up

    async def test_resolve_none_when_all_unavailable(self):
        reg = get_audio_registry()
        reg.register(FakeAsr("down", 10, available=False))
        assert await reg.resolve(KIND_ASR) is None

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValueError):
            get_audio_registry().register(type("X", (), {"name": "x", "kind": "bogus", "priority": 1})())

    def test_same_name_overwrites(self):
        reg = get_audio_registry()
        reg.register(FakeAsr("f", 10))
        reg.register(FakeAsr("f", 30))
        assert len(reg.list(KIND_ASR)) == 1


class TestService:
    async def test_transcribe_via_provider(self):
        get_audio_registry().register(FakeAsr("f", 10))
        segments = await get_audio_service().transcribe("/tmp/a.wav")
        assert segments[0]["text"] == "by f"

    async def test_transcribe_no_provider_raises(self):
        with pytest.raises(AudioNotConfigured):
            await get_audio_service().transcribe("/tmp/a.wav")

    async def test_embed_none_without_provider(self):
        assert await get_audio_service().speaker_embed("/tmp/a.wav") is None

    async def test_embed_via_provider(self):
        get_audio_registry().register(FakeVp("f", 10))
        assert await get_audio_service().speaker_embed("/tmp/a.wav") == [0.1, 0.2, 0.3]

    async def test_transcribe_and_store(self, store, monkeypatch):
        """转写 + 入库管线端到端（无声纹向量的段落仅留存文本）。"""
        import agent.audio.ingest as ingest_mod
        import agent.audio.service as svc_mod
        monkeypatch.setattr(svc_mod, "get_audio_store", lambda: store)
        monkeypatch.setattr(ingest_mod, "get_audio_store", lambda: store)
        get_audio_registry().register(FakeAsr("f", 10))
        result = await get_audio_service().transcribe_and_store("/tmp/a.wav")
        assert result["segments"] == 1
        items = (await store.list_segments())["items"]
        assert items[0]["transcript"] == "by f"


class TestEntityBinding:
    async def test_bind_and_reverse_lookup(self, store):
        s = await store.create_speaker(name="张三")
        bound = await store.bind_entity(s["id"], "user:webui:u1")
        assert bound is not None and bound["entity_scope"] == "user:webui:u1"
        speakers = await store.speakers_for_entity("user:webui:u1")
        assert [sp["id"] for sp in speakers] == [s["id"]]
        # 解绑
        unbound = await store.bind_entity(s["id"], "")
        assert unbound is not None and unbound["entity_scope"] == ""
        assert await store.speakers_for_entity("user:webui:u1") == []

    async def test_bind_invalid_scope_rejected(self, store):
        s = await store.create_speaker(name="张三")
        with pytest.raises(ValueError):
            await store.bind_entity(s["id"], "invalid-scope")

    async def test_segment_carries_entity_scope(self, store):
        s = await store.create_speaker(name="张三")
        await store.bind_entity(s["id"], "user:webui:u1")
        seg_id = await store.add_segment(speaker_id=s["id"], transcript="你好",
                                         ts_ns=time.time_ns())
        seg = await store.get_segment(seg_id)
        assert seg is not None and seg["entity_scope"] == "user:webui:u1"

    async def test_segments_filterable_by_entity_scope(self, store):
        """声纹→实体检索：时间线与混合检索均可按绑定实体过滤。"""
        a = await store.create_speaker(name="张三")
        b = await store.create_speaker(name="李四")
        await store.bind_entity(int(a["id"]), "user:qq:456")
        await store.add_segment(speaker_id=int(a["id"]), transcript="张三说吃饭")
        await store.add_segment(speaker_id=int(b["id"]), transcript="李四说开会")

        result = await store.list_segments(entity_scope="user:qq:456")
        assert [s["transcript"] for s in result["items"]] == ["张三说吃饭"]

        hits = await store.search_segments("吃饭", entity_scope="user:qq:456")
        assert [s["speaker_name"] for s in hits] == ["张三"]
        # 他人的话语不会被该实体过滤命中
        hits = await store.search_segments("开会", entity_scope="user:qq:456")
        assert hits == []
        # 空查询退化为时间线，实体过滤同样生效
        timeline = await store.search_segments("", entity_scope="user:qq:456")
        assert [s["transcript"] for s in timeline] == ["张三说吃饭"]


class TestVoiceprintAnchor:
    async def test_anchor_folds_on_every_accepted_sample(self, store):
        """采样越多锚越准：每次合格采样自动折叠（低学习率动态更新）。"""
        s = await store.create_speaker(name="张三")
        await store.add_sample(int(s["id"]), vec(0), duration_ms=3000)
        await store.add_sample(int(s["id"]), tilted(0, 0.9), duration_ms=1000)
        anchor, weight = await store.get_speaker_anchor(int(s["id"]))
        assert weight == pytest.approx(4.0)  # 3s + 1s（均未触截断）
        # 折叠方向偏向长样本：锚与 vec(0) 的余弦高于与倾斜样本的余弦
        from agent.audio.vectors import cosine
        assert cosine(anchor, vec(0)) > cosine(anchor, tilted(0, 0.9))

    async def test_refine_rebuilds_anchor_from_pool(self, store):
        """重建：锚以当前样本池重立——剔除坏样本后可复位（漂移落下）。"""
        s = await store.create_speaker(name="张三")
        sid = int(s["id"])
        await store.add_sample(sid, vec(0), duration_ms=1000)
        await store.add_sample(sid, tilted(0, 0.8), duration_ms=1000)
        result = await matcher.refine(store, sid)
        assert result["samples"] == 2
        assert result["anchor_similarity"] == pytest.approx(1.0, abs=1e-3)  # 池=全史
        anchor, weight = await store.get_speaker_anchor(sid)
        assert anchor[0] == pytest.approx(0.9, abs=1e-6)   # (1 + 0.8) / 2
        assert anchor[1] == pytest.approx(0.3, abs=1e-6)   # 0.6 / 2
        assert weight == pytest.approx(2.0)
        # 剔除早期样本后重建：锚不再记忆已删样本（漂移 < 1）
        for sample in await store.list_samples(sid):
            if sample["duration_ms"] == 1000 and sample["channel"] == "":
                first_id = sample["id"]
        await store.delete_sample(first_id)
        result = await matcher.refine(store, sid)
        assert result["samples"] == 1
        assert result["anchor_similarity"] < 0.999

    async def test_refine_requires_samples(self, store):
        s = await store.create_speaker(name="张三")
        with pytest.raises(ValueError, match="样本池为空"):
            await matcher.refine(store, int(s["id"]))

    async def test_anchor_survives_pool_churn_in_match(self, store):
        """样本池整体更迭（旧样本淘汰/删除）后，声纹锚仍把身份找回来。"""
        s = await store.create_speaker(name="张三")
        await store.bind_entity(int(s["id"]), "user:webui:u1")
        await store.add_sample(int(s["id"]), vec(0))
        # 池换血：删掉全部样本，只剩声纹锚（长期记忆）
        for sample in await store.list_samples(int(s["id"])):
            await store.delete_sample(int(sample["id"]))
        candidates = await matcher.match_vector(store, vec(0))
        assert candidates and candidates[0]["speaker_key"] == s["speaker_key"]
        assert candidates[0]["entity_scope"] == "user:webui:u1"

    async def test_merge_blends_anchors_exactly(self, store):
        """合并：锚按累计权重精确合成（与重放全部历史样本等价）。"""
        src = await store.create_speaker(name="临时")
        dst = await store.create_speaker(name="张三")
        await store.add_sample(int(src["id"]), vec(2), duration_ms=1000)
        await store.add_sample(int(dst["id"]), vec(0), duration_ms=3000)
        result = await matcher.merge(store, int(src["id"]), int(dst["id"]))
        assert result["samples_moved"] == 1
        assert result["anchor_similarity"] is not None
        anchor, weight = await store.get_speaker_anchor(int(dst["id"]))
        assert weight == pytest.approx(4.0)
        assert anchor[0] == pytest.approx(3.0 / 4.0, abs=1e-6)
        assert anchor[2] == pytest.approx(1.0 / 4.0, abs=1e-6)

    async def test_identify_passes_channel_and_duration(self, store):
        """identify 的信道/时长随样本入池（通话=voip 场景）。"""
        s = await matcher.enroll(store, "张三", vec(0), channel="enroll", duration_ms=2000)
        result = await matcher.identify(
            store, tilted(0, 0.95), audio_ms=5000, channel="voip")
        assert result["is_new"] is False
        samples = await store.list_samples(int(s["id"]))
        voip = [sm for sm in samples if sm["channel"] == "voip"]
        assert voip and voip[0]["duration_ms"] == 5000
