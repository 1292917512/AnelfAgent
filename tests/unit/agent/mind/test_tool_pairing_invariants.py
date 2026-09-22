"""tool_calls 配对铁律的测试：原子落链 / CancelledError 无孤儿 / 长度截断跳过 tool_calls。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Dict, List

import pytest

import agent.mind.tools.think_loop as tl
from agent.mind.tools.round_helpers import (
    _handle_length_recovery,
    _StageOutcome,
    _ThinkLoopCtx,
    _ThinkRoundState,
)
from agent.mind.tools.think_loop import _handle_security_leak


def _tc(name: str, call_id: str = ""):
    return SimpleNamespace(
        name=name, id=call_id or f"call_{name}",
        arguments="{}",
        raw={"id": call_id or f"call_{name}", "type": "function",
             "function": {"name": name, "arguments": "{}"}},
    )


def _make_ctx(tool_chain: List[Dict]) -> _ThinkLoopCtx:
    """构造最小可用 _ThinkLoopCtx（_handle_length_recovery 只需 tool_chain/execution_steps）。"""

    async def _noop_delta(_text: str, _reasoning: bool) -> None:
        return None

    return _ThinkLoopCtx(
        mind=SimpleNamespace(),
        mode=None,  # type: ignore[arg-type]
        anything=None,
        adapter_key="",
        options=None,
        base_messages=[],
        tool_chain=tool_chain,
        execution_steps=[],
        collected_text=[],
        active_tools=[],
        current_scope="",
        interrupts=None,
        background=None,
        wait_per_round=0.0,
        pure_tool_mode=False,
        mode_label="",
        guardrail=SimpleNamespace(),  # type: ignore[arg-type]
        pipeline=SimpleNamespace(),  # type: ignore[arg-type]
        turn_id="",
        delta_emitter=_noop_delta,
        completion=None,
    )


class TestAtomicChainAppend:
    """execute_tool_calls 原子落链：assistant 与全部 tool 响应一次写入。"""

    @pytest.fixture()
    def mock_mind(self, monkeypatch):
        async def fake_execute_one(mind, tc, iteration, anything=None):
            return json.dumps({"tool": tc.name})

        monkeypatch.setattr(tl, "execute_one_tool", fake_execute_one)
        monkeypatch.setattr(tl, "log_tool_round", lambda *a, **k: None)
        monkeypatch.setattr(tl, "preserve_reasoning_fields", lambda *a, **k: None)
        return SimpleNamespace()

    async def test_normal_path_assistant_then_tools(self, mock_mind):
        """正常路径：assistant 在前、N 条 tool 响应依次在后。"""
        tool_chain: List[dict] = []
        result = SimpleNamespace(content="")
        calls = [_tc("read_file", "a"), _tc("search_files", "b")]
        await tl.execute_tool_calls(mock_mind, tool_chain, result, calls, 1)
        assert [m["role"] for m in tool_chain] == ["assistant", "tool", "tool"]
        assert tool_chain[0]["tool_calls"]
        assert tool_chain[1]["tool_call_id"] == "a"
        assert tool_chain[2]["tool_call_id"] == "b"

    async def test_cancel_mid_batch_leaves_no_orphan(self, mock_mind, monkeypatch):
        """batch 中途 CancelledError 透传：tool_chain 不含该轮任何消息（无孤儿）。"""

        async def slow_execute(mind, tc, iteration, anything=None):
            if tc.name == "slow_tool":
                await asyncio.sleep(0.2)
            return json.dumps({"tool": tc.name})

        monkeypatch.setattr(tl, "execute_one_tool", slow_execute)
        from core.entity import EntityRegistry
        for name in ("slow_tool", "fast_tool"):
            if not EntityRegistry.get(name):
                EntityRegistry.register_tool(
                    name=name, func=lambda: "", description="t", group="test",
                    params=[], tags=[], source="internal",
                    meta={"concurrency_safe": True},
                )

        tool_chain: List[dict] = []
        result = SimpleNamespace(content="")
        calls = [_tc("slow_tool", "a"), _tc("fast_tool", "b")]
        task = asyncio.create_task(
            tl.execute_tool_calls(mock_mind, tool_chain, result, calls, 1)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert tool_chain == []

    async def test_per_tool_exception_still_pairs(self, mock_mind, monkeypatch):
        """单条工具异常被消化为合成错误结果，配对不变量仍成立。"""

        async def boom(mind, tc, iteration, anything=None):
            raise RuntimeError("炸了")

        monkeypatch.setattr(tl, "execute_one_tool", boom)
        tool_chain: List[dict] = []
        result = SimpleNamespace(content="")
        await tl.execute_tool_calls(mock_mind, tool_chain, result, [_tc("write_file", "a")], 1)
        assert [m["role"] for m in tool_chain] == ["assistant", "tool"]
        assert "炸了" in tool_chain[1]["content"]


class TestLengthRecoveryPairing:
    """_handle_length_recovery 配对不变量：跳过 tool_calls 时 assistant 仅以纯文本入链。"""

    def _mk_result(self, *, content: str = "", tool_calls=None, finish: str = "length"):
        return SimpleNamespace(
            content=content,
            tool_calls=tool_calls or [],
            finish_reason=finish,
            reasoning_content="",
            raw=None,
        )

    def test_skip_tool_calls_no_orphan_in_recovery_exhausted(self):
        """恢复耗尽分支：上游带 tool_calls 的 result 被跳过，assistant 无 tool_calls 字段。"""
        tool_chain: List[dict] = []
        ctx = _make_ctx(tool_chain)
        state = _ThinkRoundState(max_output_recoveries=3)
        result = self._mk_result(
            content="",
            tool_calls=[_tc("send_message", "x")],
        )
        out = _handle_length_recovery(ctx, state, result)
        assert out is _StageOutcome.CONTINUE
        for msg in tool_chain:
            if msg.get("role") == "assistant":
                assert "tool_calls" not in msg
        assert any("跳过" in (m.get("content") or "") for m in tool_chain if m["role"] == "system")

    def test_in_progress_recovery_drops_tool_calls_too(self):
        """恢复中分支：带 tool_calls 的 result 在续写路径同样被丢弃，assistant 无 tool_calls。"""
        tool_chain: List[dict] = []
        ctx = _make_ctx(tool_chain)
        state = _ThinkRoundState(max_output_recoveries=0)
        result = self._mk_result(
            content="正在写入",
            tool_calls=[_tc("write_file", "y")],
        )
        out = _handle_length_recovery(ctx, state, result)
        assert out is _StageOutcome.CONTINUE
        for msg in tool_chain:
            if msg.get("role") == "assistant":
                assert "tool_calls" not in msg

    def test_non_length_finish_passes_through(self):
        """非 length finish_reason 不进入恢复路径。"""
        tool_chain: List[dict] = []
        ctx = _make_ctx(tool_chain)
        state = _ThinkRoundState()
        result = self._mk_result(content="正常", finish="stop")
        out = _handle_length_recovery(ctx, state, result)
        assert out is _StageOutcome.PROCEED
        assert tool_chain == []


class TestSecurityLeakPairing:
    """_handle_security_leak：触发时丢弃本轮 tool_calls，须显式告知 LLM 下轮重新发起。"""

    async def test_security_leak_with_tool_calls_warns_redo(self, monkeypatch):
        """令牌泄露且本轮带 tool_calls：system 消息附「重新发起」提示，tool_chain 无孤儿。"""
        monkeypatch.setattr(tl, "_detect_token_leak", lambda r, t: True)
        tool_chain: List[dict] = []
        ctx = _make_ctx(tool_chain)
        state = _ThinkRoundState()
        result = SimpleNamespace(
            content="...", tool_calls=[_tc("send_message", "x")],
            finish_reason="stop", reasoning_content="", raw=None,
        )
        out = await _handle_security_leak(ctx, state, result, result.tool_calls)
        assert out is _StageOutcome.CONTINUE
        for msg in tool_chain:
            if msg.get("role") == "assistant":
                assert "tool_calls" not in msg
        sys_msgs = [m for m in tool_chain if m["role"] == "system"]
        assert any("重新发起" in (m.get("content") or "") for m in sys_msgs)

    async def test_security_leak_text_only_unchanged(self, monkeypatch):
        """令牌泄露但本轮无 tool_calls：system 消息不含「重新发起」提示。"""
        monkeypatch.setattr(tl, "_detect_token_leak", lambda r, t: True)
        tool_chain: List[dict] = []
        ctx = _make_ctx(tool_chain)
        state = _ThinkRoundState()
        result = SimpleNamespace(
            content="...", tool_calls=[],
            finish_reason="stop", reasoning_content="", raw=None,
        )
        out = await _handle_security_leak(ctx, state, result, [])
        assert out is _StageOutcome.CONTINUE
        sys_msgs = [m for m in tool_chain if m["role"] == "system"]
        assert not any("重新发起" in (m.get("content") or "") for m in sys_msgs)
