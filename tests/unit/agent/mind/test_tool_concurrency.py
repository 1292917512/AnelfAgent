"""工具并发安全分级测试（连续只读并行、写操作串行语义）。"""

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

    async def test_mode_blocked_tool_gets_synthetic_error(self, mock_mind):
        """模式禁用工具：不执行真实工具，返回合成权限错误（可见性与权限分离）。"""
        mind, result, records = mock_mind
        tool_chain: List[dict] = []
        await tl.execute_tool_calls(
            mind, tool_chain, result, [_tc("send_message", "a")], 1,
            blocked_tools=frozenset({"send_message"}),
        )
        assert records == []  # 真实工具未执行
        tool_msg = [m for m in tool_chain if m["role"] == "tool"][0]
        assert "不可用" in tool_msg["content"]
        assert "permission" in tool_msg["content"]


class TestInterruptedBatch:
    """批内中断：在途取消、占位结果保配对、未执行/被中止语义区分。"""

    @pytest.fixture()
    def mock_mind(self, monkeypatch):
        records: List[str] = []
        delays = {"slow": 5.0, "w1": 5.0}

        async def fake_execute_one(mind, tc, iteration, anything=None):
            records.append(f"start:{tc.id}")
            await asyncio.sleep(delays.get(tc.id, 0))
            records.append(f"end:{tc.id}")
            return json.dumps({"tool": tc.name})

        monkeypatch.setattr(tl, "execute_one_tool", fake_execute_one)
        monkeypatch.setattr(tl, "log_tool_round", lambda *a, **k: None)
        monkeypatch.setattr(tl, "preserve_reasoning_fields", lambda *a, **k: None)
        from core.entity import EntityRegistry
        if not EntityRegistry.get("slow_safe"):
            EntityRegistry.register_tool(
                name="slow_safe", func=lambda: "", description="t", group="test",
                params=[], tags=[], source="internal",
                meta={"concurrency_safe": True},
            )
        return SimpleNamespace(), SimpleNamespace(content=""), records

    async def test_parallel_batch_abort_preserves_pairing(self, mock_mind):
        """快调用已完成保留真实结果；慢调用在途被中止 → 占位结果 + 部分执行标记。"""
        mind, result, records = mock_mind
        tool_chain: List[dict] = []
        abort = asyncio.Event()
        calls = [_tc("slow_safe", "fast"), _tc("slow_safe", "slow")]
        # fast 无延迟立即完成，slow 睡 5s：0.1s 时中断必然命中"在途"窗口

        async def trigger():
            await asyncio.sleep(0.1)
            abort.set()

        trig = asyncio.get_running_loop().create_task(trigger())
        partial = await tl.execute_tool_calls(
            mind, tool_chain, result, calls, 1, abort_event=abort,
        )
        await trig
        assert partial is True
        tool_msgs = [m for m in tool_chain if m["role"] == "tool"]
        # 配对铁律：assistant 的两个调用都有 tool 响应，顺序一致
        assert [m["tool_call_id"] for m in tool_msgs] == ["fast", "slow"]
        assert "slow_safe" in tool_msgs[0]["content"]  # fast 的真实结果
        assert "user_cancel" in tool_msgs[1]["content"]  # slow 的中止占位
        assert "部分执行" in tool_msgs[1]["content"]
        assert "start:slow" in records  # slow 确实启动过
        assert "end:slow" not in records  # 且未完成

    async def test_later_batches_marked_not_executed(self, mock_mind):
        """中断后：后续批次不执行，占位声明"未执行"且不报部分执行。"""
        mind, result, records = mock_mind
        tool_chain: List[dict] = []
        abort = asyncio.Event()

        async def trigger():
            await asyncio.sleep(0.1)
            abort.set()

        trig = asyncio.get_running_loop().create_task(trigger())
        calls = [_tc("write_file", "w1"), _tc("write_file", "w2"), _tc("write_file", "w3")]
        partial = await tl.execute_tool_calls(
            mind, tool_chain, result, calls, 1, abort_event=abort,
        )
        await trig
        # w1 串行批首调用在途被中止；w2/w3 未启动 → 未执行占位
        assert partial is True
        tool_msgs = [m for m in tool_chain if m["role"] == "tool"]
        assert len(tool_msgs) == 3
        assert "部分执行" in tool_msgs[0]["content"]
        assert "未执行" in tool_msgs[1]["content"]
        assert "未执行" in tool_msgs[2]["content"]
        assert "start:w2" not in records and "start:w3" not in records

    async def test_no_abort_returns_false(self, mock_mind):
        mind, result, _ = mock_mind
        partial = await tl.execute_tool_calls(
            mind, [], result, [_tc("write_file", "a")], 1,
        )
        assert partial is False


