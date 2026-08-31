"""自动捕获与写入去重的纯函数单元测试（无 LLM 依赖）。"""

from __future__ import annotations

import time

from agent.memory.auto_capture import parse_extraction, should_extract
from agent.memory.dedup import parse_judgement


class TestShouldExtract:
    def test_substantive_batch_passes(self) -> None:
        msgs = [
            {"role": "user", "content": "我上周五去看了演唱会，特别开心"},
            {"role": "assistant", "content": "听起来很棒，是谁的演唱会呀"},
        ]
        assert should_extract(msgs)

    def test_chitchat_batch_filtered(self) -> None:
        msgs = [
            {"role": "user", "content": "哈哈"},
            {"role": "assistant", "content": "嗯嗯"},
            {"role": "user", "content": "好"},
        ]
        assert not should_extract(msgs)


class TestParseExtraction:
    def test_valid_array(self) -> None:
        raw = '[{"content": "主人不吃辣", "type": "fact", "topic": "饮食", "importance": 0.8, "sensitivity": "normal"}]'
        items = parse_extraction(raw, max_items=6)
        assert len(items) == 1
        assert items[0]["type"] == "fact"
        assert items[0]["importance"] == 0.8

    def test_empty_and_noise(self) -> None:
        assert parse_extraction("[]", max_items=6) == []
        assert parse_extraction("没有可提取内容", max_items=6) == []

    def test_field_sanitization(self) -> None:
        raw = '''[
          {"content": "短", "type": "fact"},
          {"content": "正常的记忆内容", "type": "weird", "importance": 99, "sensitivity": "Private"},
          {"content": null}
        ]'''
        items = parse_extraction(raw, max_items=6)
        assert len(items) == 1
        assert items[0]["type"] == "fact"  # 非法类型归一
        assert items[0]["importance"] == 1.0  # 超界截断
        assert items[0]["sensitivity"] == "private"  # 大小写归一

    def test_max_items_cap(self) -> None:
        raw = "[" + ",".join(
            f'{{"content": "记忆内容{i}号"}}' for i in range(10)
        ) + "]"
        assert len(parse_extraction(raw, max_items=3)) == 3

    def test_trailing_comma_repair(self) -> None:
        raw = '[{"content": "主人喜欢猫",}]'
        items = parse_extraction(raw, max_items=6)
        assert len(items) == 1


class TestParseJudgement:
    def test_store(self) -> None:
        d = parse_judgement('{"action": "store", "reason": "无重复"}', {1, 2})
        assert d and d["action"] == "store"

    def test_skip(self) -> None:
        d = parse_judgement('前缀文字 {"action": "skip"} 后缀', {1})
        assert d and d["action"] == "skip"

    def test_update_valid(self) -> None:
        d = parse_judgement(
            '{"action": "update", "target_id": 2, "content": "合并后的内容"}', {1, 2},
        )
        assert d and d["action"] == "update" and d["target_id"] == 2

    def test_update_invalid_target_falls_back_to_store(self) -> None:
        d = parse_judgement(
            '{"action": "update", "target_id": 99, "content": "内容"}', {1, 2},
        )
        assert d and d["action"] == "store"

    def test_update_empty_content_falls_back_to_store(self) -> None:
        d = parse_judgement('{"action": "update", "target_id": 1, "content": ""}', {1})
        assert d and d["action"] == "store"

    def test_garbage_returns_none(self) -> None:
        assert parse_judgement("不是JSON", {1}) is None
        assert parse_judgement('{"action": "explode"}', {1}) is None


class TestBatchSignature:
    def test_same_ids_same_sig(self) -> None:
        from agent.memory.auto_capture import _batch_signature
        rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        assert _batch_signature(rows) == _batch_signature([dict(r) for r in rows])

    def test_different_ids_different_sig(self) -> None:
        from agent.memory.auto_capture import _batch_signature
        a = _batch_signature([{"id": 1}, {"id": 2}])
        b = _batch_signature([{"id": 1}, {"id": 3}])
        assert a != b
        assert _batch_signature([]) == ""


