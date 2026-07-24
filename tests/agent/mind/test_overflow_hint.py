"""窗口外消息计数（软归档感知）单元测试。

对话窗口只显示最近 max_size 条，但窗口外消息仍完整存于 DB。
溢出提示应告知窗口外真实数量与检索路径，而非沉默丢弃。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent.mind.prefrontal_cortex import PrefrontalCortex


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
