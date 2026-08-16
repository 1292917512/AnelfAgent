"""mind 测试共享 fixture：think_loop 出站投递拦截 + 默认会话实体。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def anything():
    """默认会话实体替身（SimpleNamespace 形态，够 think_loop 路由使用）。"""
    return SimpleNamespace(adapter_key="test", uid=1, group_id=0)


@pytest.fixture
def deliver_mock(monkeypatch: pytest.MonkeyPatch):
    """拦截纯文本投递，避免真实频道发送。"""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("agent.mind.tools.think_loop.deliver_text", mock)
    return mock
