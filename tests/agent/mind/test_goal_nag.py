"""goal nag 提醒注入测试（对齐 Claude Code todo_reminder 启发式）。"""

from __future__ import annotations

import pytest

from agent.planning import nag


@pytest.fixture(autouse=True)
def clean_state():
    nag.reset("s1")
    yield
    nag.reset("s1")


class TestGoalNag:
    def test_no_nag_without_goal_usage(self):
        for _ in range(20):
            assert nag.maybe_nag("s1") == ""

    def test_no_nag_right_after_use(self):
        nag.note_tools_used("s1", ["create_goal"])
        for _ in range(5):
            assert nag.maybe_nag("s1") == ""

    def test_nag_after_threshold(self):
        nag.note_tools_used("s1", ["create_goal"])
        texts = [nag.maybe_nag("s1") for _ in range(11)]
        fired = [t for t in texts if t]
        assert len(fired) == 1
        assert "目标提醒" in fired[0]
        assert "请勿向用户提及" in fired[0]

    def test_nag_not_repeated_immediately(self):
        nag.note_tools_used("s1", ["update_goal"])
        for _ in range(11):
            nag.maybe_nag("s1")
        # 提醒过一次后，10 轮内不再提醒
        for _ in range(5):
            assert nag.maybe_nag("s1") == ""

    def test_use_resets_timer(self):
        nag.note_tools_used("s1", ["create_goal"])
        for _ in range(9):
            nag.maybe_nag("s1")
        nag.note_tools_used("s1", ["list_goals"])
        for _ in range(9):
            assert nag.maybe_nag("s1") == ""

    def test_non_goal_tools_ignored(self):
        nag.note_tools_used("s1", ["read_file", "web_search"])
        for _ in range(20):
            assert nag.maybe_nag("s1") == ""
