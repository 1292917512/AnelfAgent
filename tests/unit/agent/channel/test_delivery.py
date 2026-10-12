"""投递标签、回执隔离与后台生命周期。"""

import asyncio
import json

import pytest

from agent.channel.delivery import capture_deliveries, delivery_count, delivery_tags, record_delivery
from agent.mind.tools.result_pipeline import _truncate_json_output
from core.tags import etag_all, strip_message_meta_tags


def test_delivery_labels_roundtrip_and_never_escape_outbound():
    scope = "user_webui:用户#chat_1"
    tags = delivery_tags("internal", "pending", scope)
    assert dict(etag_all(tags)) == {
        "delivery_mode": "internal", "delivery_status": "pending", "delivery_scope": scope,
    }
    assert strip_message_meta_tags(tags + "正文[at_uid:123]") == "正文[at_uid:123]"
    assert delivery_count() == 0


def test_nested_execution_does_not_confirm_parent_delivery():
    with capture_deliveries("user_qq:1"):
        with capture_deliveries("reflect:child"):
            record_delivery("user_qq:1")
            assert delivery_count() == 0
        assert delivery_count() == 0
        record_delivery("user_qq:1")
        assert delivery_count() == 1


def test_explicit_silence_is_scoped_and_does_not_claim_sent():
    from agent.mind.mind import _end_reply_tool
    from agent.mind.tool_activation import bind_scope, reset_scope

    scope = "user_webui:web_user#chat_1"
    token = bind_scope(scope)
    try:
        with capture_deliveries(scope):
            result = json.loads(_end_reply_tool(silent=True))
            assert delivery_count() == 0
    finally:
        reset_scope(token)
    assert result["silent"] is True
    assert dict(etag_all(result["delivery"])) == {
        "delivery_mode": "silent", "delivery_status": "skipped", "delivery_scope": scope,
    }


async def test_concurrent_channels_keep_independent_receipts():
    async def run(scope: str) -> int:
        with capture_deliveries(scope):
            await asyncio.sleep(0)
            record_delivery("user_qq:1")
            return delivery_count()

    assert await asyncio.gather(run("user_qq:1"), run("user_telegram:1")) == [1, 0]


async def test_late_background_delivery_cannot_update_finished_capture():
    release = asyncio.Event()

    async def late_send() -> int:
        await release.wait()
        record_delivery("user_qq:1")
        return delivery_count()

    with capture_deliveries("user_qq:1"):
        task = asyncio.create_task(late_send())
    with capture_deliveries("user_qq:1"):
        release.set()
        assert await task == 0
        assert delivery_count() == 0


@pytest.mark.parametrize("limit", [600, 10000])
def test_result_budget_preserves_delivery_tags(limit: int):
    payload = {f"large_{i}": "数据" * 1000 for i in range(60)}
    payload.update(success=True, delivery=delivery_tags("channel", "sent", "group_qq:1"))
    result = _truncate_json_output("send_message", json.dumps(payload), limit)
    assert result is not None
    compacted = json.loads(result)
    assert compacted["success"] is True
    assert compacted["delivery"] == payload["delivery"]
