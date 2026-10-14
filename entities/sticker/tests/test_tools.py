"""表情发送回执与使用统计的一致性。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from entities import _sdk
from entities.sticker import tools


@pytest.mark.parametrize("receipt,used", [
    ({"success": True}, True), ({"success": False}, False),
    ({"error": "channel unavailable"}, False), ({"ok": False}, False),
    ({}, False), ([], False),
])
async def test_sticker_usage_requires_confirmed_delivery(tmp_path, monkeypatch, receipt, used):
    path = tmp_path / "sticker.png"
    path.write_bytes(b"test")
    store = SimpleNamespace(get_sticker=AsyncMock(return_value={"file_path": str(path)}), touch_use=AsyncMock())
    monkeypatch.setattr(tools, "get_sticker_store", lambda: store)
    monkeypatch.setattr(_sdk, "execute_send_action", AsyncMock(return_value=json.dumps(receipt)))
    result = await tools.send_sticker("sticker-test", "webui", "user-test")
    assert json.loads(result) == receipt
    assert store.touch_use.await_count == int(used)


async def test_usage_failure_preserves_successful_delivery(tmp_path, monkeypatch):
    path = tmp_path / "sticker.png"
    path.write_bytes(b"test")
    store = SimpleNamespace(
        get_sticker=AsyncMock(return_value={"file_path": str(path)}),
        touch_use=AsyncMock(side_effect=RuntimeError("store unavailable")),
    )
    monkeypatch.setattr(tools, "get_sticker_store", lambda: store)
    sender = AsyncMock(return_value='{"success": true}')
    monkeypatch.setattr(_sdk, "execute_send_action", sender)
    assert json.loads(await tools.send_sticker("sticker-test", "webui", "user-test"))["success"] is True
    sender.assert_awaited_once()
