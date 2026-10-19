"""执行日志测试：环形缓冲隔离/深度 + 入库简版裁剪 + 查询工具。

入库简版回归诉求：长轮次（25 次工具）的 [已执行操作摘要] 整段入库，
每次窗口加载都吃上下文；新语义为历史只留尾部 5 条 + 查询指引，
完整清单经 get_execution_log 按需取回。
"""

from __future__ import annotations

from agent.mind.execution.reply_finalize import (
    _build_execution_summary,
    _compact_summary_for_history,
    format_undelivered_reply,
    parse_execution_summary,
)
from agent.mind.tools import execution_log as elog


def _full_summary(n: int) -> str:
    lines = [f"  #{i} tool_{i}(arg={i}) → ok" for i in range(1, n + 1)]
    return f"[已执行操作摘要] 本轮共执行 {n} 次工具\n" + "\n".join(lines)


class TestRingBuffer:
    def setup_method(self):
        elog.reset()

    def test_record_and_recent_order(self):
        """recent 返回新到旧，turns 钳制在可用范围内。"""
        for i in range(1, 4):
            elog.record("user_qq:1", f"summary-{i}", iterations=i)

        entries = elog.recent("user_qq:1", 3)
        assert [e.summary for e in entries] == ["summary-3", "summary-2", "summary-1"]
        assert elog.recent("user_qq:1", 99)[0].summary == "summary-3"
        assert elog.recent("user_qq:1") == elog.recent("user_qq:1", 1)

    def test_scope_isolation_and_depth(self):
        """缓冲按会话隔离；超出深度自动淘汰最老条目。"""
        for i in range(elog.LOG_DEPTH + 3):
            elog.record("user_qq:1", f"summary-{i}")
        elog.record("group_qq:2", "other")

        assert len(elog.recent("user_qq:1", 99)) == elog.LOG_DEPTH
        assert elog.recent("user_qq:1", 99)[-1].summary == "summary-3"
        assert [e.summary for e in elog.recent("group_qq:2", 1)] == ["other"]

    def test_empty_scope_not_recorded(self):
        """无会话归属（空 scope）的调用不进缓冲。"""
        elog.record("", "summary")
        assert elog.recent("", 5) == []


class TestHistoryCompact:
    def test_undelivered_note_survives_long_tool_summary(self):
        """投递事实在完整摘要及历史裁剪后保留，正文换行不伪造额外摘要条目。"""
        chain = [{"role": "tool", "tool_call_id": f"tc_{i}", "content": "结果" * 100} for i in range(100)]
        note = format_undelivered_reply("第一行\n第二行", "Jev 拒绝投递")
        summary = _build_execution_summary(chain, ["普通过程记录", note])
        assert "已省略" in summary
        assert summary.endswith(note)
        compact = _compact_summary_for_history(summary)
        assert compact.endswith(note)
        assert '"第一行\\n第二行"' in compact
        assert "普通过程记录" not in summary
        parsed = parse_execution_summary(compact)
        assert parsed is not None and parsed["count"] == 100

    def test_short_summary_unchanged(self):
        """条数不超上限时原样入库（首行 + 5 条以内）。"""
        summary = _full_summary(5)
        assert _compact_summary_for_history(summary) == summary

    def test_long_summary_tail_only(self):
        """长摘要只保留统计头 + 最近 5 条 + 省略指引。"""
        summary = _full_summary(25)
        compact = _compact_summary_for_history(summary)

        compact_lines = compact.splitlines()
        assert len(compact_lines) == 6
        assert compact_lines[0].startswith("[已执行操作摘要] 本轮共执行 25 次工具")
        assert "get_execution_log" in compact_lines[0]
        assert "此前 20 条已省略" in compact_lines[0]
        assert compact_lines[1:] == summary.splitlines()[-5:]
        # 尾部保留的是最后一次操作
        assert compact_lines[-1].startswith("  #25")


class TestToolSurface:
    def setup_method(self):
        elog.reset()

    async def test_tool_requires_session_scope(self):
        """无会话上下文时给出可读说明而非报错。"""
        out = await elog.get_execution_log()
        assert "无会话上下文" in out

    async def test_tool_returns_recorded_summary(self):
        """绑定会话 scope 后可取回完整摘要。"""
        from core.conversation_scope import bind_scope, reset_scope

        token = bind_scope("user_qq:1")
        try:
            out = await elog.get_execution_log()
            assert "暂无执行日志" in out

            elog.record("user_qq:1", _full_summary(25))
            out = await elog.get_execution_log()
            assert "  #1 " in out and "  #25 " in out
        finally:
            reset_scope(token)
