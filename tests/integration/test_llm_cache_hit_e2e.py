"""真 API 缓存命中回归测试（env-gated，默认 skip）。

对齐 dsh request-cache.e2e.ts 的核心断言：同一前缀连续两次调用，第二次
必须命中供应商缓存（cache_read_input_tokens > 0）。这是"前缀字节稳定性"
的最终证明——所有客户端侧的断点装饰/分层冻结/工具数组稳定性设计，最终都
要落到供应商真实回报的缓存命中上。

覆盖两条线：
- Anthropic 断点线（ANTHROPIC_API_KEY）：cache_control 显式断点
- DeepSeek 自动缓存线（DEEPSEEK_API_KEY）：自动前缀缓存

运行：
    LLM_CACHE_E2E=1 ANTHROPIC_API_KEY=sk-... pytest -m integration
注意：会消耗真实 token（两次 max_tokens=1 轻调用 + 前缀构造）。
"""

from __future__ import annotations

import os

import pytest

from agent.llm.config import LLMClientConfig
from agent.llm.llm_client import LLMClient
from agent.llm.prompt_cache import decorate_messages, is_anthropic_wire

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("LLM_CACHE_E2E") != "1",
        reason="set LLM_CACHE_E2E=1 with ANTHROPIC_API_KEY/DEEPSEEK_API_KEY",
    ),
]

# 足够长的稳定前缀（跨过供应商最小缓存粒度：Anthropic 1024 tokens /
# DeepSeek 64-token cache block），确保可缓存
_STABLE_PREFIX = (
    "你是一个测试助手。以下是用于验证前缀缓存命中率的稳定背景知识，"
    "请在后续回答中记住它们。"
) + ("背景知识条目：前缀缓存要求请求前缀字节级稳定，任何 system/tools/消息 "
     "前段的改动都会使缓存失效。" * 80)


def _build_messages(extra_user: str) -> list[dict]:
    """构造带 _layer 标签的消息（模拟上下文管线产物）。"""
    return [
        {"role": "system", "content": _STABLE_PREFIX, "_layer": "stable"},
        {"role": "user", "content": extra_user, "_layer": "conversation"},
    ]


async def _call_twice_collect_usage(client: LLMClient, messages: list[dict]):
    """同一前缀连续两次调用，返回第二次的 usage。"""
    decorated = decorate_messages(
        messages,
        anthropic=is_anthropic_wire(
            client.config.litellm_model or "",
            client.config.api_type or "",
        ),
        api_type=client.config.api_type or "",
    )
    last_usage = None
    for _round in range(2):
        async for delta in client.chat_stream(
            decorated, options={"max_tokens": 1, "include_usage": True},
        ):
            if delta.usage is not None:
                last_usage = delta.usage
    return last_usage


@pytest.mark.asyncio
async def test_anthropic_prefix_cache_hit() -> None:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        pytest.skip("未提供 ANTHROPIC_API_KEY")
    model = os.getenv("LLM_CACHE_E2E_ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
    client = LLMClient(config=LLMClientConfig(
        name="e2e-anthropic",
        api_key=api_key,
        model=model,
        api_type="anthropic",
        max_tokens=1,
    ))
    usage = await _call_twice_collect_usage(client, _build_messages("只回复：ok"))
    assert usage is not None, "端点未返回 usage（无法验证缓存命中）"
    if not usage.cache_observable:
        pytest.skip("端点未回报缓存字段，无法断言命中率")
    assert usage.cache_read_input_tokens > 0, (
        f"第二次调用应命中缓存，实际 cache_read={usage.cache_read_input_tokens} "
        f"prompt={usage.prompt_tokens}"
    )


@pytest.mark.asyncio
async def test_deepseek_prefix_cache_hit() -> None:
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        pytest.skip("未提供 DEEPSEEK_API_KEY")
    model = os.getenv("LLM_CACHE_E2E_DEEPSEEK_MODEL", "deepseek-chat")
    client = LLMClient(config=LLMClientConfig(
        name="e2e-deepseek",
        base_url="https://api.deepseek.com",
        api_key=api_key,
        model=model,
        api_type="openai",
        max_tokens=1,
    ))
    usage = await _call_twice_collect_usage(client, _build_messages("只回复：ok"))
    assert usage is not None, "端点未返回 usage（无法验证缓存命中）"
    if not usage.cache_observable:
        pytest.skip("端点未回报缓存字段，无法断言命中率")
    assert usage.cache_read_input_tokens > 0, (
        f"第二次调用应命中缓存，实际 cache_read={usage.cache_read_input_tokens} "
        f"prompt={usage.prompt_tokens}"
    )
