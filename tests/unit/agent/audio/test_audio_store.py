"""AudioStore 数据面测试：档案/样本池/片段/FTS 检索/未读收件箱。"""

from __future__ import annotations

import pytest

from agent.audio.store import AudioStore, parse_time_ns
from agent.memory.store._shared import build_fts_query


def vec(dim: int) -> list[float]:
    """构造第 dim 维为 1 的 192 维单位向量。"""
    return [1.0 if i == dim else 0.0 for i in range(192)]


@pytest.fixture
async def store(tmp_path):
    s = AudioStore(str(tmp_path / "voiceprints.sqlite3"))
    yield s
    await s.close()


class TestSpeakerCrud:
    async def test_create_and_get(self, store: AudioStore) -> None:
        s = await store.create_speaker(name="张三", role="家人")
        assert s["speaker_key"].startswith("spk_")
        assert s["status"] == "confirmed"
        got = await store.get_speaker(s["id"])
        assert got is not None and got["name"] == "张三"

    async def test_pending_key_prefix(self, store: AudioStore) -> None:
        s = await store.create_speaker(status="pending")
        assert s["speaker_key"].startswith("spk_tmp_")

    async def test_confirm_refreshes_key(self, store: AudioStore) -> None:
        s = await store.create_speaker(status="pending")
        updated = await store.update_speaker(s["id"], name="王五", status="confirmed")
        assert updated is not None
        assert updated["speaker_key"].startswith("spk_")
        assert updated["name"] == "王五"

    async def test_find_by_id_key_name(self, store: AudioStore) -> None:
        s = await store.create_speaker(name="李四")
        assert (await store.find_speakers(str(s["id"])))[0]["id"] == s["id"]
        assert (await store.find_speakers(s["speaker_key"]))[0]["id"] == s["id"]
        assert (await store.find_speakers("李四"))[0]["id"] == s["id"]
        assert (await store.find_speakers("李"))[0]["id"] == s["id"]
        assert await store.find_speakers("不存在的人") == []

    async def test_update_whitelist_fields(self, store: AudioStore) -> None:
        s = await store.create_speaker(name="张三")
        updated = await store.update_speaker(s["id"], role="同事", threshold=0.8, notes="备注")
        assert updated is not None
        assert updated["role"] == "同事" and updated["threshold"] == 0.8

    async def test_delete_cascades_segments(self, store: AudioStore) -> None:
        """级联删除：说话人删除时其话语片段一并删除（不产生未知孤儿）。"""
        s = await store.create_speaker(name="张三")
        await store.add_sample(s["id"], vec(0))
        seg_id = await store.add_segment(speaker_id=s["id"], transcript="你好")
        deleted = await store.delete_speaker(s["id"])
        assert deleted is not None
        assert await store.get_speaker(s["id"]) is None
        assert await store.get_segment(seg_id) is None  # 片段随删，不再置 NULL

    async def test_list_filter(self, store: AudioStore) -> None:
        await store.create_speaker(name="张三", role="家人")
        await store.create_speaker(status="pending")
        confirmed = await store.list_speakers(status="confirmed")
        assert confirmed["total"] == 1
        pending = await store.list_speakers(status="pending")
        assert pending["total"] == 1
        by_keyword = await store.list_speakers(keyword="家人")
        assert by_keyword["total"] == 1


