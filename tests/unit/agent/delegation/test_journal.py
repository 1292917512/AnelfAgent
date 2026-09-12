"""委托运行日志（agent.delegation.journal）单元测试。

路径由 conftest 的 autouse 夹具隔离到 tmp_path（DELEGATION_DIR 重定向）。
"""

from __future__ import annotations

import time

from agent.delegation import journal


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
        journal.append_ledger(journal.LEDGER_CLOSED, "d2", status="成功")
        unclosed = journal.unclosed_delegations()
        assert [r["id"] for r in unclosed] == ["d1"]
        # 扫描即闭合：再次扫描为空（at-most-once）
        assert journal.unclosed_delegations() == []

    def test_repeated_events_last_wins(self) -> None:
        journal.append_ledger(journal.LEDGER_STARTED, "d1", goal="g")
        journal.append_ledger(journal.LEDGER_CLOSED, "d1", status="成功")
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
