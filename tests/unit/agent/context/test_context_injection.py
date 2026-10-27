"""上下文注入内容块测试：平台命令方言 / goal nag 提醒 / 窗口外溢出提示。"""

from __future__ import annotations

import platform
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.context import assembly
from agent.context.assembly import ContextAssembly
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.mind.tool_assembly import ToolAssembly
from agent.planning import nag


@pytest.mark.parametrize("profile_chars,evidence_chars", [(6000, 500), (6000, 6000), (0, 7000), (7000, 0), (100, 100)])
def test_profile_and_retrieval_keep_space_without_changing_originals(profile_chars, evidence_chars):
    from copy import deepcopy

    from agent.context.preparation import allocate_recollection
    from core.config import ConfigManager

    ConfigManager.set("memory_inject_max_chars", 6000)
    profiles = [{"role": "system", "content": "p" * profile_chars}]
    evidence = [{"role": "system", "content": "e" * evidence_chars, "_source": {"origin": "memory_recall", "id": "mem:17"}}]
    originals = deepcopy((profiles, evidence))
    allocation = allocate_recollection(profiles, evidence)
    assert sum(len(m["content"]) for m in allocation.profiles + allocation.evidence) <= 6000
    if profile_chars:
        assert allocation.profiles
    if evidence_chars:
        assert allocation.evidence and allocation.evidence[0]["_source"] == evidence[0]["_source"]
    if evidence_chars < 3000:
        assert allocation.evidence == ([evidence[0]] if evidence_chars else [])
    assert (profiles, evidence) == originals
    if profile_chars + evidence_chars <= 6000:
        assert not allocation.omission_notice()
    else:
        assert "不代表完整历史" in allocation.omission_notice()


@pytest.mark.parametrize("budget", [0, 1, 200, 201, 499, 6000])
def test_budget_includes_partial_marker_and_preserves_source_order(budget):
    from agent.context.preparation import allocate_recollection
    from core.config import ConfigManager

    ConfigManager.set("memory_inject_max_chars", budget)
    evidence = [{"content": "first-identity " + "a" * 3000}, {"content": "second-identity " + "b" * 3000}]
    allocation = allocate_recollection([{"content": "p" * 6000}], evidence)
    assert sum(len(m["content"]) for m in allocation.profiles + allocation.evidence) <= budget
    assert allocation.evidence_usage.omitted_blocks + allocation.evidence_usage.injected_blocks == 2
    assert allocation.evidence_usage.truncated_blocks <= allocation.evidence_usage.injected_blocks
    if allocation.evidence:
        assert allocation.evidence[0]["content"].startswith("first-identity")

# ==================================================================
# _env_info_block 平台命令方言提示（BSD 用户态注入 / Linux 不注入）
# ==================================================================

def _block(monkeypatch: pytest.MonkeyPatch, system: str) -> str:
    monkeypatch.setattr(platform, "system", lambda: system)
    return assembly._env_info_block()


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
    block = assembly._env_info_block()
    assert "你的操作环境" in block and "宿主环境" in block
    assert assembly._env_info_block() == block


# ==================================================================
# goal nag 提醒注入（目标长期未触碰的启发式提醒）
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

def _pfc(record_count: int, total_count: int, max_size: int = 3) -> ContextAssembly:
    records = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
        for i in range(record_count)
    ]
    conversation_data = SimpleNamespace(
        max_size=max_size,
        get_conversation_record_by_everything=AsyncMock(return_value=records),
        count_messages=AsyncMock(return_value=total_count),
    )
    pfc = PrefrontalCortex(
        everything_data=SimpleNamespace(),
        conversation_data=conversation_data,
    )
    return ContextAssembly(pfc, ToolAssembly(), conversation_data=conversation_data)


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
