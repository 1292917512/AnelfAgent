"""工具"礼貌失败"选择性落库（agent.mind.tools.think_loop._record_tool_result_failure）。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent.llm.types import ToolCall
from agent.mind.tools.think_loop import _record_tool_result_failure


def _tc(name: str = "web_fetch") -> ToolCall:
    return ToolCall(id="c1", name=name, arguments="{}")


def _mind(record: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(
        memory_store=SimpleNamespace(record_tool_error=record),
    )


async def _flush() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


class TestRecordToolResultFailure:
    async def test_tracked_cause_recorded(self) -> None:
        record = AsyncMock()
        mind = _mind(record)
        result = json.dumps(
            {"error": "请求超时", "cause": "timeout", "retryable": True},
            ensure_ascii=False,
        )
        _record_tool_result_failure(mind, _tc(), result)
        await _flush()

        record.assert_awaited_once()
        kwargs = record.await_args.kwargs
        assert kwargs["tool_name"] == "web_fetch"
        assert kwargs["error_type"] == "timeout"

    async def test_param_trial_and_error_not_recorded(self) -> None:
        record = AsyncMock()
        mind = _mind(record)
        result = json.dumps({"error": "参数无效", "cause": "param"}, ensure_ascii=False)
        _record_tool_result_failure(mind, _tc(), result)
        await _flush()

        record.assert_not_awaited()

    async def test_user_cancel_not_recorded(self) -> None:
        record = AsyncMock()
        mind = _mind(record)
        result = json.dumps({"error": "已取消", "cause": "user_cancel"}, ensure_ascii=False)
        _record_tool_result_failure(mind, _tc(), result)
        await _flush()

        record.assert_not_awaited()

    async def test_success_payload_not_recorded(self) -> None:
        record = AsyncMock()
        mind = _mind(record)
        _record_tool_result_failure(mind, _tc(), json.dumps({"ok": True, "data": "x"}))
        await _flush()

        record.assert_not_awaited()

    async def test_non_json_result_not_recorded(self) -> None:
        record = AsyncMock()
        mind = _mind(record)
        _record_tool_result_failure(mind, _tc(), "纯文本结果")
        await _flush()

        record.assert_not_awaited()

    async def test_no_memory_store_noop(self) -> None:
        mind = SimpleNamespace(memory_store=None)
        # 不抛异常即通过
        _record_tool_result_failure(mind, _tc(), json.dumps({"error": "x", "cause": "timeout"}))
