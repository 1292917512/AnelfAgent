"""委托运行日志（agent.delegation.journal）单元测试。

路径由 conftest 的 autouse 夹具隔离到 tmp_path（DELEGATION_DIR 重定向）。
"""

from __future__ import annotations

import json
import time

import pytest

from agent.delegation import journal


def _write_ledger(records: list) -> None:
    """以显式 ts 直写账本行（历史折叠测试需要确定性的时间序）。"""
    path = journal.ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        for rec in records:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")


class TestProgress:
    def test_append_and_read(self, tmp_path) -> None:
        journal.append_progress("d1", "委托启动: 目标A")
        journal.append_progress("d1", "工具 search 完成")
        text = journal.progress_path("d1").read_text(encoding="utf-8")
        assert "委托启动: 目标A" in text
        assert "工具 search 完成" in text
        assert text.count("\n") == 2

    def test_empty_id_noop(self) -> None:
        journal.append_progress("", "不应写入")


class TestTranscript:
    def test_sensitive_tool_results_do_not_survive_persistence(self) -> None:
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "secret-call", "function": {
                "name": "absent_tool", "arguments": '{"password":"input-secret"}',
            }}]},
            {"role": "tool", "tool_call_id": "secret-call", "content": '{"notes":"raw-secret"}',
             "_sensitive_result": True},
        ]
        assert journal.save_transcript({"delegation_id": "sensitive", "messages": messages})
        saved = journal.transcript_path("sensitive").read_text(encoding="utf-8")
        assert "raw-secret" not in saved and "input-secret" not in saved
        assert "_sensitive_result" not in saved
        assert messages[1]["content"] == '{"notes":"raw-secret"}'
        assert journal.load_transcript("sensitive") is not None

    def test_save_and_load(self) -> None:
        data = {
            "delegation_id": "d2",
            "goal": "调研",
            "messages": [{"role": "user", "content": "hi"}],
            "output": "结论",
            "completed_reason": "completed",
        }
        assert journal.save_transcript(data) is True
        loaded = journal.load_transcript("d2")
        assert loaded is not None
        assert loaded["goal"] == "调研"
        assert loaded["messages"] == [{"role": "user", "content": "hi"}]

    def test_oversize_degrades_to_non_continuable(self) -> None:
        data = {
            "delegation_id": "d3",
            "messages": [{"role": "user", "content": "x" * 300_000}],
        }
        assert journal.save_transcript(data) is True
        assert journal.load_transcript("d3") is None  # messages 被丢弃 → 不可续跑
        # 档案本体保留（crash 诊断价值）
        import json
        raw = json.loads(journal.transcript_path("d3").read_text(encoding="utf-8"))
        assert raw["continuable"] is False and raw["messages"] is None

    def test_missing_returns_none(self) -> None:
        assert journal.load_transcript("ghost") is None


class TestLedger:
    def test_unclosed_detection_and_close(self) -> None:
        journal.append_ledger(journal.LEDGER_STARTED, "d1", goal="任务一", scope="user_qq:1")
        journal.append_ledger(journal.LEDGER_STARTED, "d2", goal="任务二", scope="user_qq:2")
        journal.append_ledger(journal.LEDGER_CLOSED, "d2", status=journal.STATUS_SUCCESS)
        unclosed = journal.unclosed_delegations()
        assert [r["id"] for r in unclosed] == ["d1"]
        # 扫描即闭合：再次扫描为空（at-most-once）
        assert journal.unclosed_delegations() == []

    def test_recovery_skips_live_runs_and_notices_require_persisted_close(self, tmp_path, monkeypatch):
        monkeypatch.setattr(journal.ConfigPaths, "DELEGATION_DIR", str(tmp_path))
        journal.append_ledger("started", "live")
        journal.append_ledger("started", "past")
        with monkeypatch.context() as patch:
            patch.setattr(journal, "append_ledger", lambda *a, **kw: False)
            assert journal.unclosed_delegations() == []
        assert [r["id"] for r in journal.unclosed_delegations(lambda did: did == "live")] == ["past"]
        assert journal.unclosed_delegations(lambda did: did == "live") == []
        journal.append_ledger("closed", "live", status="success")
        assert journal.unclosed_delegations() == []

    def test_repeated_events_last_wins(self) -> None:
        journal.append_ledger(journal.LEDGER_STARTED, "d1", goal="g")
        journal.append_ledger(journal.LEDGER_CLOSED, "d1", status=journal.STATUS_SUCCESS)
        journal.append_ledger(journal.LEDGER_STARTED, "d1", goal="g2")
        assert [r["id"] for r in journal.unclosed_delegations()] == ["d1"]