class TestSamplePool:
    async def test_fifo_eviction(self, store: AudioStore) -> None:
        from core.config import ConfigManager

        ConfigManager.set("audio_sample_evict_strategy", "fifo")
        s = await store.create_speaker(name="张三")
        for i in range(4):
            await store.add_sample(s["id"], vec(i), max_samples=3)
        samples = await store.list_samples(s["id"])
        assert len(samples) == 3  # 最早的 vec(0) 被淘汰

    async def test_outlier_eviction(self, store: AudioStore) -> None:
        """outlier 策略：极端样本被淘汰（即使它更早入池），代表性样本保留。"""
        from core.config import ConfigManager

        ConfigManager.set("audio_sample_evict_strategy", "outlier")
        s = await store.create_speaker(name="张三")
        # 3 个相似样本 + 1 个正交极端样本，池满后再来 1 个相似样本
        await store.add_sample(s["id"], vec(0), max_samples=3)
        await store.add_sample(s["id"], [0.99, 0.01] + [0.0] * 190, max_samples=3)
        await store.add_sample(s["id"], vec(5), max_samples=3)  # 极端样本
        new_id = await store.add_sample(s["id"], [0.98, 0.02] + [0.0] * 190, max_samples=3)
        assert new_id > 0
        samples = await store.list_samples(s["id"])
        assert len(samples) == 3
        # 极端样本 vec(5) 应已被淘汰：池中向量都集中在 vec(0) 附近
        vectors = [await store.get_sample_vector(sm["id"]) for sm in samples]
        assert all(v and v[0] > 0.9 for v in vectors)

    async def test_outlier_rejects_noisy_new_sample(self, store: AudioStore) -> None:
        """outlier 策略：池满时噪音新样本与质心差异过大 → 拒绝入池。"""
        from core.config import ConfigManager

        ConfigManager.set("audio_sample_evict_strategy", "outlier")
        s = await store.create_speaker(name="张三")
        await store.add_sample(s["id"], vec(0), max_samples=2)
        await store.add_sample(s["id"], [0.99, 0.01] + [0.0] * 190, max_samples=2)
        rejected = await store.add_sample(s["id"], vec(7), max_samples=2)
        assert rejected == -1
        assert len(await store.list_samples(s["id"])) == 2

    async def test_search_vectors(self, store: AudioStore) -> None:
        s1 = await store.create_speaker(name="张三")
        s2 = await store.create_speaker(name="李四")
        await store.add_sample(s1["id"], vec(0))
        await store.add_sample(s2["id"], vec(1))
        hits = await store.search_sample_vectors(vec(0), limit=5)
        assert hits[0]["speaker_id"] == s1["id"]
        assert hits[0]["score"] > 0.99

    async def test_delete_sample(self, store: AudioStore) -> None:
        s = await store.create_speaker(name="张三")
        sample_id = await store.add_sample(s["id"], vec(0))
        assert await store.delete_sample(sample_id) is True
        assert await store.list_samples(s["id"]) == []


class TestSegments:
    async def test_add_and_join_speaker_name(self, store: AudioStore) -> None:
        s = await store.create_speaker(name="张三")
        seg_id = await store.add_segment(speaker_id=s["id"], transcript="今晚一起吃饭")
        seg = await store.get_segment(seg_id)
        assert seg is not None
        assert seg["speaker_name"] == "张三"
        assert seg["read"] is False

    async def test_time_and_speaker_filters(self, store: AudioStore) -> None:
        s = await store.create_speaker(name="张三")
        await store.add_segment(speaker_id=s["id"], transcript="早上好", ts_ns=1_000)
        await store.add_segment(speaker_id=s["id"], transcript="晚上好", ts_ns=2_000)
        result = await store.list_segments(speaker_id=s["id"], from_ns=1_500)
        assert result["total"] == 1
        assert result["items"][0]["transcript"] == "晚上好"

    async def test_fts_search(self, store: AudioStore) -> None:
        s = await store.create_speaker(name="张三")
        await store.add_segment(speaker_id=s["id"], transcript="今天晚上一起吃饭吧")
        await store.add_segment(speaker_id=s["id"], transcript="明天开会讨论项目")
        hits = await store.search_segments("吃饭")
        assert len(hits) == 1
        assert hits[0]["speaker_name"] == "张三"

    async def test_mark_read(self, store: AudioStore) -> None:
        s = await store.create_speaker(name="张三")
        id1 = await store.add_segment(speaker_id=s["id"], transcript="片段一")
        await store.add_segment(speaker_id=s["id"], transcript="片段二")
        assert await store.unread_count() == 2
        assert await store.mark_read([id1]) == 1
        assert await store.unread_count() == 1
        assert await store.mark_read(None) == 1
        assert await store.unread_count() == 0

    async def test_embedding_backfill_fields(self, store: AudioStore) -> None:
        s = await store.create_speaker(name="张三")
        seg_id = await store.add_segment(speaker_id=s["id"], transcript="待回填")
        missing = await store.list_missing_transcript_embeddings(10)
        assert [r["id"] for r in missing] == [seg_id]
        await store.set_transcript_embedding(seg_id, [0.1, 0.2, 0.3])
        assert await store.list_missing_transcript_embeddings(10) == []
        seg = await store.get_segment(seg_id)
        assert seg is not None and seg["has_embedding"] is True


