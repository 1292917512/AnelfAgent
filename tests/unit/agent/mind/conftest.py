"""mind 测试共享 fixture：think_loop 出站投递拦截 + 默认会话实体。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def isolate_tool_executor(monkeypatch):
    """在注册表执行边界替换工具，脚本化的并发思维用例各用自己的结果。"""
    from helpers.think_loop_fakes import current_fake_mind

    from core.entity import EntityRegistry

    execute = EntityRegistry.execute_tool_call

    async def execute_call(tc):
        mind = current_fake_mind.get()
        return await mind.execute_tool(tc) if mind is not None else await execute(tc)

    monkeypatch.setattr(EntityRegistry, "execute_tool_call", execute_call)


@pytest.fixture(autouse=True)
async def isolate_context_providers() -> AsyncIterator[None]:
    """思维单测只采集本用例注册的提供者，结束前回收后台采集任务。"""
    from core.context_provider import ContextProviderRegistry

    providers = dict(ContextProviderRegistry._providers)
    ContextProviderRegistry.reset()
    try:
        yield
    finally:
        pending = list(ContextProviderRegistry._inflight.values())
        ContextProviderRegistry.reset()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        ContextProviderRegistry._providers.update(providers)


@pytest.fixture
def anything():
    """默认会话实体替身（SimpleNamespace 形态，够 think_loop 路由使用）。"""
    return SimpleNamespace(adapter_key="test", uid=1, group_id=0,
                           scope_type="user", scope_id="test:1", entity_scope="user_test:1",
                           get_text_content=lambda: "你好")


@pytest.fixture
def deliver_mock(monkeypatch: pytest.MonkeyPatch):
    """监测发送管道，验证执行过程不会自行对外投递。"""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("agent.channel.output_tools.execute_send_action", mock)
    return mock
