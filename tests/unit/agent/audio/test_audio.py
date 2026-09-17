"""音频核心层测试：提供者注册表/优先级链、服务解析、入库管线、旧库迁移。"""

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


class TestMigration:
    async def test_migrate_legacy_voiceprints(self, store, tmp_path, monkeypatch):
        """旧实体声纹库（说话人/样本/片段/录制）一次性迁入，幂等。"""
        import aiosqlite
        legacy = tmp_path / "agent_voiceprints.sqlite3"
        async with aiosqlite.connect(str(legacy)) as db:
            await db.executescript("""
                CREATE TABLE speakers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, speaker_key TEXT UNIQUE,
                    name TEXT DEFAULT '', role TEXT DEFAULT '', status TEXT DEFAULT 'confirmed',
                    threshold REAL, notes TEXT DEFAULT '', device_source TEXT DEFAULT '',
                    total_audio_ms INTEGER DEFAULT 0, first_seen_ns INTEGER DEFAULT 0,
                    last_seen_ns INTEGER DEFAULT 0, match_count INTEGER DEFAULT 0,
                    archived INTEGER DEFAULT 0);
                CREATE TABLE voice_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, speaker_id INTEGER,
                    vector BLOB, segment_id INTEGER, score REAL DEFAULT 0,
                    source TEXT DEFAULT '', created_ns INTEGER DEFAULT 0);
                CREATE TABLE voice_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, recording_path TEXT DEFAULT '',
                    source_file TEXT DEFAULT '', device_source TEXT DEFAULT '',
                    start_ms INTEGER DEFAULT 0, end_ms INTEGER DEFAULT 0,
                    part_start_ms INTEGER DEFAULT 0, speaker_id INTEGER,
                    is_new_speaker INTEGER DEFAULT 0, similarity REAL DEFAULT 0,
                    transcript TEXT DEFAULT '', transcript_embedding BLOB,
                    ts_ns INTEGER DEFAULT 0, read INTEGER DEFAULT 0);
                CREATE TABLE recordings (
                    path TEXT PRIMARY KEY, kind TEXT DEFAULT 'folder',
                    fingerprint TEXT DEFAULT '', started_ns INTEGER DEFAULT 0,
                    file_count INTEGER DEFAULT 0, status TEXT DEFAULT 'done',
                    error TEXT DEFAULT '', segments INTEGER DEFAULT 0,
                    files_json TEXT DEFAULT '[]', synced_ns INTEGER DEFAULT 0);
            """)
            await db.execute(
                "INSERT INTO speakers(speaker_key, name, first_seen_ns, last_seen_ns) "
                "VALUES ('spk_0001', '张三', 1, 2)")
            await db.execute(
                "INSERT INTO voice_samples(speaker_id, vector, source, created_ns) "
                "VALUES (1, ?, 'enroll', 100)", (b"\x00" * 768,))
            await db.execute(
                "INSERT INTO voice_segments(recording_path, source_file, start_ms, end_ms, "
                "speaker_id, transcript, ts_ns) "
                "VALUES ('/nas/a', 'a.wav', 0, 500, 1, '旧片段', 1000)")
            await db.execute(
                "INSERT INTO recordings(path, kind, synced_ns) VALUES ('/nas/a', 'folder', 100)")
            await db.commit()
        monkeypatch.setattr(
            "agent.audio.store._legacy_db_candidates", lambda: [str(legacy)])
        totals = await store.migrate_legacy()
        assert totals == {"speakers": 1, "samples": 1, "segments": 1, "recordings": 1}
        # 幂等：再跑一次零增量
        assert await store.migrate_legacy() == {
            "speakers": 0, "samples": 0, "segments": 0, "recordings": 0}
        speaker = await store.get_speaker_by_key("spk_0001")
        assert speaker is not None and speaker["name"] == "张三"
        items = (await store.list_segments())["items"]
        assert items[0]["transcript"] == "旧片段"
        assert items[0]["speaker_id"] == speaker["id"]
        assert await store.get_recording("/nas/a") is not None

    async def test_migrate_v1_audio_segments(self, tmp_path):
        """初版音频库表（abs_*/speaker_key/vector 列）升级为新结构。"""
        import aiosqlite
        db_path = str(tmp_path / "audio.sqlite3")
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE audio_segments ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT DEFAULT '', "
                "device_source TEXT DEFAULT '', ts_ns INTEGER NOT NULL, "
                "start_ms INTEGER DEFAULT 0, end_ms INTEGER DEFAULT 0, "
                "abs_start_ms INTEGER, abs_end_ms INTEGER, transcript TEXT DEFAULT '', "
                "transcript_tokens TEXT DEFAULT '', speaker_key TEXT DEFAULT '', "
                "speaker_name TEXT DEFAULT '', similarity REAL DEFAULT 0, "
                "vector BLOB, created_at REAL NOT NULL)")
            await db.execute(
                "INSERT INTO audio_segments(source_file, ts_ns, transcript, speaker_key, "
                "created_at) VALUES ('a.wav', 1000, '你好', 'spk_0001', 1.0)")
            await db.commit()
        store = AudioStore(db_path)
        await store.initialize()
        items = (await store.list_segments())["items"]
        assert len(items) == 1 and items[0]["transcript"] == "你好"
        # v1 迁移后 FTS 索引已全量重建（迁入行可被全文召回）
        hits = await store.search_segments("你好")
        assert hits and hits[0]["transcript"] == "你好"
        # v1 说话人引用暂存，待旧声纹库迁入后回填
        assert await store._get_meta("v1_segment_speaker_keys")
        speaker = await store.create_speaker(name="张三")
        await store._set_meta("v1_segment_speaker_keys",
                              '{"1": "' + speaker["speaker_key"] + '"}')
        await store._backfill_v1_speaker_keys()
        items = (await store.list_segments())["items"]
        assert items[0]["speaker_id"] == speaker["id"]
        await store.close()

    async def test_migrate_oldest_schema_without_later_columns(self, store, tmp_path, monkeypatch):
        """最老库（无 recording_path/part_start_ms/abs_start_ms 等后加列）宽容迁移。"""
        import aiosqlite
        legacy = tmp_path / "agent_voiceprints.sqlite3"
        async with aiosqlite.connect(str(legacy)) as db:
            await db.executescript("""
                CREATE TABLE speakers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, speaker_key TEXT UNIQUE,
                    name TEXT DEFAULT '');
                CREATE TABLE voice_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, speaker_id INTEGER,
                    vector BLOB, created_ns INTEGER DEFAULT 0);
                CREATE TABLE voice_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT DEFAULT '',
                    start_ms INTEGER DEFAULT 0, end_ms INTEGER DEFAULT 0,
                    speaker_id INTEGER, similarity REAL DEFAULT 0,
                    transcript TEXT DEFAULT '', ts_ns INTEGER DEFAULT 0,
                    read INTEGER DEFAULT 0);
            """)
            await db.execute(
                "INSERT INTO speakers(speaker_key, name) VALUES ('spk_0001', '张三')")
            await db.execute(
                "INSERT INTO voice_samples(speaker_id, vector, created_ns) "
                "VALUES (1, ?, 100)", (b"\x00" * 768,))
            await db.execute(
                "INSERT INTO voice_segments(source_file, start_ms, end_ms, speaker_id, "
                "transcript, ts_ns) VALUES ('a.wav', 0, 500, 1, '旧片段', 1000)")
            await db.commit()
        monkeypatch.setattr(
            "agent.audio.store._legacy_db_candidates", lambda: [str(legacy)])
        totals = await store.migrate_legacy()
        assert totals["speakers"] == 1 and totals["samples"] == 1
        assert totals["segments"] == 1
        speaker = await store.get_speaker_by_key("spk_0001")
        assert speaker is not None
        items = (await store.list_segments())["items"]
        assert items[0]["transcript"] == "旧片段"
        assert items[0]["speaker_id"] == speaker["id"]
        assert items[0]["recording_path"] == ""

    async def test_migrate_enriches_v1_rows_instead_of_duplicating(
        self, store, tmp_path, monkeypatch,
    ):
        """早期已迁过的行（recording_path=''）命中时富化更新而非重复插入。"""
        import aiosqlite
        speaker = await store.create_speaker(name="张三")
        v1_seg = await store.add_segment(
            source_file="a.wav", start_ms=0, end_ms=500, transcript="旧片段", ts_ns=1000)
        legacy = tmp_path / "agent_voiceprints.sqlite3"
        async with aiosqlite.connect(str(legacy)) as db:
            await db.executescript("""
                CREATE TABLE speakers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, speaker_key TEXT UNIQUE,
                    name TEXT DEFAULT '', first_seen_ns INTEGER DEFAULT 0,
                    last_seen_ns INTEGER DEFAULT 0);
                CREATE TABLE voice_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, speaker_id INTEGER,
                    vector BLOB, created_ns INTEGER DEFAULT 0);
                CREATE TABLE voice_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, recording_path TEXT DEFAULT '',
                    source_file TEXT DEFAULT '', start_ms INTEGER DEFAULT 0,
                    end_ms INTEGER DEFAULT 0, speaker_id INTEGER,
                    transcript TEXT DEFAULT '', ts_ns INTEGER DEFAULT 0,
                    read INTEGER DEFAULT 0);
            """)
            await db.execute(
                f"INSERT INTO speakers(speaker_key, name) "
                f"VALUES ('{speaker['speaker_key']}', '张三')")
            await db.execute(
                "INSERT INTO voice_segments(recording_path, source_file, start_ms, end_ms, "
                "speaker_id, transcript, ts_ns) "
                "VALUES ('/nas/a', 'a.wav', 0, 500, 1, '旧片段', 1000)")
            await db.commit()
        monkeypatch.setattr(
            "agent.audio.store._legacy_db_candidates", lambda: [str(legacy)])
        totals = await store.migrate_legacy()
        assert totals["segments"] == 0  # 未重复插入
        items = (await store.list_segments())["items"]
        assert len(items) == 1
        assert items[0]["id"] == v1_seg
        assert items[0]["recording_path"] == "/nas/a"  # 已富化
        assert items[0]["speaker_id"] == speaker["id"]


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


