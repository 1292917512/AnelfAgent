"""上下文注入内容块测试：平台命令方言 / goal nag 提醒 / 窗口外溢出提示。"""

from __future__ import annotations

import platform
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.mind import prefrontal_cortex
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.planning import nag

# ==================================================================
# _env_info_block 平台命令方言提示（BSD 用户态注入 / Linux 不注入）
# ==================================================================

def _block(monkeypatch: pytest.MonkeyPatch, system: str) -> str:
    monkeypatch.setattr(platform, "system", lambda: system)
    return prefrontal_cortex._env_info_block()


def test_bsd_dialect_hint_on_darwin(monkeypatch: pytest.MonkeyPatch):
    block = _block(monkeypatch, "Darwin")
    assert "平台: darwin" in block
    assert "BSD" in block
    assert "-printf" in block


def test_no_bsd_dialect_hint_on_linux(monkeypatch: pytest.MonkeyPatch):
    block = _block(monkeypatch, "Linux")
    assert "平台: linux" in block
    assert "BSD" not in block


def test_env_block_contains_python_summary():
    """[运行环境] 注入工作区/宿主环境事实，且进程级缓存保证字节稳定。"""
    block = prefrontal_cortex._env_info_block()
    assert "你的操作环境" in block and "宿主环境" in block
    assert prefrontal_cortex._env_info_block() == block


# ==================================================================
# goal nag 提醒注入（对齐 Claude Code todo_reminder 启发式）
# ==================================================================

@pytest.fixture(autouse=True)
def clean_nag_state():
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


# ==================================================================
# 窗口外消息计数（软归档感知）
# ==================================================================

def _pfc(record_count: int, total_count: int, max_size: int = 3) -> PrefrontalCortex:
    records = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
        for i in range(record_count)
    ]
    conversation_data = SimpleNamespace(
        max_size=max_size,
        get_conversation_record_by_everything=AsyncMock(return_value=records),
        count_messages=AsyncMock(return_value=total_count),
    )
    return PrefrontalCortex(
        everything_data=SimpleNamespace(),
        conversation_data=conversation_data,
    )


class TestOverflowHint:
    async def test_hidden_count_injected(self) -> None:
        """窗口已满且存在窗口外历史：提示包含真实隐藏数量。"""
        pfc = _pfc(record_count=3, total_count=10)
        msgs = await pfc.build_llm_context(
            memory_msgs=[], anything=SimpleNamespace(uid=1, group_id=0),
        )
        hint = [m for m in msgs if "上下文溢出" in str(m.get("content", ""))]
        assert hint, "窗口满时应注入溢出提示"
        assert "7 条更早消息在窗口外" in hint[0]["content"]
        assert "recall_conversation" in hint[0]["content"]
        assert "lookup_message" in hint[0]["content"]

    async def test_no_hidden_count_when_exact(self) -> None:
        """窗口刚好满但无窗口外历史：提示不包含隐藏数量。"""
        pfc = _pfc(record_count=3, total_count=3)
        msgs = await pfc.build_llm_context(
            memory_msgs=[], anything=SimpleNamespace(uid=1, group_id=0),
        )
        hint = [m for m in msgs if "上下文溢出" in str(m.get("content", ""))]
        assert hint
        assert "条更早消息在窗口外" not in hint[0]["content"]

    async def test_no_hint_below_window(self) -> None:
        """窗口未满：不注入溢出提示。"""
        pfc = _pfc(record_count=2, total_count=2)
        msgs = await pfc.build_llm_context(
            memory_msgs=[], anything=SimpleNamespace(uid=1, group_id=0),
        )
        assert not [m for m in msgs if "上下文溢出" in str(m.get("content", ""))]

    async def test_count_failure_degrades_gracefully(self) -> None:
        """计数查询失败：提示仍注入，仅缺少数量信息。"""
        pfc = _pfc(record_count=3, total_count=0)
        pfc._conversation_data.count_messages = AsyncMock(side_effect=RuntimeError("db down"))
        msgs = await pfc.build_llm_context(
            memory_msgs=[], anything=SimpleNamespace(uid=1, group_id=0),
        )
        hint = [m for m in msgs if "上下文溢出" in str(m.get("content", ""))]
        assert hint and "条更早消息在窗口外" not in hint[0]["content"]