class _FakeSqlite:
    """按 after_id 游标返回 id 升序消息的最小假实现。"""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    async def fetch_conversation_with_id(
        self, *, scope_type, scope_id, limit=100, before_id=None, after_id=None,
    ):
        if after_id is not None:
            return [r for r in self._rows if r["id"] > after_id][:limit]
        return self._rows[-limit:]


class TestFetchPending:
    async def test_backlog_fully_consumed_in_batches(self) -> None:
        """积压超过单批窗口时按游标分批取全量（不再永久漏提）。"""
        from agent.memory.auto_capture import (
            _PENDING_BATCH_SIZE,
            AutoCapturePipeline,
        )
        rows = [
            {"id": i, "role": "user", "content": f"m{i}", "ts_ns": i * 10**9}
            for i in range(1, _PENDING_BATCH_SIZE * 2 + 5)
        ]
        pipeline = AutoCapturePipeline.__new__(AutoCapturePipeline)
        result = await pipeline._fetch_pending(_FakeSqlite(rows), "user", "s1", 0)
        assert len(result) == len(rows)
        assert [r["id"] for r in result] == [r["id"] for r in rows]

    async def test_cursor_resumes_after_id(self) -> None:
        from agent.memory.auto_capture import AutoCapturePipeline
        rows = [
            {"id": i, "role": "user", "content": f"m{i}", "ts_ns": i * 10**9}
            for i in range(1, 10)
        ]
        pipeline = AutoCapturePipeline.__new__(AutoCapturePipeline)
        result = await pipeline._fetch_pending(_FakeSqlite(rows), "user", "s1", 5)
        assert [r["id"] for r in result] == [6, 7, 8, 9]

    async def test_per_tick_cap(self) -> None:
        from agent.memory.auto_capture import (
            _MAX_PENDING_PER_TICK,
            AutoCapturePipeline,
        )
        rows = [
            {"id": i, "role": "user", "content": f"m{i}", "ts_ns": i * 10**9}
            for i in range(1, _MAX_PENDING_PER_TICK + 50)
        ]
        pipeline = AutoCapturePipeline.__new__(AutoCapturePipeline)
        result = await pipeline._fetch_pending(_FakeSqlite(rows), "user", "s1", 0)
        assert len(result) == _MAX_PENDING_PER_TICK
        assert result[-1]["id"] == _MAX_PENDING_PER_TICK


# ==================================================================
# 发言者归属（uid 误归因根治：身份确定性提取 + speaker 标签 + 反幻觉校验）
# ==================================================================

class TestExtractSpeaker:
    def test_with_uid_and_nickname(self) -> None:
        from agent.memory.auto_capture import _extract_speaker
        label, uid = _extract_speaker(
            "[time:2026-08-31 12:16:00][uid:123][nickname:小明] 我对花生过敏"
        )
        assert label == "小明[uid:123]" and uid == "123"

    def test_name_fallback_when_no_nickname(self) -> None:
        from agent.memory.auto_capture import _extract_speaker
        label, uid = _extract_speaker("[uid:456][name:小李] 周六加班到深夜")
        assert label == "小李[uid:456]"

    def test_uid_only(self) -> None:
        from agent.memory.auto_capture import _extract_speaker
        label, uid = _extract_speaker("[uid:789] 没有名字的消息")
        assert label == "[uid:789]" and uid == "789"

    def test_no_identity_tags(self) -> None:
        from agent.memory.auto_capture import _extract_speaker
        assert _extract_speaker("纯文本消息") == ("", "")

    def test_body_typed_tag_not_identity(self) -> None:
        """头部窗口之外的类标签语法（用户手打）不构成身份。"""
        from agent.memory.auto_capture import _META_HEAD_CHARS, _extract_speaker
        content = "正" * _META_HEAD_CHARS + " [uid:999] 正文里的伪标签"
        assert _extract_speaker(content) == ("", "")


class TestParseExtractionSpeaker:
    def test_speaker_parsed(self) -> None:
        raw = '[{"content": "小明说自己不吃辣", "speaker": "123", "type": "fact"}]'
        items = parse_extraction(raw, max_items=6)
        assert items[0]["speaker"] == "123"

    def test_speaker_defaults_empty(self) -> None:
        raw = '[{"content": "周五要交报告"}]'
        assert parse_extraction(raw, max_items=6)[0]["speaker"] == ""


