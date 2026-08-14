"""缓存预热（Mind.prewarm_scope_cache）发送边界规整回归测试。

锁定修复：prewarm 曾直接把带 _layer 标签的消息发给供应商（decorate 后
未经 normalize_for_send），内部分类标签泄露可能导致严格校验端点 400。
本测试断言 chat_stream 收到的消息已剥离 _layer。
"""

from __future__ import annotations

from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.mind.mind import Mind


class _FakeStreamDelta:
    pass


def _fake_llm_client(sent: List[dict]) -> MagicMock:
    """构造 LLMClient 替身：chat_stream 捕获收到的 messages。"""
    from agent.llm.llm_client import LLMClient

    client = MagicMock(spec=LLMClient)
    client.config = MagicMock()
    client.config.litellm_model = "anthropic/claude-test"
    client.config.api_type = "anthropic"
    client.config.model = "claude-test"

    async def _stream(messages, *, options=None, tools=None, tool_choice=None):
        sent.extend(messages)
        yield _FakeStreamDelta()

    client.chat_stream = _stream
    return client


@pytest.mark.asyncio
async def test_prewarm_strips_layer_tags(monkeypatch) -> None:
    sent: List[dict] = []
    mind = MagicMock(spec=Mind)
    mind.retriever = None  # 跳过永久块检索
    mind._resolve_entity_scope = MagicMock(return_value="user_test:u1")
    mind._get_models_summary = MagicMock(return_value="")

    async def _layered(*a, **k):
        return ("人设", "工具说明", "便签")

    mind._build_layered_prompts = _layered
    mind.conversation_data = MagicMock()
    mind.conversation_data.get_conversation_summary = AsyncMock(return_value=None)
    mind.conversation_data.get_conversation_record_by_everything = AsyncMock(return_value=[])

    # build_llm_context 返回带 _layer 标签的消息（真实管线行为）
    async def _build_context(**kwargs):
        return [
            {"role": "system", "content": "人设", "_layer": "stable"},
            {"role": "user", "content": "你好", "_layer": "conversation"},
        ]

    mind.pfc = MagicMock()
    mind.pfc.build_llm_context = _build_context
    mind.pfc.get_active_tool_schemas = AsyncMock(return_value=[])
    mind.llm = _fake_llm_client(sent)

    # 绑定真实的 prewarm_scope_cache 实现到替身实例
    await Mind.prewarm_scope_cache(mind, "user", "test:u1")

    assert sent, "chat_stream 应被调用"
    for msg in sent:
        assert "_layer" not in msg, f"_layer 标签不应泄露给供应商: {msg}"


@pytest.mark.asyncio
async def test_prewarm_no_llm_client_noop(monkeypatch) -> None:
    """llm 非 LLMClient 时预热静默退出，不抛异常。"""
    mind = MagicMock(spec=Mind)
    mind.retriever = None
    mind._resolve_entity_scope = MagicMock(return_value="user_test:u1")
    mind._get_models_summary = MagicMock(return_value="")

    async def _layered(*a, **k):
        return ("", "", "")

    mind._build_layered_prompts = _layered
    mind.conversation_data = MagicMock()
    mind.conversation_data.get_conversation_summary = AsyncMock(return_value=None)
    mind.conversation_data.get_conversation_record_by_everything = AsyncMock(return_value=[])

    async def _build_context(**kwargs):
        return [{"role": "system", "content": "x", "_layer": "stable"}]

    mind.pfc = MagicMock()
    mind.pfc.build_llm_context = _build_context
    mind.llm = None  # 非 LLMClient

    # 不应抛异常
    await Mind.prewarm_scope_cache(mind, "user", "test:u1")