class TestRetention:
    def test_purge_expired(self, tmp_path, monkeypatch) -> None:
        from core import path as path_mod

        target = tmp_path / "delegations"
        monkeypatch.setattr(path_mod.ConfigPaths, "DELEGATION_DIR", str(target), raising=False)
        old = target / "old.log"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("x", encoding="utf-8")
        stale = time.time() - 30 * 86400
        import os
        os.utime(old, (stale, stale))
        fresh = target / "fresh.json"
        fresh.write_text("{}", encoding="utf-8")
        keep = target / "ledger.jsonl"
        keep.write_text("", encoding="utf-8")
        # 触发清理（新事件写入顺带执行）
        journal.append_ledger(journal.LEDGER_STARTED, "dx", goal="g")
        assert not old.exists()
        assert fresh.exists()
        assert keep.exists()


    def test_compaction_keeps_open_and_cross_period_attempts(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(journal.ConfigPaths, "DELEGATION_DIR", str(tmp_path))
        now = time.time()
        old = now - 30 * 86400
        _write_ledger([
            {"event": "started", "id": "expired", "ts": old},
            {"event": "closed", "id": "expired", "ts": old},
            {"event": "started", "id": "same", "ts": old, "goal": "finished attempt"},
            {"event": "started", "id": "open", "ts": old},
            {"event": "annotation", "id": "open", "ts": now},
            {"event": "closed", "id": "same", "ts": now, "status": "success"},
            {"event": "started", "id": "same", "ts": now, "goal": "new attempt"},
        ])
        with journal.ledger_path().open("ab") as fp:
            fp.write(b'{"event":\xff')
        journal.append_ledger("started", "after-tail")
        contents = journal.ledger_path().read_text(encoding="utf-8")
        assert "expired" not in contents and "annotation" in contents
        assert journal.recent_history()[0]["goal"] == "finished attempt"
        assert {r["id"] for r in journal.unclosed_delegations()} == {"open", "same", "after-tail"}
        assert not journal.unclosed_delegations()

    def test_retention_zero_and_truncated_tail(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(journal.ConfigPaths, "DELEGATION_DIR", str(tmp_path))
        monkeypatch.setattr(journal, "_retention_days", lambda: 0)
        _write_ledger([{"event": "closed", "id": "expired", "ts": 1}])
        with journal.ledger_path().open("ab") as fp:
            fp.write(b'{"event":')
        journal.append_ledger("started", "after-tail")
        assert b"expired" in journal.ledger_path().read_bytes()
        assert [r["id"] for r in journal.unclosed_delegations()] == ["after-tail"]

    def test_append_during_compaction_is_preserved(self, tmp_path, monkeypatch) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import contextmanager
        from threading import Event
        monkeypatch.setattr(journal.ConfigPaths, "DELEGATION_DIR", str(tmp_path))
        _write_ledger([{"event": "closed", "id": "expired", "ts": 1}])
        entered, appending = Event(), Event()
        original = journal.atomic_binary_writer

        @contextmanager
        def delayed(path):
            with original(path) as writer:
                entered.set()
                assert appending.wait(3)
                yield writer

        def append():
            assert entered.wait(3)
            appending.set()
            journal.append_ledger("started", "second")

        monkeypatch.setattr(journal, "atomic_binary_writer", delayed)
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(journal.append_ledger, "started", "first")
            second = pool.submit(append)
            first.result(5)
            second.result(5)
        assert [r["id"] for r in journal.unclosed_delegations()] == ["first", "second"]

    def test_compaction_write_failure_preserves_original_and_new_record(self, tmp_path, monkeypatch):
        from core import file_utils
        monkeypatch.setattr(journal.ConfigPaths, "DELEGATION_DIR", str(tmp_path))
        _write_ledger([{"event": "closed", "id": "expired", "ts": 1}])

        def fail(*args):
            raise OSError("replace failed")
        monkeypatch.setattr(file_utils.os, "replace", fail)
        journal.append_ledger("started", "new")
        assert [r["id"] for _, r in journal._ledger_records(journal.ledger_path())] == ["expired", "new"]
        assert not list(tmp_path.glob(".atomic_*.tmp"))


class TestProgressTail:
    def test_read_tail_lines(self) -> None:
        # 会话级共享 delegation 目录，用唯一 id 避免与其他用例的进度流交叉
        journal.append_progress("tail-d1", "第一行")
        journal.append_progress("tail-d1", "第二行")
        result = journal.read_progress_tail("tail-d1")
        assert [line.split("] ", 1)[1] for line in result["lines"]] == ["第一行", "第二行"]
        assert result["truncated"] is False

    def test_missing_file_empty(self) -> None:
        assert journal.read_progress_tail("tail-ghost") == {"lines": [], "truncated": False}

    def test_max_lines_truncates(self) -> None:
        for i in range(10):
            journal.append_progress("tail-d2", f"第{i}行")
        result = journal.read_progress_tail("tail-d2", max_lines=3)
        assert len(result["lines"]) == 3
        assert result["truncated"] is True
        assert result["lines"][-1].endswith("第9行")

    def test_oversize_file_tail_read(self) -> None:
        path = journal.progress_path("tail-d3")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * 100_000 + "\n[00:00:01] 末尾行\n", encoding="utf-8")
        result = journal.read_progress_tail("tail-d3")
        assert result["truncated"] is True
        assert result["lines"][-1] == "[00:00:01] 末尾行"


class TestHistory:
    @pytest.fixture(autouse=True)
    def _fresh_dir(self, tmp_path, monkeypatch) -> None:
        """历史折叠对账本全量敏感，隔离到独立目录（与 TestRetention 同模式）。"""
        from core import path as path_mod

        monkeypatch.setattr(
            path_mod.ConfigPaths, "DELEGATION_DIR",
            str(tmp_path / "delegations"), raising=False,
        )

    def test_started_closed_folded(self) -> None:
        _write_ledger([
            {"event": "started", "id": "d1", "ts": 100.0, "goal": "任务一",
             "scope": "user_qq:1", "agent": "", "model": "m1", "adapter_key": "qq"},
            {"event": "closed", "id": "d1", "ts": 130.0, "status": journal.STATUS_SUCCESS},
            {"event": "started", "id": "d2", "ts": 140.0, "goal": "任务二", "scope": "user_qq:2"},
        ])
        items = journal.recent_history()
        # 未闭合（运行中/未扫描）的不入历史
        assert [i["delegation_id"] for i in items] == ["d1"]
        item = items[0]
        assert item["goal"] == "任务一" and item["status"] == journal.STATUS_SUCCESS
        assert item["model"] == "m1" and item["adapter_key"] == "qq"
        assert item["scope"] == "user_qq:1"
        assert item["started_at"] == 100.0 and item["finished_at"] == 130.0
        assert item["duration_seconds"] == 30

    def test_ordered_by_finished_desc_and_limited(self) -> None:
        records: list = []
        for i in range(5):
            records.append({"event": "started", "id": f"d{i}", "ts": 100.0 + i, "goal": f"g{i}"})
            records.append({"event": "closed", "id": f"d{i}", "ts": 200.0 + i, "status": "失败"})
        _write_ledger(records)
        items = journal.recent_history(limit=3)
        assert [i["delegation_id"] for i in items] == ["d4", "d3", "d2"]

    def test_lost_status_preserved(self) -> None:
        _write_ledger([
            {"event": "started", "id": "d1", "ts": 100.0, "goal": "g"},
            {"event": "closed", "id": "d1", "ts": 150.0, "status": "lost"},
        ])
        items = journal.recent_history()
        assert items[0]["status"] == "lost"

    def test_empty_ledger(self) -> None:
        assert journal.recent_history() == []

    def test_nested_metadata_and_outcome_are_preserved(self) -> None:
        journal.append_ledger(
            journal.LEDGER_STARTED, "nested", goal="child task",
            parent_id="parent", depth=2, resumed_from="previous",
        )
        journal.append_ledger(
            journal.LEDGER_CLOSED, "nested", status=journal.STATUS_FAILED,
            summary="Tool timed out", usage={"turns": 3, "input_tokens": 150},
        )
        item = journal.recent_history()[0]
        assert item["parent_id"] == "parent" and item["depth"] == 2
        assert item["resumed_from"] == "previous"
        assert item["summary"] == "Tool timed out"
        assert item["usage"]["turns"] == 3


class TestRecovery:
    async def test_recover_injects_notice_per_scope(self) -> None:
        from agent.delegation import journal
        from agent.delegation.recovery import recover_interrupted_delegations

        journal.append_ledger(
            journal.LEDGER_STARTED, "d1", goal="整理资料", scope="user_qq:1",
        )
        journal.append_ledger(
            journal.LEDGER_STARTED, "d2", goal="翻译文档", scope="user_qq:1",
        )
        journal.append_ledger(
            journal.LEDGER_STARTED, "d3", goal="临时分析", scope="reflect:xyz",
        )

        appended: list = []

        class _Router:
            async def append(self, domain, *, scope_type, scope_id, role, content, **kw):
                appended.append((scope_type, scope_id, content))

        class _ConvData:
            router = _Router()

        class _Mind:
            conversation_data = _ConvData()

        recovered = await recover_interrupted_delegations(_Mind())
        # 同 scope 聚合一条；reflect 域无处投递只记日志
        assert recovered == 1
        assert len(appended) == 1
        scope_type, scope_id, content = appended[0]
        assert scope_type == "user" and scope_id == "qq:1"
        assert "被进程中断" in content
        assert "整理资料" in content and "翻译文档" in content
        # at-most-once：账本已闭合
        assert journal.unclosed_delegations() == []
