from unittest.mock import AsyncMock

import pytest

from core.event_bus import EVENT_CHAT_BROADCAST, event_bus
from entities import _sdk


@pytest.mark.parametrize("scope", ["_global", "user_qq:123", "group_webui:123"])
async def test_extension_is_not_broadcast_without_web_conversation(monkeypatch: pytest.MonkeyPatch, scope: str) -> None:
    emit = AsyncMock()
    monkeypatch.setattr(event_bus, "emit", emit)
    monkeypatch.setattr(_sdk, "get_current_scope", lambda: scope)
    assert not await _sdk.emit_chat_extension("share.link", {"url": "/example"}, "Example")
    emit.assert_not_awaited()


async def test_extension_keeps_originating_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    emit = AsyncMock()
    monkeypatch.setattr(event_bus, "emit", emit)
    monkeypatch.setattr(_sdk, "get_current_scope", lambda: "user_webui:web_user#chat-b")
    assert await _sdk.emit_chat_extension("share.link", {"url": "/example"}, "Example")
    emit.assert_awaited_once_with(EVENT_CHAT_BROADCAST, {
        "event": "extension", "chat_id": "chat-b",
        "extension": {"type": "share.link", "payload": {"url": "/example"}, "fallback": "Example"},
    })
