from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from agent.llm.llm_client import LLMClient, LLMClientConfig, LLMNotConfiguredError
from agent.llm.llm_manager import LLMManager
from agent.llm.types import ChatResult


def _client(name: str, *, timeout: float = 60.0) -> LLMClient:
    return LLMClient(LLMClientConfig(
        name=name,
        model=f"{name}-model",
        provider_id=name,
        timeout=timeout,
    ))


@pytest.mark.asyncio
async def test_empty_manager_fails_without_retrying_network(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    with pytest.raises(LLMNotConfiguredError):
        await manager.chat_with_fallback(
            [{"role": "user", "content": "hello"}],
            max_retries=3,
            timeout=1,
        )


@pytest.mark.asyncio
async def test_primary_retries_then_succeeds(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    primary = _client("primary")
    primary.chat = AsyncMock(side_effect=[
        RuntimeError("temporary"),
        ChatResult(content="ok"),
    ])
    monkeypatch.setattr(
        "agent.llm.llm_manager.asyncio.sleep",
        AsyncMock(),
    )

    result = await manager.chat_with_fallback(
        [{"role": "user", "content": "hello"}],
        client=primary,
        max_retries=1,
        timeout=10,
    )

    assert result.content == "ok"
    assert primary.chat.await_count == 2


@pytest.mark.asyncio
async def test_timeout_cancels_underlying_chat(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    # 单次尝试上限取 min(调用方 timeout, 客户端 timeout)，用小值让 wait_for 立即触发取消
    primary = _client("primary", timeout=0.05)
    cancelled = asyncio.Event()

    async def slow_chat(*_args, **_kwargs):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    primary.chat = slow_chat

    with pytest.raises(asyncio.TimeoutError):
        await manager.chat_with_fallback(
            [{"role": "user", "content": "hello"}],
            client=primary,
            max_retries=0,
            timeout=0.01,
        )

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_caller_timeout_caps_attempt(tmp_path) -> None:
    """调用方 timeout 小于客户端配置时真实生效（此前仅作预算下限，是死配置）。"""
    manager = LLMManager(str(tmp_path / "llm.json"))
    primary = _client("primary", timeout=60.0)
    cancelled = asyncio.Event()

    async def slow_chat(*_args, **_kwargs):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    primary.chat = slow_chat

    with pytest.raises(asyncio.TimeoutError):
        await manager.chat_with_fallback(
            [{"role": "user", "content": "hello"}],
            client=primary,
            max_retries=0,
            timeout=0.05,
        )

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_stream_idle_window_honors_caller_timeout(tmp_path) -> None:
    """流式空闲窗口取 min(调用方 timeout, 客户端配置)：调用方更短值生效。"""
    from agent.llm.types import ChatStreamDelta

    manager = LLMManager(str(tmp_path / "llm.json"))

    async def stalled_gen(*_args, **_kwargs):
        yield ChatStreamDelta(content="开头")
        await asyncio.sleep(60)  # 静默悬挂，远超调用方 0.05s 空闲窗口
        yield ChatStreamDelta(content="不会再到达")

    primary = _client("primary", timeout=60.0)
    primary.chat_stream = lambda *_args, **_kwargs: stalled_gen()

    with pytest.raises(asyncio.TimeoutError):
        await manager.chat_with_fallback(
            [{"role": "user", "content": "hi"}],
            client=primary, max_retries=0, timeout=0.05, stream=True,
        )


@pytest.mark.asyncio
async def test_fallback_preserves_tools_and_options(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    primary = _client("primary")
    fallback = _client("fallback")
    primary.chat = AsyncMock(side_effect=RuntimeError("down"))
    fallback.chat = AsyncMock(return_value=ChatResult(content="fallback"))
    manager._clients = {"primary": primary, "fallback": fallback}
    manager._type_priorities = {"chat": ["primary", "fallback"]}

    result = await manager.chat_with_fallback(
        [{"role": "user", "content": "hello"}],
        client=primary,
        options={"temperature": 0.2},
        tools=[{"type": "function", "function": {"name": "lookup"}}],
        tool_choice="auto",
        max_retries=0,
        timeout=10,
    )

    assert result.content == "fallback"
    fallback.chat.assert_awaited_once()
    kwargs = fallback.chat.await_args.kwargs
    assert kwargs["options"] == {"temperature": 0.2}
    assert kwargs["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_invalid_response_retries_or_falls_back(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    primary = _client("primary")
    fallback = _client("fallback")
    primary.chat = AsyncMock(return_value=ChatResult(
        content="",
        finish_reason="error",
    ))
    fallback.chat = AsyncMock(return_value=ChatResult(content="ok"))
    manager._clients = {"primary": primary, "fallback": fallback}
    manager._type_priorities = {"chat": ["primary", "fallback"]}

    result = await manager.chat_with_fallback(
        [{"role": "user", "content": "hello"}],
        client=primary,
        max_retries=0,
        timeout=10,
    )

    assert result.content == "ok"


def test_default_resolution_has_no_side_effect(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    first = _client("first")
    manager._clients = {"first": first}
    manager._type_priorities = {"chat": ["first"]}
    manager._default_chat = ""

    assert manager.get_default() is first
    assert manager.default_name == ""


def test_custom_model_uses_explicit_context_window(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    client = LLMClient(LLMClientConfig(
        name="custom",
        model="custom-model",
        max_tokens=4096,
        context_window=128000,
    ))
    manager._clients = {"custom": client}

    manager._register_unknown_models()

    import litellm
    assert litellm.model_cost[client.config.litellm_model]["max_input_tokens"] == 128000


@pytest.mark.asyncio
async def test_close_closes_all_clients(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    first = _client("first")
    second = _client("second")
    first.close = AsyncMock()
    second.close = AsyncMock()
    manager._clients = {"first": first, "second": second}

    await manager.close()

    first.close.assert_awaited_once()
    second.close.assert_awaited_once()


def test_config_round_trip_preserves_extended_parameters(tmp_path) -> None:
    config_path = tmp_path / "llm.json"
    config_path.write_text(json.dumps({
        "providers": [{
            "id": "provider",
            "base_url": "https://example.test/v1",
            "api_key": "secret",
            "api_type": "openai",
            "models": [{
                "id": "chat",
                "model": "model",
                "context_window": 128000,
                "request_params": {"api_version": "2025-01-01"},
                "extra_body": {"custom": True},
            }],
        }],
        "type_priorities": {"chat": ["chat"]},
        "default_chat": "chat",
    }), encoding="utf-8")

    manager = LLMManager(str(config_path))
    assert manager.save_config() is True
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    model = saved["providers"][0]["models"][0]
    assert model["context_window"] == 128000
    assert model["request_params"] == {"api_version": "2025-01-01"}
    assert model["extra_body"] == {"custom": True}


def test_error_redaction_removes_api_key(tmp_path) -> None:
    config_path = tmp_path / "llm.json"
    config_path.write_text(json.dumps({
        "providers": [{
            "id": "p1",
            "api_key": "top-secret-key",
            "models": [{"id": "default", "model": "model"}],
        }],
        "default_chat": "default",
    }), encoding="utf-8")
    manager = LLMManager(str(config_path))
    client = manager.get_client("default") or LLMClient(LLMClientConfig(
        model="model",
        api_key="top-secret-key",
    ))
    redacted = manager._safe_error(
        RuntimeError("request failed with top-secret-key"),
        client,
    )
    assert "top-secret-key" not in redacted
    assert "****" in redacted


@pytest.mark.asyncio
async def test_internal_purpose_usage_recorded(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内部辅助调用（guardian/summarize 等）的 usage 记入缓存统计与成本账本。"""
    from agent.llm.types import UsageInfo

    manager = LLMManager(str(tmp_path / "llm.json"))
    primary = _client("primary")
    primary.chat = AsyncMock(return_value=ChatResult(
        content="ok",
        usage=UsageInfo(prompt_tokens=100, completion_tokens=10, total_tokens=110),
    ))
    recorded: list[dict] = []
    monkeypatch.setattr(
        "agent.mind.cache_stats.cache_usage_tracker.record",
        lambda usage, *, kind="reply", model="": recorded.append(
            {"kind": kind, "model": model, "total": usage.total_tokens}),
    )

    result = await manager.chat_with_fallback(
        [{"role": "user", "content": "hi"}],
        client=primary, max_retries=0, timeout=10, purpose="guardian",
    )
    assert result.content == "ok"
    assert recorded == [{"kind": "guardian", "model": "primary-model", "total": 110}]


@pytest.mark.asyncio
async def test_stream_slow_but_flowing_completes(tmp_path) -> None:
    """流式通道空闲超时语义：持续出片段的慢流不受墙钟限制（思考中不掐）。"""
    from agent.llm.types import ChatStreamDelta, UsageInfo

    manager = LLMManager(str(tmp_path / "llm.json"))
    # 空闲窗口 0.15s；每 0.03s 一个片段共 0.24s——总时长超窗口但全程有活动
    async def gen(*_args, **_kwargs):
        for i in range(8):
            await asyncio.sleep(0.03)
            yield ChatStreamDelta(content=f"段{i};")
        yield ChatStreamDelta(
            finish_reason="stop",
            usage=UsageInfo(prompt_tokens=3, completion_tokens=7, total_tokens=10),
        )

    primary = _client("primary", timeout=0.15)
    primary.chat_stream = lambda *_args, **_kwargs: gen()
    primary.chat = AsyncMock(side_effect=AssertionError("流式通道不应调用非流式 chat"))

    result = await manager.chat_with_fallback(
        [{"role": "user", "content": "hi"}],
        client=primary, max_retries=0, timeout=10, stream=True,
    )

    assert result.content == "".join(f"段{i};" for i in range(8))
    assert result.finish_reason == "stop"
    assert result.usage is not None and result.usage.total_tokens == 10
    assert result.ttft_ms is not None
    primary.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_stall_times_out_and_falls_back(tmp_path) -> None:
    """流中途静默超过空闲窗口：超时取消底层生成器 → 回退链切换候选。"""
    from agent.llm.types import ChatStreamDelta

    manager = LLMManager(str(tmp_path / "llm.json"))
    cancelled = asyncio.Event()

    async def stalled_gen(*_args, **_kwargs):
        try:
            yield ChatStreamDelta(content="开头")
            await asyncio.sleep(60)  # 静默悬挂
            yield ChatStreamDelta(content="不会再到达")
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def ok_gen(*_args, **_kwargs):
        yield ChatStreamDelta(content="回退结果")

    primary = _client("primary", timeout=0.05)
    primary.chat_stream = lambda *_args, **_kwargs: stalled_gen()
    fallback = _client("fallback")
    fallback.chat_stream = lambda *_args, **_kwargs: ok_gen()
    manager._clients = {"primary": primary, "fallback": fallback}
    manager._type_priorities = {"chat": ["primary", "fallback"]}

    result = await manager.chat_with_fallback(
        [{"role": "user", "content": "hi"}],
        client=primary, max_retries=0, timeout=10, stream=True,
    )

    assert result.content == "回退结果"
    assert cancelled.is_set()


def test_stream_aggregator_merges_fields() -> None:
    """StreamAggregator：正文/思考增量拼接、tool_calls 合并、usage/finish 覆盖。"""
    from agent.llm.stream_aggregate import StreamAggregator
    from agent.llm.types import ChatStreamDelta, ToolCall, UsageInfo

    agg = StreamAggregator()
    agg.feed(ChatStreamDelta(reasoning_content="思"))
    agg.feed(ChatStreamDelta(reasoning_content="考"))
    agg.feed(ChatStreamDelta(content="正"))
    agg.feed(ChatStreamDelta(
        content="文", tool_calls=[ToolCall(id="1", name="f", arguments="{}")],
    ))
    agg.feed(ChatStreamDelta(finish_reason="tool_calls"))
    agg.feed(ChatStreamDelta(
        usage=UsageInfo(prompt_tokens=2, completion_tokens=3, total_tokens=5),
    ))

    result = agg.build(model="m")
    assert result.content == "正文"
    assert result.reasoning_content == "思考"
    assert [c.name for c in result.tool_calls] == ["f"]
    assert result.finish_reason == "tool_calls"
    assert result.usage is not None and result.usage.total_tokens == 5
    assert result.model == "m"
    assert result.ttft_ms is not None


@pytest.mark.asyncio
async def test_internal_usage_record_failure_is_fail_open(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """记账链路异常不影响调用结果。"""
    manager = LLMManager(str(tmp_path / "llm.json"))
    primary = _client("primary")
    from agent.llm.types import UsageInfo
    primary.chat = AsyncMock(return_value=ChatResult(
        content="ok",
        usage=UsageInfo(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    ))
    def _boom(*a, **kw):
        raise RuntimeError("tracker broken")
    monkeypatch.setattr("agent.mind.cache_stats.cache_usage_tracker.record", _boom)

    result = await manager.chat_with_fallback(
        [{"role": "user", "content": "hi"}],
        client=primary, max_retries=0, timeout=10,
    )
    assert result.content == "ok"