class TestHelpers:
    def test_parse_time_ns(self) -> None:
        assert parse_time_ns("") is None
        assert parse_time_ns("2026-08-01") is not None
        assert parse_time_ns("2026-08-01 14:30") is not None
        assert parse_time_ns("1785988800") == 1_785_988_800_000_000_000
        assert parse_time_ns("垃圾输入") is None

    def test_build_fts_query(self) -> None:
        assert build_fts_query("") is None
        query = build_fts_query("今天晚上")
        assert query is not None and "OR" in query
        assert build_fts_query("hello world") == '"hello" OR "world"'


class TestTranscriptEdit:
    async def test_update_transcript(self, store: AudioStore) -> None:
        """单条修订：FTS 同步 + 向量置空待回填。"""
        seg_id = await store.add_segment(transcript="我们去尺饭吧", ts_ns=1000)
        await store.set_transcript_embedding(seg_id, [0.1, 0.2])
        updated = await store.update_transcript(seg_id, "我们去吃饭吧")
        assert updated is not None and updated["transcript"] == "我们去吃饭吧"
        assert updated["has_embedding"] is False  # 向量已置空
        hits = await store.search_segments("吃饭")
        assert [h["id"] for h in hits] == [seg_id]  # FTS 按新文本命中
        assert await store.update_transcript(9999, "x") is None

    async def test_replace_batch(self, store: AudioStore) -> None:
        """批量替换：人名一次全改 + 过滤范围 + dry_run。"""
        s = await store.create_speaker(name="张三")
        id1 = await store.add_segment(speaker_id=s["id"], transcript="章三说这个需求", ts_ns=1)
        id2 = await store.add_segment(speaker_id=s["id"], transcript="章三又确认了排期", ts_ns=2)
        await store.add_segment(transcript="与章三无关的人说的章三", ts_ns=3)

        # dry_run：只统计不写入
        preview = await store.replace_in_transcripts("章三", "张三", dry_run=True)
        assert preview["matched"] == 3 and preview["changed"] == 0
        assert len(preview["samples"]) == 3

        # 限定说话人正式替换
        result = await store.replace_in_transcripts("章三", "张三", speaker_id=s["id"])
        assert result["changed"] == 2
        seg1 = await store.get_segment(id1)
        seg2 = await store.get_segment(id2)
        assert seg1 is not None and seg1["transcript"] == "张三说这个需求"
        assert seg2 is not None and seg2["transcript"] == "张三又确认了排期"
        # FTS 已按新文本生效
        hits = await store.search_segments("张三说")
        assert hits and hits[0]["id"] == id1

    async def test_replace_empty_find(self, store: AudioStore) -> None:
        result = await store.replace_in_transcripts("  ", "x")
        assert result["matched"] == 0


