"""工具并发安全分级测试（对齐 Claude Code toolOrchestration 语义）。"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import List

import pytest

import agent.mind.tools.think_loop as tl
import entities.filesystem.tools  # noqa: F401  注册 os 组工具（read_file 等并发安全标记）


def _tc(name: str, call_id: str = ""):
    return SimpleNamespace(
        name=name, id=call_id or f"call_{name}",
        arguments="{}", raw={"id": call_id or f"call_{name}", "function": {"name": name, "arguments": "{}"}},
    )


class TestPartition:
    def test_safe_batch_grouped(self):
        calls = [_tc("read_file", "1"), _tc("search_files", "2"),
                 _tc("write_file", "3"), _tc("read_file", "4")]
        parts = tl._partition_tool_calls(calls)
        assert [(p[0], [tc.id for tc in p[1]]) for p in parts] == [
            (True, ["1", "2"]),
            (False, ["3"]),
            (True, ["4"]),
        ]

    def test_unknown_tool_fail_closed(self):
        parts = tl._partition_tool_calls([_tc("no_such_tool_xyz")])
        assert parts[0][0] is False

    def test_all_unsafe_serial(self):
        calls = [_tc("write_file", "1"), _tc("edit_file", "2")]
        parts = tl._partition_tool_calls(calls)
        assert all(not p[0] for p in parts) and len(parts) == 2


class TestExecuteToolCalls:
    @pytest.fixture()
    def mock_mind(self, monkeypatch):
        records: List[str] = []
        delays = {"slow_safe": 0.05}

        async def fake_execute_one(mind, tc, iteration, anything=None):
            records.append(f"start:{tc.id}")
            await asyncio.sleep(delays.get(tc.name, 0))
            records.append(f"end:{tc.id}")
            return json.dumps({"tool": tc.name})

        monkeypatch.setattr(tl, "execute_one_tool", fake_execute_one)
        monkeypatch.setattr(tl, "log_tool_round", lambda *a, **k: None)
        monkeypatch.setattr(tl, "preserve_reasoning_fields", lambda *a, **k: None)
        # 注册一个并发安全的慢工具
        from core.entity import EntityRegistry
        if not EntityRegistry.get("slow_safe"):
            EntityRegistry.register_tool(
                name="slow_safe", func=lambda: "", description="t", group="test",
                params=[], tags=[], source="internal",
                meta={"concurrency_safe": True},
            )
        mind = SimpleNamespace()
        result = SimpleNamespace(content="")
        return mind, result, records

    async def test_safe_tools_run_in_parallel(self, mock_mind):
        mind, result, records = mock_mind
        tool_chain: List[dict] = []
        calls = [_tc("slow_safe", "a"), _tc("slow_safe", "b")]
        start = time.monotonic()
        await tl.execute_tool_calls(mind, tool_chain, result, calls, 1)
        elapsed = time.monotonic() - start
        # 并行：总耗时约 0.05s 而非 0.1s
        assert elapsed < 0.09
        # 顺序保持：tool 消息按调用顺序
        tool_msgs = [m for m in tool_chain if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["a", "b"]

    async def test_unsafe_tools_run_serially(self, mock_mind):
        mind, result, records = mock_mind
        tool_chain: List[dict] = []
        calls = [_tc("write_file", "a"), _tc("write_file", "b")]
        await tl.execute_tool_calls(mind, tool_chain, result, calls, 1)
        # 串行：a 完整结束后 b 才开始
        assert records == ["start:a", "end:a", "start:b", "end:b"]

    async def test_exception_becomes_error_result(self, mock_mind, monkeypatch):
        mind, result, _ = mock_mind

        async def boom(m, tc, i, anything=None):
            raise RuntimeError("炸了")

        monkeypatch.setattr(tl, "execute_one_tool", boom)
        tool_chain: List[dict] = []
        await tl.execute_tool_calls(mind, tool_chain, result, [_tc("write_file", "a")], 1)
        tool_msg = [m for m in tool_chain if m["role"] == "tool"][0]
        assert "炸了" in tool_msg["content"]