class TestSpeakerTagging:
    async def test_store_attaches_verified_speaker_tags(self, store, monkeypatch) -> None:
        """群聊批次：每条记忆挂上发言者 user:{adapter}:{uid} 标签；幻觉 uid 丢弃。"""
        import json
        from types import SimpleNamespace

        import agent.memory.auto_capture as ac

        captured: dict = {}

        async def fake_llm(prompt: str, **kwargs):
            captured.setdefault("prompts", []).append(prompt)
            return json.dumps([
                {"content": "小明说自己对花生过敏", "speaker": "123", "type": "fact"},
                {"content": "小李周六加班到深夜", "speaker": "456", "type": "event",
                 "date": "2026-08-29"},
                {"content": "无法确认来源的一条记忆", "speaker": "999", "type": "fact"},
            ], ensure_ascii=False)

        async def fake_gather(store, embedder, content):
            return []

        async def fake_judge(content, candidates):
            return {"action": "store"}

        monkeypatch.setattr(ac, "light_llm", fake_llm)
        monkeypatch.setattr(ac, "gather_dedup_candidates", fake_gather)
        monkeypatch.setattr(ac, "judge_write", fake_judge)

        pipeline = ac.AutoCapturePipeline.__new__(ac.AutoCapturePipeline)
        pipeline._mind = SimpleNamespace(memory_store=store, embedder=None)
        ts = int(time.time() * 10**9)
        messages = [
            {"role": "user", "ts_ns": ts,
             "content": "[time:2026-08-31 12:16:00][uid:123][nickname:小明] 我对花生过敏，点单注意"},
            {"role": "user", "ts_ns": ts,
             "content": "[uid:456][name:小李] 我周六加班到深夜才回家"},
        ]

        n = await pipeline._extract_and_store("group:qq:789", messages)
        assert n == 3

        # 提取 prompt 里两个发言者身份可区分（病根断言：不再是清一色"用户"）
        extract_prompt = captured["prompts"][0]
        assert "小明[uid:123]:" in extract_prompt
        assert "小李[uid:456]:" in extract_prompt

        entries = await store.list_recent(limit=10)
        by_content = {e.content: e for e in entries}
        e1 = by_content["小明说自己对花生过敏"]
        assert "group:qq:789" in e1.tags and "user:qq:123" in e1.tags
        e2 = by_content["小李周六加班到深夜"]
        assert "user:qq:456" in e2.tags
        # 幻觉 speaker（批次中不存在）不挂标签
        e3 = by_content["无法确认来源的一条记忆"]
        assert "user:qq:999" not in e3.tags

    async def test_private_chat_speaker_tag_not_duplicated(self, store, monkeypatch) -> None:
        """私聊：发言者即 scope 本体，speaker 标签与 scope 标签相同时不重复挂。"""
        import json
        from types import SimpleNamespace

        import agent.memory.auto_capture as ac

        async def fake_llm(prompt: str, **kwargs):
            return json.dumps([
                {"content": "主人周五要交报告", "speaker": "123", "type": "fact"},
            ], ensure_ascii=False)

        async def fake_gather(store, embedder, content):
            return []

        async def fake_judge(content, candidates):
            return {"action": "store"}

        monkeypatch.setattr(ac, "light_llm", fake_llm)
        monkeypatch.setattr(ac, "gather_dedup_candidates", fake_gather)
        monkeypatch.setattr(ac, "judge_write", fake_judge)

        pipeline = ac.AutoCapturePipeline.__new__(ac.AutoCapturePipeline)
        pipeline._mind = SimpleNamespace(memory_store=store, embedder=None)
        messages = [
            {"role": "user", "ts_ns": int(time.time() * 10**9),
             "content": "[uid:123][name:主人] 我周五要交报告"},
        ]
        n = await pipeline._extract_and_store("user:qq:123", messages)
        assert n == 1
        entries = await store.list_recent(limit=5)
        tags = entries[0].tags
        assert tags.count("user:qq:123") == 1