class TestSegmentMerge:
    async def test_merge_basic(self, store: AudioStore) -> None:
        """相邻碎片合并：文本拼接 + 时间跨度 + 归属继承 + FTS 生效。"""
        s = await store.create_speaker(name="张三")
        id1 = await store.add_segment(
            recording_path="/r1", speaker_id=s["id"], start_ms=0, end_ms=1000, transcript="我们今天", ts_ns=1
        )
        id2 = await store.add_segment(
            recording_path="/r1", speaker_id=s["id"], start_ms=1000, end_ms=2000, transcript="去吃饭吧", ts_ns=2
        )
        merged = await store.merge_segments([id1, id2])
        assert merged is not None
        assert merged["id"] == id1
        assert merged["transcript"] == "我们今天 去吃饭吧"
        assert merged["start_ms"] == 0 and merged["end_ms"] == 2000
        assert merged["speaker_id"] == s["id"]
        assert await store.get_segment(id2) is None
        hits = await store.search_segments("吃饭")
        assert hits and hits[0]["id"] == id1

    async def test_merge_custom_text_and_speaker(self, store: AudioStore) -> None:
        s1 = await store.create_speaker(name="张三")
        s2 = await store.create_speaker(name="李四")
        id1 = await store.add_segment(recording_path="/r1", speaker_id=s1["id"], transcript="错字连篇", ts_ns=1)
        id2 = await store.add_segment(recording_path="/r1", speaker_id=s1["id"], transcript="的第二段", ts_ns=2)
        merged = await store.merge_segments([id1, id2], transcript="修正后的完整句子", speaker_id=s2["id"])
        assert merged is not None
        assert merged["transcript"] == "修正后的完整句子"
        assert merged["speaker_id"] == s2["id"]

    async def test_merge_cross_recording_rejected(self, store: AudioStore) -> None:
        id1 = await store.add_segment(recording_path="/r1", transcript="一", ts_ns=1)
        id2 = await store.add_segment(recording_path="/r2", transcript="二", ts_ns=2)
        with pytest.raises(ValueError):
            await store.merge_segments([id1, id2])

    async def test_merge_single_rejected(self, store: AudioStore) -> None:
        id1 = await store.add_segment(recording_path="/r1", transcript="一", ts_ns=1)
        assert await store.merge_segments([id1]) is None


class TestSegmentSplit:
    async def test_split_basic(self, store: AudioStore) -> None:
        """拆段：首段截断 + 次段继承（时间按切点顺延）。"""
        s = await store.create_speaker(name="张三")
        seg_id = await store.add_segment(
            recording_path="/r1",
            speaker_id=s["id"],
            start_ms=0,
            end_ms=10000,
            part_start_ms=5000,
            transcript="一整段话",
            ts_ns=1_000_000_000_000,
        )
        result = await store.split_segment(seg_id, 4000, text_second="后半句")
        assert result is not None
        first, second = result["first"], result["second"]
        assert first["end_ms"] == 4000
        assert first["transcript"] == "一整段话"
        assert second["start_ms"] == 4000 and second["end_ms"] == 10000
        assert second["transcript"] == "后半句"
        assert second["recording_path"] == "/r1"
        assert second["part_start_ms"] == 5000
        assert second["speaker_id"] == s["id"]
        # 次段 ts = 原 ts + 切点偏移
        assert second["ts_ns"] == 1_000_000_000_000 + 4000 * 1_000_000
        assert first["id"] == seg_id and second["id"] != seg_id

    async def test_split_unknown_speaker(self, store: AudioStore) -> None:
        s = await store.create_speaker(name="张三")
        seg_id = await store.add_segment(
            recording_path="/r1", speaker_id=s["id"], start_ms=0, end_ms=5000, transcript="两人对话", ts_ns=1000
        )
        result = await store.split_segment(seg_id, 2000, speaker_second_id=None, speaker_second_set=True)
        assert result is not None
        assert result["second"]["speaker_id"] is None

    async def test_split_invalid_point(self, store: AudioStore) -> None:
        seg_id = await store.add_segment(recording_path="/r1", start_ms=0, end_ms=5000, transcript="x", ts_ns=1000)
        with pytest.raises(ValueError):
            await store.split_segment(seg_id, 5000)
        with pytest.raises(ValueError):
            await store.split_segment(seg_id, 0)


class TestSummaryCache:
    async def test_dirty_refresh(self, store: AudioStore) -> None:
        first = await store.summary()
        assert first["confirmed_names"] == []
        await store.create_speaker(name="张三")
        second = await store.summary()
        assert second["confirmed_names"] == ["张三"]
        # 未写操作时应命中缓存（同一对象）
        assert await store.summary() is second


