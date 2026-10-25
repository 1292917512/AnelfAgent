"""自动捕获的纯函数单元测试（无 LLM 依赖）。"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.memory.auto_capture import parse_extraction, should_extract
from agent.memory.capture_state import CaptureOutcome


class TestShouldExtract:
    def test_substantive_batch_passes(self) -> None:
        msgs = [
            {"role": "user", "content": "我上周五去看了演唱会，特别开心"},
            {"role": "assistant", "content": "听起来很棒，是谁的演唱会呀"},
        ]
        assert should_extract(msgs)

    def test_nontext_batch_filtered(self) -> None:
        assert not should_extract([{"role": "user", "content": "[uid:123] 😂"}])
        assert should_extract([{"role": "user", "content": "我姓张"}])


class TestParseExtraction:
    def test_valid_array(self) -> None:
        items = parse_extraction('[{"content":"主人不吃辣","type":"fact","importance":0.8}]', max_items=6)
        assert items[0].content == "主人不吃辣" and items[0].importance == 0.8
        assert parse_extraction("[]", max_items=6) == []

    @pytest.mark.parametrize("raw", ["没有可提取内容", '{"content":"错误根节点"}',
        '[{"content":null}]', '[{"content":"正常内容","type":"weird"}]',
        '[{"content":"正常内容","importance":99}]', '[{"content":"主人喜欢猫",}]'])
    def test_invalid_output_is_failure(self, raw: str) -> None:
        with pytest.raises(ValueError):
            parse_extraction(raw, max_items=6)

    def test_over_budget_output_is_not_silently_dropped(self) -> None:
        with pytest.raises(ValueError):
            parse_extraction('[{"content":"完整事实一"},{"content":"完整事实二"}]', max_items=1)


class _FakeSqlite:
    """按 after_id 游标返回 id 升序消息的最小假实现。"""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    async def fetch_conversation_with_id(
        self, *, scope_type, scope_id, limit=100, before_id=None, after_id=None,
    ):
        if after_id is not None:
            return [r for r in self._rows if r["id"] > after_id][:limit]
        rows = [r for r in self._rows if before_id is None or r["id"] < before_id]
        return rows[-limit:]


async def test_relation_capture_cannot_rename_hubs_and_enqueue_their_entire_neighborhood(store, monkeypatch):
    import agent.memory.auto_capture as ac

    graph = store.graph
    owner = await graph.upsert_node("user:qq:1", label="正式用户名称")
    group = await graph.upsert_node("group:qq:100", label="正式群名称")
    edge = await graph.add_relation("group:qq:100", "包含成员", "user:qq:1",
                                    evidence="既有成员关系", origin="heartbeat_extract")
    db = await store._conn.get_db()
    async with store._conn.tx(db):
        await db.executemany(
            "INSERT INTO graph_nodes(node_key,node_type,label,created_ns,updated_ns) VALUES(?,?,?,?,?)",
            [(f"topic:neighbor-{i}", "topic", f"邻居 {i}", 1, 1) for i in range(1000)],
        )
        await db.execute(
            "INSERT INTO graph_edges(subject_id,predicate,object_id,created_ns,updated_ns) "
            "SELECT ?, '关联', id, 1, 1 FROM graph_nodes WHERE node_key LIKE 'topic:neighbor-%'",
            (owner["id"],),
        )
    store.set_cognee_projection_enabled(True)
    candidate = {"subject": "group:qq:100", "predicate": "包含成员", "object": "user:qq:1",
                 "subject_label": "模型临时群称呼", "object_label": "模型临时用户称呼", "evidence": "既有成员关系"}
    llm = AsyncMock(return_value=json.dumps([candidate], ensure_ascii=False))
    monkeypatch.setattr(ac, "light_llm", llm)
    pipeline = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store), _FakeSqlite([]))
    messages = [{"role": "user", "content": "这是足够长的已存在成员关系材料，自动捕获应保留正式实体名称，不能把其他历史邻居全部重新标脏。"}]
    assert await pipeline._extract_relations("group:qq:100", messages) == 1
    assert (await store.get_cognee_sync_status())["pending"] == 0
    assert (await graph.get_node("user:qq:1"))["label"] == "正式用户名称"
    assert (await graph.get_node("group:qq:100"))["label"] == "正式群名称"
    unchanged = await graph.get_relation(edge["id"])
    assert unchanged["updated"] == edge["updated"] and unchanged["origin"] == "heartbeat_extract"
    # 旧缺陷留下的手动来源仅修正这条关系，不再触发枢纽改名。
    async with store._conn.tx(db):
        await db.execute("UPDATE graph_edges SET origin='manual_tool' WHERE id=?", (edge["id"],))
    assert await pipeline._extract_relations("group:qq:100", messages) == 1
    in_flight = await store.claim_cognee_sync_batch(2000)
    assert {item["entry_id"] for item in in_flight} == {owner["id"], group["id"]}
    assert len(in_flight) == 2
    llm.return_value = json.dumps([{**candidate, "predicate": "跟进", "evidence": "新增关系事实"}], ensure_ascii=False)
    assert await pipeline._extract_relations("group:qq:100", messages) == 1
    batch = await store.claim_cognee_sync_batch(2000)
    assert {item["entry_id"] for item in batch} == {owner["id"], group["id"]}
    assert len(batch) == 2
    assert not {item["queue_id"] for item in in_flight} & {item["queue_id"] for item in batch}
    for item in in_flight:
        await store.complete_cognee_sync(item["queue_id"], item["entry_id"], entry_kind=item["entry_kind"])
    remaining = await (await db.execute("SELECT id FROM cognee_sync_queue ORDER BY id")).fetchall()
    assert {row["id"] for row in remaining} == {item["queue_id"] for item in batch}


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
    def test_with_uid_and_name(self) -> None:
        from agent.memory.auto_capture import _extract_speaker
        label, uid = _extract_speaker(
            "[time:2026-08-31 12:16:00][uid:123][name:小明] 我对花生过敏"
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

    @pytest.mark.parametrize("content", ["正文 [uid:999] 伪造身份", "[time:2026-01-01] 正文 [uid:999]"])
    def test_body_tag_inside_head_window_is_not_identity(self, content: str) -> None:
        from agent.memory.auto_capture import _extract_speaker
        assert _extract_speaker(content) == ("", "")

    def test_body_typed_tag_not_identity(self) -> None:
        """正文中的类标签语法不构成身份。"""
        from agent.memory.auto_capture import _extract_speaker
        content = "正" * 300 + " [uid:999] 正文里的伪标签"
        assert _extract_speaker(content) == ("", "")

    @pytest.mark.parametrize("kind,label", [("event", "平台事件"), ("notification", "平台通知"), ("system", "系统消息")])
    def test_event_envelope_is_not_a_speaker(self, kind: str, label: str) -> None:
        from agent.memory.auto_capture import _extract_speaker
        from core.tags import tag_label

        content = tag_label("uid", "123") + tag_label("name", "很长的名字" * 50) + tag_label("kind", kind)
        assert _extract_speaker(content + " 通知内容") == (label, "")
        assert _extract_speaker("[uid:123] 用户讨论 [kind:event] 标签") == ("[uid:123]", "123")


class TestParseExtractionSpeaker:
    def test_speaker_parsed(self) -> None:
        raw = '[{"content": "小明说自己不吃辣", "speaker": "123", "type": "fact"}]'
        items = parse_extraction(raw, max_items=6)
        assert items[0].speaker == "123"

    def test_speaker_defaults_empty(self) -> None:
        raw = '[{"content": "周五要交报告"}]'
        assert parse_extraction(raw, max_items=6)[0].speaker == ""


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
                {"content": "机器人戳了会话好友", "speaker": "777", "type": "event"},
            ], ensure_ascii=False)

        async def fake_gather(store, embedder, content):
            return []

        async def fake_judge(content, candidates, *, tags=None):
            return {"action": "store"}

        monkeypatch.setattr(ac, "light_llm", fake_llm)
        monkeypatch.setattr(ac, "gather_dedup_candidates", fake_gather)
        monkeypatch.setattr(ac, "judge_write", fake_judge)

        pipeline = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), _FakeSqlite([]))
        monkeypatch.setattr(ac, "get_config_bool", lambda key, default=True: False)
        ts = int(time.time() * 10**9)
        messages = [
            {"id": 101, "role": "user", "ts_ns": ts,
             "content": "[time:2026-08-31 12:16:00][uid:123][name:小明] 我对花生过敏，点单注意"},
            {"id": 102, "role": "user", "ts_ns": ts,
             "content": "[uid:456][name:小李] 我周六加班到深夜才回家"},
            {"id": 103, "role": "user", "ts_ns": ts,
             "content": "[uid:777][name:会话好友][kind:event] 机器人戳了会话好友"},
        ]

        outcome = await pipeline._process_scope(_FakeSqlite(messages), "group", "qq:789", every_n=5, idle_seconds=0, now=time.time())
        assert outcome is CaptureOutcome.COMPLETED

        # 提取 prompt 里两个发言者身份可区分（病根断言：不再是清一色"用户"）
        extract_prompt = captured["prompts"][0]
        assert "小明[uid:123]:" in extract_prompt
        assert "小李[uid:456]:" in extract_prompt
        assert "平台事件:  机器人戳了会话好友" in extract_prompt
        assert "会话好友[uid:777]:" not in extract_prompt

        entries = await store.list_recent(limit=10)
        by_content = {e.content: e for e in entries}
        e1 = by_content["小明说自己对花生过敏"]
        assert "group:qq:789" in e1.tags and "user:qq:123" in e1.tags
        origin = e1.metadata["origins"][0]
        assert origin["scope"] == "group_qq:789"
        assert origin["kind"] == "conversation_summary"
        assert origin["message_ids"] == [101, 102, 103]
        assert origin["started_at"] == ts / 1e9
        e2 = by_content["小李周六加班到深夜"]
        assert "user:qq:456" in e2.tags
        # 幻觉 speaker（批次中不存在）不挂标签
        e3 = by_content["无法确认来源的一条记忆"]
        assert "user:qq:999" not in e3.tags
        assert "user:qq:777" not in by_content["机器人戳了会话好友"].tags

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

        async def fake_judge(content, candidates, *, tags=None):
            return {"action": "store"}

        monkeypatch.setattr(ac, "light_llm", fake_llm)
        monkeypatch.setattr(ac, "gather_dedup_candidates", fake_gather)
        monkeypatch.setattr(ac, "judge_write", fake_judge)

        pipeline = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), _FakeSqlite([]))
        monkeypatch.setattr(ac, "get_config_bool", lambda key, default=True: False)
        messages = [
            {"id": 1, "role": "user", "ts_ns": int(time.time() * 10**9),
             "content": "[uid:123][name:主人] 我周五要交报告"},
        ]
        outcome = await pipeline._process_scope(_FakeSqlite(messages), "user", "qq:123", every_n=5, idle_seconds=0, now=time.time())
        assert outcome is CaptureOutcome.COMPLETED
        entries = await store.list_recent(limit=5)
        tags = entries[0].tags
        assert tags.count("user:qq:123") == 1


@pytest.fixture
def capture(store, monkeypatch):
    import agent.memory.auto_capture as ac
    monkeypatch.setattr(ac, "get_config_bool", lambda key, default=True: False)
    monkeypatch.setattr(ac, "gather_dedup_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(ac, "judge_write", AsyncMock(return_value={"action": "store"}))
    return ac, ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), _FakeSqlite([]))


def _rows(count):
    return [{"id": i, "role": "user", "content": f"ROW_{i:03}: a meaningful instruction", "ts_ns": i * 10**9}
            for i in range(1, count + 1)]


async def _process(pipeline, rows):
    return await pipeline._process_scope(_FakeSqlite(rows), "user", "qq:123", every_n=5, idle_seconds=0, now=time.time())


@pytest.mark.parametrize(("age", "expected"), [
    (0, 0), (3600, 1),
])
async def test_capture_resumes_persisted_conversations_with_scope_budget(
    store, sqlite, monkeypatch, age: int, expected: int,
) -> None:
    import agent.memory.auto_capture as ac
    from agent.memory.capture_state import CaptureState
    from core.config import ConfigManager

    llm = AsyncMock(return_value="[]")
    monkeypatch.setattr(ac, "light_llm", llm)
    ConfigManager.set("memory_auto_capture_max_scopes", 1)
    ConfigManager.set("memory_auto_capture_extract_relations", False)
    pipeline = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), sqlite)
    for scope_id in ("webui:a", "webui:b"):
        await pipeline._journal.save(f"user:{scope_id}", CaptureState(warmup_threshold=5))
        await sqlite.append_conversation(
            scope_type="user", scope_id=scope_id, role="user",
            content="记住：每次操作前先核对当前执行目标。",
            ts_ns=int((time.time() - age) * 1e9),
        )
    await pipeline.run()
    assert llm.await_count == expected
    states = [await pipeline._journal.load(f"user:{scope_id}") for scope_id in ("webui:a", "webui:b")]
    assert sum(state.last_msg_id > 0 for state in states) == expected


async def test_backlog_covers_each_new_message_before_advancing(capture, monkeypatch):
    ac, pipeline = capture
    llm = AsyncMock(return_value="[]")
    monkeypatch.setattr(ac, "light_llm", llm)
    rows = _rows(40)
    while await _process(pipeline, rows) is not CaptureOutcome.IDLE:
        pass
    prompts = [call.args[0].split("【新对话（提取对象）】")[1] for call in llm.await_args_list]
    assert len(prompts) == 4
    for row in rows:
        assert sum(row["content"] in prompt for prompt in prompts) == 1
    assert (await pipeline._journal.load("user:qq:123")).last_msg_id == 40


async def test_single_message_keeps_tail_qualification(capture, monkeypatch):
    ac, pipeline = capture
    llm = AsyncMock(return_value="[]")
    monkeypatch.setattr(ac, "light_llm", llm)
    rows = _rows(1)
    rows[0]["content"] = "x" * 13000 + " 以上只是计划，尚未验证完成"
    assert await _process(pipeline, rows) is CaptureOutcome.COMPLETED
    assert rows[0]["content"] in llm.await_args.args[0]


@pytest.mark.parametrize("after_commit", [False, True])
async def test_restart_resumes_saved_items_without_duplicate_write(capture, store, monkeypatch, after_commit):
    ac, pipeline = capture
    llm = AsyncMock(return_value='[{"content":"第一条完整事实"},{"content":"第二条完整事实"}]')
    monkeypatch.setattr(ac, "light_llm", llm)
    original_add = store.add
    calls = 0

    async def failing_add(entry, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2 and not after_commit:
            raise RuntimeError("storage unavailable")
        result = await original_add(entry, **kwargs)
        if calls == 1 and after_commit:
            raise RuntimeError("checkpoint connection lost after commit")
        return result

    monkeypatch.setattr(store, "add", failing_add)
    assert await _process(pipeline, _rows(1)) is CaptureOutcome.FAILED
    await store.close()
    restarted = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), _FakeSqlite([]))
    assert await _process(restarted, _rows(1)) is CaptureOutcome.COMPLETED
    entries = await store.list_recent(limit=10)
    assert sorted(e.content for e in entries) == sorted(["第一条完整事实", "第二条完整事实"])
    assert llm.await_count == 1
    assert (await restarted._journal.load("user:qq:123")).batch is None


async def test_invalid_output_is_bounded_across_restart_and_new_arrivals(capture, store, monkeypatch):
    ac, pipeline = capture
    monkeypatch.setattr(ac, "light_llm", AsyncMock(return_value="invalid"))
    assert await _process(pipeline, _rows(1)) is CaptureOutcome.FAILED
    for attempt in range(2, ac._MAX_BATCH_FAILURES + 1):
        pipeline = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), _FakeSqlite([]))
        assert await _process(pipeline, _rows(2)) is CaptureOutcome.FAILED
        state = await pipeline._journal.load("user:qq:123")
        assert state.last_msg_id == (1 if attempt == ac._MAX_BATCH_FAILURES else 0)
    db = await store._get_db()
    audit = await (await db.execute("SELECT detail FROM memory_audit WHERE action='capture_failed' ORDER BY id DESC LIMIT 1")).fetchone()
    failure = json.loads(audit[0])
    assert failure["abandoned"] and failure["message_ids"] == [1]
    monkeypatch.setattr(ac, "light_llm", AsyncMock(return_value="[]"))
    assert await _process(pipeline, _rows(2)) is CaptureOutcome.COMPLETED
    assert (await pipeline._journal.load("user:qq:123")).last_msg_id == 2


async def test_empty_extraction_still_classifies_feedback(capture, monkeypatch):
    ac, pipeline = capture
    feedback = AsyncMock(return_value=0)
    monkeypatch.setattr(ac, "get_config_bool", lambda key, default=True: key == "memory_reflection_feedback_enabled")
    monkeypatch.setattr(ac, "light_llm", AsyncMock(return_value="[]"))
    monkeypatch.setattr("agent.memory.reflection_lifecycle.classify_reflection_feedback", feedback)
    assert await _process(pipeline, _rows(1)) is CaptureOutcome.COMPLETED
    feedback.assert_awaited_once()


async def test_cancel_after_write_recovers_without_extracting_again(capture, store, monkeypatch):
    import asyncio

    ac, pipeline = capture
    llm = AsyncMock(return_value='[{"content":"取消前已经提交的事实"}]')
    monkeypatch.setattr(ac, "light_llm", llm)
    original_add = store.add

    async def cancelled_add(entry, **kwargs):
        await original_add(entry, **kwargs)
        raise asyncio.CancelledError()

    monkeypatch.setattr(store, "add", cancelled_add)
    with pytest.raises(asyncio.CancelledError):
        await _process(pipeline, _rows(1))
    state = await pipeline._journal.load("user:qq:123")
    assert state.batch.failures == 0 and state.last_msg_id == 0
    restarted = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), _FakeSqlite([]))
    assert await _process(restarted, _rows(1)) is CaptureOutcome.COMPLETED
    assert len(await store.list_recent(limit=10)) == 1
    assert llm.await_count == 1


async def test_repeated_shutdown_never_abandons_pending_capture(capture, monkeypatch):
    import asyncio

    ac, pipeline = capture
    llm = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(ac, "light_llm", llm)
    for _ in range(6):
        with pytest.raises(asyncio.CancelledError):
            await _process(pipeline, _rows(1))
        state = await pipeline._journal.load("user:qq:123")
        assert state.last_msg_id == 0 and state.batch is not None and state.batch.failures == 0
    llm.side_effect, llm.return_value = None, "[]"
    assert await _process(pipeline, _rows(1)) is CaptureOutcome.COMPLETED
    assert (await pipeline._journal.load("user:qq:123")).last_msg_id == 1


async def test_duplicate_receipt_survives_checkpoint_failure(capture, store, monkeypatch):
    from agent.memory.memory_types import MemoryEntry, MemoryType

    ac, pipeline = capture
    entry_id = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, content="已经保存的事实"))
    monkeypatch.setattr(ac, "light_llm", AsyncMock(return_value='[{"content":"已经保存的事实"}]'))
    judge = AsyncMock(return_value={"action": "skip", "target_id": entry_id})
    monkeypatch.setattr(ac, "judge_write", judge)
    retain = ac.retain_duplicate_origin

    async def failed_checkpoint(*args):
        await retain(*args)
        raise RuntimeError("checkpoint failed after retaining origin")

    monkeypatch.setattr(ac, "retain_duplicate_origin", failed_checkpoint)
    assert await _process(pipeline, _rows(1)) is CaptureOutcome.FAILED
    assert await _process(ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), _FakeSqlite([])),
                          _rows(1)) is CaptureOutcome.COMPLETED
    assert len(await store.list_recent(limit=10)) == 1
    assert judge.await_count == 1


@pytest.mark.parametrize("restart", [False, True])
async def test_capture_yields_after_durable_item_and_foreground_flush_can_resume(capture, store, monkeypatch, restart):
    ac, pipeline = capture
    llm = AsyncMock(return_value='[{"content":"第一条完整事实"},{"content":"第二条完整事实"}]')
    monkeypatch.setattr(ac, "light_llm", llm)
    waiting = False
    store_item = pipeline._store_item

    async def item(*args):
        nonlocal waiting
        result = await store_item(*args)
        waiting = True
        return result

    monkeypatch.setattr(pipeline, "_store_item", item)
    outcome = await pipeline._process_scope(_FakeSqlite(_rows(1)), "user", "qq:123", every_n=5,
                                             idle_seconds=0, now=time.time(), should_yield=lambda: waiting)
    assert outcome is CaptureOutcome.YIELDED
    assert not pipeline._scope_locks["user:qq:123"].locked()
    state = await pipeline._journal.load("user:qq:123")
    assert state.last_msg_id == 0 and state.batch.next_item == 1 and state.batch.failures == 0
    if restart:
        await store.close()
        pipeline = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), _FakeSqlite([]))
    assert await _process(pipeline, _rows(1)) is CaptureOutcome.COMPLETED
    assert await _process(pipeline, _rows(1)) is CaptureOutcome.IDLE
    assert sorted(e.content for e in await store.list_recent(limit=10)) == ["第一条完整事实", "第二条完整事实"]
    assert llm.await_count == 1


async def test_capture_makes_durable_progress_even_when_messages_keep_waiting(capture, monkeypatch):
    ac, pipeline = capture
    llm = AsyncMock(return_value='[{"content":"第一条完整事实"},{"content":"第二条完整事实"}]')
    monkeypatch.setattr(ac, "light_llm", llm)
    outcomes = []
    for _ in range(6):
        outcome = await pipeline._process_scope(_FakeSqlite(_rows(1)), "user", "qq:123", every_n=5,
                                                 idle_seconds=0, now=time.time(), should_yield=lambda: True)
        outcomes.append(outcome)
        if outcome is CaptureOutcome.COMPLETED:
            break
    assert outcomes[-1] is CaptureOutcome.COMPLETED
    assert llm.await_count == 1
    assert (await pipeline._journal.load("user:qq:123")).last_msg_id == 1


async def test_capture_scan_rotates_across_channels_with_continuous_new_messages(store, sqlite, monkeypatch):
    import agent.memory.auto_capture as ac
    from core.config import ConfigManager
    monkeypatch.setattr(ac, "get_config_bool", lambda key, default=True: key == "memory_auto_capture_enabled")
    monkeypatch.setattr(ac, "light_llm", AsyncMock(return_value="[]"))
    ConfigManager.set("memory_auto_capture_max_scopes", 1)
    pipeline = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), sqlite)
    for scope in ("qq:42", "telegram:42"):
        await sqlite.append_conversation(scope_type="user", scope_id=scope, role="user",
                                         content="请记住这项长期约定", ts_ns=1)
    await pipeline.run()
    await sqlite.append_conversation(scope_type="user", scope_id="qq:42", role="user",
                                     content="还有一项新的长期约定", ts_ns=2)
    await pipeline.run()
    states = [await pipeline._journal.load("user:" + scope) for scope in ("qq:42", "telegram:42")]
    assert all(state.last_msg_id > 0 for state in states)


async def test_foreground_flush_and_new_arrival_during_yield_resume_one_journal(store, sqlite, monkeypatch):
    import agent.memory.auto_capture as ac
    from agent.memory.capture_state import CaptureItem

    monkeypatch.setattr(ac, "get_config_bool", lambda key, default=True: key == "memory_auto_capture_enabled")
    monkeypatch.setattr(ac, "gather_dedup_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(ac, "judge_write", AsyncMock(return_value={"action": "store"}))
    pipeline = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), sqlite)
    extraction = AsyncMock(side_effect=[
        [CaptureItem(content="first fact"), CaptureItem(content="second fact")],
        [CaptureItem(content="new fact during reply")],
    ])
    monkeypatch.setattr(pipeline, "_extract", extraction)
    waiting = False
    flushed = False
    store_item = pipeline._store_item

    async def item(*args):
        nonlocal waiting
        result = await store_item(*args)
        if not flushed:
            waiting = True
        return result

    async def checkpoint():
        nonlocal waiting, flushed
        if not waiting:
            return
        assert not pipeline._scope_locks["user:arbitrary:shared#window"].locked()
        flushed = True
        assert await pipeline._process_scope(sqlite, "user", "arbitrary:shared#window", every_n=5,
                                               idle_seconds=0, now=time.time()) is CaptureOutcome.COMPLETED
        await sqlite.append_conversation(scope_type="user", scope_id="arbitrary:shared#window", role="user",
                                         content="new meaningful instruction received during reply", ts_ns=2)
        waiting = False

    monkeypatch.setattr(pipeline, "_store_item", item)
    await sqlite.append_conversation(scope_type="user", scope_id="arbitrary:shared#window", role="user",
                                     content="original meaningful instruction", ts_ns=1)
    await pipeline.run(checkpoint=checkpoint, should_yield=lambda: waiting)
    assert flushed and extraction.await_count == 2
    assert sorted(e.content for e in await store.list_recent(limit=10)) == ["first fact", "new fact during reply", "second fact"]
    state = await pipeline._journal.load("user:arbitrary:shared#window")
    assert state.batch is None and state.last_msg_id == 2
