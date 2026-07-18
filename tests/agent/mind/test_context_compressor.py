"""上下文压缩管线（agent.mind.context_compressor）单元测试。"""

from __future__ import annotations

from typing import List
from unittest.mock import AsyncMock

from agent.mind.context_compressor import (
    CompressionConfig,
    ContextCompressor,
)


class _FakeMind:
    """最小 Mind 替身：提供窗口查询与摘要生成。"""

    def __init__(self, context_length: int = 10_000) -> None:
        self._context_length = context_length
        self.summarize_text = AsyncMock(return_value="【摘要】早期对话要点")

    def get_model_context_length(self) -> int:
        return self._context_length


def _make_messages(n: int, prefix: str = "消息") -> List[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"{prefix}{i}"} for i in range(n)]


def _compressor(context_length: int = 10_000, **cfg) -> ContextCompressor:
    config = CompressionConfig(enabled=True, **cfg)
    return ContextCompressor(_FakeMind(context_length), config)


class TestThreshold:
    def test_threshold_from_window(self) -> None:
        c = _compressor(10_000, threshold_percent=0.75)
        assert c.threshold_tokens() == 7_500

    def test_unknown_window_no_threshold(self) -> None:
        c = _compressor(0)
        assert c.threshold_tokens() == 0

    def test_estimate_tokens(self) -> None:
        msgs = [{"role": "user", "content": "a" * 400}]
        assert ContextCompressor.estimate_tokens(msgs) == 100


class TestShouldCompress:
    def test_disabled(self) -> None:
        c = _compressor(10_000)
        c.config = CompressionConfig(enabled=False)
        assert not c.should_compress(_make_messages(100), last_prompt_tokens=999_999)

    def test_real_usage_triggers(self) -> None:
        c = _compressor(10_000, threshold_percent=0.75)
        assert c.should_compress([], last_prompt_tokens=8_000)
        assert not c.should_compress([], last_prompt_tokens=5_000)

    def test_estimated_fallback(self) -> None:
        c = _compressor(1_000, threshold_percent=0.75)
        big = [{"role": "user", "content": "x" * 4000}]  # ~1000 tokens
        assert c.should_compress(big)

    def test_manual_request(self) -> None:
        c = _compressor(10_000)
        c.request_manual("scope1")
        assert c.should_compress([], scope="scope1")
        assert not c.should_compress([], scope="scope2")


class TestCompressMessages:
    async def test_compress_middle(self) -> None:
        c = _compressor(10_000, protect_first_n=2, protect_last_n=3)
        base = [{"role": "system", "content": "系统提示"}] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"历史{i} " + "内容" * 200}
            for i in range(10)
        ]
        chain = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"工具链{i} " + "数据" * 200}
            for i in range(6)
        ]

        new_base, new_chain = await c.compress_messages(base, chain, scope="s")

        # 头部 system 保留
        assert new_base[0]["content"] == "系统提示"
        # 保首轮 2 条
        assert new_base[1]["content"].startswith("历史0")
        # 压缩反馈注入
        feedback = [m for m in new_base if "上下文压缩" in str(m.get("content", ""))]
        assert feedback and "【摘要】" in feedback[0]["content"]
        # 保尾轮
        assert len(new_chain) <= 3 + 6 - 3  # 尾部来自 compressible 末尾
        # 指标记录
        assert c.metrics.total_compressions == 1
        assert c.metrics.last_after_tokens < c.metrics.last_before_tokens

    async def test_manual_request_consumed(self) -> None:
        c = _compressor(10_000)
        c.request_manual("s")
        base = [{"role": "system", "content": "sys"}] + _make_messages(12)
        await c.compress_messages(base, [], scope="s")
        assert not c.should_compress([], scope="s")

    async def test_too_few_messages_skipped(self) -> None:
        c = _compressor(10_000)
        base = [{"role": "system", "content": "sys"}] + _make_messages(3)
        new_base, new_chain = await c.compress_messages(base, [], scope="s")
        assert new_base == base and new_chain == []
        assert c.metrics.total_compressions == 0

    async def test_orphan_tool_messages_removed(self) -> None:
        c = _compressor(10_000, protect_first_n=1, protect_last_n=3)
        base = [{"role": "system", "content": "sys"}] + _make_messages(8)
        chain = [
            {"role": "tool", "tool_call_id": "x", "content": "孤儿结果"},
            {"role": "assistant", "content": "最近回复"},
            {"role": "user", "content": "最新消息"},
        ]
        _, new_chain = await c.compress_messages(base, chain, scope="s")
        assert new_chain[0]["role"] != "tool"

    async def test_fallback_summary_on_llm_failure(self) -> None:
        mind = _FakeMind(10_000)
        mind.summarize_text = AsyncMock(side_effect=RuntimeError("LLM 不可用"))
        c = ContextCompressor(mind, CompressionConfig(enabled=True, protect_first_n=1, protect_last_n=2))
        base = [{"role": "system", "content": "sys"}] + _make_messages(10)
        new_base, _ = await c.compress_messages(base, [], scope="s")
        feedback = [m for m in new_base if "上下文压缩" in str(m.get("content", ""))]
        assert feedback, "摘要失败时应回退确定性摘要"
        assert c.metrics.failures == 1