class TestVoiceprintRefine:
    async def test_refine_sets_anchor_with_drift(self, store):
        s = await store.create_speaker(name="张三")
        for i in range(3):
            await store.add_sample(int(s["id"]), vec(0) if i == 0 else vec(1))
        result = await matcher.refine(store, int(s["id"]))
        assert result["samples"] == 3
        assert result["anchor_similarity"] is None  # 首次精化无漂移
        anchor = await store.get_speaker_anchor(int(s["id"]))
        assert len(anchor) == 192
        # 再次精化：新旧锚融合，漂移为余弦（≤1）
        result2 = await matcher.refine(store, int(s["id"]))
        assert result2["anchor_similarity"] is not None
        assert result2["anchor_similarity"] <= 1.0

    async def test_refine_requires_samples(self, store):
        s = await store.create_speaker(name="张三")
        with pytest.raises(ValueError, match="样本池为空"):
            await matcher.refine(store, int(s["id"]))

    async def test_anchor_survives_pool_churn_in_match(self, store):
        """样本池整体更迭（旧样本淘汰）后，质心锚仍把身份找回来。"""
        s = await store.create_speaker(name="张三")
        await store.bind_entity(int(s["id"]), "user:webui:u1")
        await store.add_sample(int(s["id"]), vec(0))
        await matcher.refine(store, int(s["id"]))
        # 池换血：删掉全部样本，只剩质心锚
        for sample in await store.list_samples(int(s["id"])):
            await store.delete_sample(int(sample["id"]))
        candidates = await matcher.match_vector(store, vec(0))
        assert candidates and candidates[0]["speaker_key"] == s["speaker_key"]
        assert candidates[0]["entity_scope"] == "user:webui:u1"

    async def test_merge_refines_target(self, store):
        src = await store.create_speaker(name="临时")
        dst = await store.create_speaker(name="张三")
        await store.add_sample(int(src["id"]), vec(2))
        await store.add_sample(int(dst["id"]), vec(0))
        result = await matcher.merge(store, int(src["id"]), int(dst["id"]))
        assert "refined" in result and result["refined"]["samples"] >= 1
        assert await store.get_speaker_anchor(int(dst["id"]))
