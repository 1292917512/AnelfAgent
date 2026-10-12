"""动态人物上下文保留同名身份，不把登记记录描述成当前在场。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.vision.face import context


async def test_recent_faces_with_same_name_keep_distinct_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    store = SimpleNamespace(list_events=AsyncMock(return_value={"items": [{"faces": [
        {"person_id": 1, "person_name": "Alex", "entity_scope": "user:qq:1"},
        {"person_id": 2, "person_name": "Alex", "entity_scope": "user:webui:1"},
    ]}]}))
    monkeypatch.setattr(context, "get_face_store", lambda: store)
    monkeypatch.setattr(context, "_recent_cache", (0.0, []))
    lines = await context._recent_present_lines()
    assert "[face_scope:user:qq:1]" in lines[0]
    assert "[face_scope:user:webui:1]" in lines[0]
    assert await context._recent_present_lines() == lines
    store.list_events.assert_awaited_once()


async def test_registered_identity_is_rendered_once_after_pending_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = {"identities": [{"id": 7, "name": "Alex", "role": "owner", "entity_scope": "user:qq:1"}],
               "pending_count": 3, "unread_count": 8}
    store = SimpleNamespace(summary=AsyncMock(return_value=summary))
    monkeypatch.setattr(context, "get_face_store", lambda: store)
    monkeypatch.setattr(context, "_recent_present_lines", AsyncMock(return_value=[]))
    result = await context.FaceStatusProvider().provide("user:qq:1")
    assert result and result.content.count("Alex") == 1
    assert "#7 Alex(owner)[face_scope:user:qq:1]" in result.content
    assert result.content.index("未读") < result.content.index("Alex")
    assert "不代表当前在场" in result.content
