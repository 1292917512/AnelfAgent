"""mind 测试共享 fixture：think_loop 出站投递拦截 + 默认会话实体。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


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
    return SimpleNamespace(adapter_key="test", uid=1, group_id=0, get_text_content=lambda: "你好")


@pytest.fixture
def deliver_mock(monkeypatch: pytest.MonkeyPatch):
    """监测发送管道，验证执行过程不会自行对外投递。"""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("agent.channel.output_tools.execute_send_action", mock)
    return mock