class TestEarlyToolRunner:
    """流中早执行：只读资格、按 id 回收复用、参数漂移弃用。"""

    @pytest.fixture()
    def mock_mind(self, monkeypatch):
        records: List[str] = []

        async def fake_execute_one(mind, tc, iteration, anything=None):
            records.append(tc.id)
            return json.dumps({"tool": tc.name, "early": True})

        monkeypatch.setattr(tl, "execute_one_tool", fake_execute_one)
        monkeypatch.setattr(tl, "log_tool_round", lambda *a, **k: None)
        monkeypatch.setattr(tl, "preserve_reasoning_fields", lambda *a, **k: None)
        from core.entity import EntityRegistry
        if not EntityRegistry.get("slow_safe"):
            EntityRegistry.register_tool(
                name="slow_safe", func=lambda: "", description="t", group="test",
                params=[], tags=[], source="internal",
                meta={"concurrency_safe": True},
            )
        return SimpleNamespace(), records

    def test_submit_rejects_write_tools(self, mock_mind):
        mind, _ = mock_mind
        runner = tl._EarlyToolRunner(mind, None, 0)
        runner.submit(_tc("write_file", "a"))
        assert runner._tasks == {}

    def test_submit_rejects_leaky_arguments(self, mock_mind, monkeypatch):
        mind, _ = mock_mind
        import agent.mind.tools.think_loop as tl_mod
        monkeypatch.setattr(
            "agent.security.session_token.detect_leak", lambda s: True,
        )
        runner = tl_mod._EarlyToolRunner(mind, None, 0)
        call = SimpleNamespace(
            name="slow_safe", id="a", arguments='{"q": "secret"}',
            raw={"id": "a", "function": {"name": "slow_safe", "arguments": "{}"}},
        )
        runner.submit(call)
        assert runner._tasks == {}

    async def test_collect_reuses_early_result(self, mock_mind):
        mind, records = mock_mind
        runner = tl._EarlyToolRunner(mind, None, 0)
        call = _tc("slow_safe", "a")
        runner.submit(call)
        out = await runner.collect(call)
        assert "early" in out  # 提前执行的真实输出
        assert records == ["a"]  # 只执行一次

        # 工具批回收同 id：不再触发现场执行
        tool_chain: List[dict] = []
        await tl.execute_tool_calls(
            mind, tool_chain, SimpleNamespace(content=""), [call], 1, early=runner,
        )
        assert records == ["a"]
        tool_msg = [m for m in tool_chain if m["role"] == "tool"][0]
        assert "early" in tool_msg["content"]

    async def test_collect_drift_falls_back(self, mock_mind):
        mind, records = mock_mind
        runner = tl._EarlyToolRunner(mind, None, 0)
        runner.submit(_tc("slow_safe", "a"))
        drifted = SimpleNamespace(
            name="slow_safe", id="a", arguments='{"changed": 1}',
            raw={"id": "a", "function": {"name": "slow_safe", "arguments": "{}"}},
        )
        assert await runner.collect(drifted) is tl._EARLY_MISS
