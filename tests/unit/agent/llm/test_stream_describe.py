"""流式空闲超时聚合与视觉识别流式通道的单元测试。

覆盖：
- aggregate_with_idle_timeout：慢而不断的流完成、静默悬挂判死、吊流天花板
- describe_images：走流式通道（非 chat）、长思考（总时长超 timeout）不判死、
  空结果仍视为失败供候选链回退
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from agent.llm.llm_client import LLMClient, LLMClientConfig
from agent.llm.stream_aggregate import aggregate_with_idle_timeout
from agent.llm.types import ChatStreamDelta, ImageContent


def _vision_client(timeout: float = 0.3) -> LLMClient:
    return LLMClient(LLMClientConfig(
        name="qwen-test",
        model="qwen-vision-test",
        provider_id="qwen-test",
        supports_vision=True,
        timeout=timeout,
    ))


async def _flowing_gen(total: float, gap: float) -> AsyncIterator[ChatStreamDelta]:
    """慢而不断的流：每 gap 秒吐一个增量，持续 total 秒（模拟长思考）。"""
    import time
    deadline = time.monotonic() + total
    yield ChatStreamDelta(reasoning_content="思考开始")
    while time.monotonic() < deadline:
        await asyncio.sleep(gap)
        yield ChatStreamDelta(reasoning_content="…")
    yield ChatStreamDelta(content="识别完成", finish_reason="stop")


async def _stalled_gen() -> AsyncIterator[ChatStreamDelta]:
    """静默悬挂：首个增量后长期不吐（模拟端点挂死）。"""
    yield ChatStreamDelta(content="开头")
    await asyncio.sleep(10)
    yield ChatStreamDelta(content="不会再到达")  # pragma: no cover


async def _drip_forever_gen() -> AsyncIterator[ChatStreamDelta]:
    """吊流：持续让出事件循环并产出增量，避免真实定时延迟干扰天花板测试。"""
    while True:
        await asyncio.sleep(0)
        yield ChatStreamDelta(content="滴")


class TestAggregateWithIdleTimeout:
    @pytest.mark.asyncio
    async def test_slow_but_flowing_completes(self) -> None:
        """总时长远超空闲窗口，但增量间隔始终小于窗口——完成不判死。"""
        result = await aggregate_with_idle_timeout(
            _flowing_gen(total=0.5, gap=0.05), idle_timeout=0.2, model="m",
        )
        assert result.content == "识别完成"
        assert result.finish_reason == "stop"
        assert "思考开始" in result.reasoning_content

    @pytest.mark.asyncio
    async def test_stall_times_out(self) -> None:
        """完全静默超过空闲窗口——判死。"""
        with pytest.raises(asyncio.TimeoutError):
            await aggregate_with_idle_timeout(
                _stalled_gen(), idle_timeout=0.1,
            )

    @pytest.mark.asyncio
    async def test_drip_stream_hits_ceiling(self) -> None:
        """每 chunk 都有活动的吊流——总时长天花板兜底判死。"""
        with pytest.raises(asyncio.TimeoutError, match="总时长超限"):
            await aggregate_with_idle_timeout(
                _drip_forever_gen(), idle_timeout=0.1, ceiling_mult=3,
            )

    async def test_empty_keepalives_do_not_reset_first_response_deadline(self) -> None:
        closed = asyncio.Event()

        async def keepalives() -> AsyncIterator[ChatStreamDelta]:
            try:
                while True:
                    yield ChatStreamDelta(content="")
                    await asyncio.sleep(0.005)
            finally:
                closed.set()

        with pytest.raises(asyncio.TimeoutError, match="首响应超时"):
            await aggregate_with_idle_timeout(
                keepalives(), idle_timeout=1, first_response_timeout=0.03,
            )
        assert closed.is_set()

    async def test_first_reasoning_releases_first_response_deadline(self) -> None:
        result = await aggregate_with_idle_timeout(
            _flowing_gen(total=0.2, gap=0.02), idle_timeout=0.1, first_response_timeout=0.03,
        )
        assert result.content == "识别完成"


class TestDescribeImagesStreaming:
    @pytest.mark.asyncio
    async def test_uses_stream_channel_not_chat(self, monkeypatch) -> None:
        """视觉识别必须走流式通道：chat() 被调用即失败。"""

        async def _fail_chat(*_a, **_kw):
            raise AssertionError("describe_images 不应走非流式 chat()")

        client = _vision_client()
        monkeypatch.setattr(client, "chat", _fail_chat)
        monkeypatch.setattr(
            client, "chat_stream",
            lambda *_a, **_kw: _flowing_gen(total=0.2, gap=0.03),
        )
        text = await client.describe_images(
            [ImageContent(data="aGk=", is_url=False)], prompt="描述",
        )
        assert text == "识别完成"

    @pytest.mark.asyncio
    async def test_long_thinking_survives_timeout_window(self, monkeypatch) -> None:
        """长思考（总时长 > 配置 timeout）只要增量不断流即完成。"""
        client = _vision_client(timeout=0.2)
        monkeypatch.setattr(
            client, "chat_stream",
            lambda *_a, **_kw: _flowing_gen(total=0.6, gap=0.04),
        )
        text = await client.describe_images(
            [ImageContent(data="aGk=", is_url=False)], prompt="描述",
        )
        assert text == "识别完成"

    @pytest.mark.asyncio
    async def test_empty_result_raises_for_fallback(self, monkeypatch) -> None:
        """空产出仍视为失败，供视觉候选链回退到下一模型。"""

        async def _empty_gen() -> AsyncIterator[ChatStreamDelta]:
            yield ChatStreamDelta(reasoning_content="只思考")
            yield ChatStreamDelta(content="", finish_reason="stop")

        client = _vision_client()
        monkeypatch.setattr(client, "chat_stream", lambda *_a, **_kw: _empty_gen())
        with pytest.raises(RuntimeError, match="空结果"):
            await client.describe_images(
                [ImageContent(data="aGk=", is_url=False)], prompt="描述",
            )

    @pytest.mark.asyncio
    async def test_stalled_stream_raises_for_fallback(self, monkeypatch) -> None:
        """静默悬挂判死，异常上抛供候选链回退。"""
        client = _vision_client(timeout=0.1)
        monkeypatch.setattr(client, "chat_stream", lambda *_a, **_kw: _stalled_gen())
        with pytest.raises(asyncio.TimeoutError):
            await client.describe_images(
                [ImageContent(data="aGk=", is_url=False)], prompt="描述",
            )

    @pytest.mark.parametrize("finish", ["length", "", "error", "content_filter"])
    async def test_incomplete_description_is_not_success(self, monkeypatch: pytest.MonkeyPatch, finish: str) -> None:
        async def incomplete() -> AsyncIterator[ChatStreamDelta]:
            yield ChatStreamDelta(content="只输出了一部分", finish_reason=finish)

        client = _vision_client()
        monkeypatch.setattr(client, "chat_stream", lambda *_a, **_kw: incomplete())
        with pytest.raises(RuntimeError, match="未完整结束"):
            await client.describe_images([ImageContent("aGk=")])

    async def test_auxiliary_effort_override_does_not_change_model_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _vision_client()
        client.config.reasoning_effort = "high"
        seen: list[dict] = []

        async def stream(messages: list[dict], **kwargs: Any) -> AsyncIterator[ChatStreamDelta]:
            seen.append(kwargs["options"])
            yield ChatStreamDelta(content="ok", finish_reason="stop")

        monkeypatch.setattr(client, "chat_stream", stream)
        await client.describe_images([ImageContent("aGk=")])
        await client.describe_images([ImageContent("aGk=")], options={"reasoning_effort": "low"})
        assert [options["reasoning_effort"] for options in seen] == ["off", "low"]
        assert client.config.reasoning_effort == "high"

    def test_empty_keepalive_does_not_count_as_first_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        from agent.llm.stream_aggregate import StreamAggregator

        times = iter([10.0, 10.05])
        monkeypatch.setattr("agent.llm.stream_aggregate.time", SimpleNamespace(monotonic=lambda: next(times)))
        aggregator = StreamAggregator()
        aggregator.feed(ChatStreamDelta())
        assert aggregator.build().ttft_ms is None
        aggregator.feed(ChatStreamDelta(content="ok", finish_reason="stop"))
        assert aggregator.build().ttft_ms == pytest.approx(50)