class TestReviewRegressions:
    async def test_split_inherits_read_flag(self, store: AudioStore) -> None:
        """拆段：次段已读标记继承原段（不反转产生幽灵未读）。"""
        read_id = await store.add_segment(
            recording_path="/r1", start_ms=0, end_ms=2000, transcript="已读段", ts_ns=1)
        await store.mark_read([read_id])
        result = await store.split_segment(read_id, 1000)
        assert result is not None
        assert result["first"]["read"] is True
        assert result["second"]["read"] is True

        unread_id = await store.add_segment(
            recording_path="/r1", start_ms=0, end_ms=2000, transcript="未读段", ts_ns=2)
        result = await store.split_segment(unread_id, 1000)
        assert result is not None
        assert result["second"]["read"] is False

    async def test_merge_repoints_sample_links(self, store: AudioStore) -> None:
        """并段：被并片段挂接的声纹样本改挂保留片段（防录制删除级联漏清）。"""
        s = await store.create_speaker(name="张三")
        id1 = await store.add_segment(
            recording_path="/r1", speaker_id=s["id"], start_ms=0, end_ms=1000,
            transcript="第一段", ts_ns=1)
        id2 = await store.add_segment(
            recording_path="/r1", speaker_id=s["id"], start_ms=1000, end_ms=2000,
            transcript="第二段", ts_ns=2)
        sample_id = await store.add_sample(s["id"], [0.1] * 192, segment_id=id2)
        assert sample_id > 0
        merged = await store.merge_segments([id1, id2])
        assert merged is not None
        samples = await store.list_samples(s["id"])
        assert samples[0]["segment_id"] == id1
        # 录制删除时样本能被级联清理
        removed = await store.delete_recording("/r1")
        assert removed["samples_deleted"] == 1

    async def test_migration_skips_archived_key_collision(
        self, store: AudioStore, tmp_path, monkeypatch,
    ) -> None:
        """旧库 speaker_key 与已归档档案撞键：映射复用而非 UNIQUE 崩溃。"""
        import aiosqlite
        archived = await store.create_speaker(name="旧张三")
        await store.archive_speaker(archived["id"])
        legacy = tmp_path / "agent_voiceprints.sqlite3"
        async with aiosqlite.connect(str(legacy)) as db:
            await db.executescript("""
                CREATE TABLE speakers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, speaker_key TEXT UNIQUE,
                    name TEXT DEFAULT '');
                CREATE TABLE voice_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT DEFAULT '',
                    start_ms INTEGER DEFAULT 0, speaker_id INTEGER,
                    transcript TEXT DEFAULT '', ts_ns INTEGER DEFAULT 0);
            """)
            await db.execute(
                f"INSERT INTO speakers(speaker_key, name) "
                f"VALUES ('{archived['speaker_key']}', '张三')")
            await db.execute(
                "INSERT INTO voice_segments(source_file, start_ms, speaker_id, transcript, ts_ns) "
                "VALUES ('a.wav', 0, 1, '旧片段', 1000)")
            await db.commit()
        monkeypatch.setattr(
            "agent.audio.store._legacy_db_candidates", lambda: [str(legacy)])
        totals = await store.migrate_legacy()
        assert totals["speakers"] == 0  # 撞键映射复用，不重复建档
        items = (await store.list_segments())["items"]
        assert items[0]["speaker_id"] == archived["id"]


class TestSummaryBindings:
    async def test_bindings_in_summary(self, store: AudioStore) -> None:
        s = await store.create_speaker(name="张三", role="家人")
        await store.bind_entity(int(s["id"]), "user:webui:u1")
        lonely = await store.create_speaker(name="李四")
        summary = await store.summary()
        assert "张三(家人)" in summary["confirmed_names"]
        assert summary["entity_bindings"] == ["张三(家人)→user:webui:u1"]
        assert lonely["name"] in "李四"
