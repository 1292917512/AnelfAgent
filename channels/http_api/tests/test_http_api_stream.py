"""http_api 频道流式面测试（不触网）。

覆盖：SSE 订阅者的 scope 前缀过滤、async 模式下 send_text 的 reply 帧投递。
"""

from __future__ import annotations

import asyncio
import json

from channels.http_api.adapter import HttpApiChannel, _StreamSub


def _frame_json(raw: str) -> dict:
    return json.loads(raw)


class TestPushFrameScopeFilter:
    def test_matching_scope_delivered(self) -> None:
        channel = HttpApiChannel()
        sub = _StreamSub("user_http_api:u1")
        channel._stream_subs.append(sub)
        channel._push_frame({"type": "delta", "delta": "hi"}, "user_http_api:u1")
        channel._push_frame({"type": "delta", "delta": "sub"}, "user_http_api:u1#chat2")
        assert sub.queue.qsize() == 2

    def test_other_scope_filtered_out(self) -> None:
        channel = HttpApiChannel()
        sub = _StreamSub("user_http_api:u1")
        channel._stream_subs.append(sub)
        channel._push_frame({"type": "delta", "delta": "x"}, "user_http_api:u2")
        channel._push_frame({"type": "delta", "delta": "y"}, "user_webui:u1")
        channel._push_frame({"type": "delta", "delta": "z"}, "")
        assert sub.queue.qsize() == 0

    def test_prefix_must_not_swallow_similar_ids(self) -> None:
        """前缀过滤不得让 u10 的帧漏进 u1 的订阅（# 边界）。"""
        channel = HttpApiChannel()
        sub = _StreamSub("user_http_api:u1")
        channel._stream_subs.append(sub)
        channel._push_frame({"type": "delta"}, "user_http_api:u10")
        channel._push_frame({"type": "delta"}, "user_http_api:u1x")
        assert sub.queue.qsize() == 0

    def test_no_subscribers_zero_cost(self) -> None:
        channel = HttpApiChannel()
        channel._push_frame({"type": "delta"}, "user_http_api:u1")  # 不应抛异常


class TestSendTextStreamDelivery:
    def test_async_mode_reply_frame(self) -> None:
        """无 pending future 时经 SSE reply 帧投递最终文本。"""
        channel = HttpApiChannel()
        sub = _StreamSub("user_http_api:u1")
        channel._stream_subs.append(sub)
        result = json.loads(asyncio.run(channel.send_text("u1", "最终答复")))
        assert result["success"] is True
        assert result["delivery"] == "stream"
        frame = _frame_json(sub.queue.get_nowait())
        assert frame["type"] == "reply"
        assert frame["content"] == "最终答复"

    def test_pending_future_takes_precedence(self) -> None:
        """同步模式优先：future 被 resolve，流订阅者不重复收 reply 帧。"""
        channel = HttpApiChannel()

        async def scenario() -> None:
            fut = channel._expect_reply("u1")
            sub = _StreamSub("user_http_api:u1")
            channel._stream_subs.append(sub)
            result = json.loads(await channel.send_text("u1", "答复"))
            assert result["success"] is True
            assert await asyncio.wait_for(fut, timeout=1) == "答复"
            assert sub.queue.empty()

        asyncio.run(scenario())

    def test_no_future_no_subscriber_errors(self) -> None:
        channel = HttpApiChannel()
        result = json.loads(asyncio.run(channel.send_text("nobody", "text")))
        assert result["success"] is False
