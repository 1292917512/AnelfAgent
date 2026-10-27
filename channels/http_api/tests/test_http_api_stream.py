"""http_api 频道流式面测试（不触网）。

覆盖：SSE 订阅者的 scope 前缀过滤、async 模式下 send_text 的 reply 帧投递。
"""

from __future__ import annotations

import asyncio
import json

import pytest

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
    def test_private_group_and_subsession_replies_do_not_cross(self) -> None:
        channel = HttpApiChannel()

        async def scenario() -> None:
            private = channel._expect_reply("42")
            group = channel._expect_reply("42", channel_type="group")
            private_sub = _StreamSub("user_http_api:42")
            first = _StreamSub("group_http_api:42#one", include_children=False)
            second = _StreamSub("group_http_api:42#two", include_children=False)
            channel._stream_subs.extend([private_sub, first, second])
            await channel.send_text("42", "group", channel_type="group")
            assert await group == "group"
            assert not private.done()
            await channel.send_text("42", "one", session_id="one", channel_type="group")
            assert json.loads(first.queue.get_nowait())["content"] == "one"
            assert second.queue.empty() and private_sub.queue.empty()
            private.cancel()

        asyncio.run(scenario())

    def test_slow_subscriber_is_closed_without_unbounded_growth(self) -> None:
        channel = HttpApiChannel()
        sub = _StreamSub("user_http_api:u1", maxsize=1)
        channel._stream_subs.append(sub)
        channel._push_frame({"type": "delta", "delta": "one"}, "user_http_api:u1")
        channel._push_frame({"type": "delta", "delta": "two"}, "user_http_api:u1")
        assert sub.closed and sub.queue.qsize() == 1
        assert sub.queue.get_nowait() == ""
        assert not channel._deliver_to_subscribers("u1", {"type": "reply"})

    def test_interrupted_turn_releases_only_its_own_waiter(self) -> None:
        channel = HttpApiChannel()

        async def scenario() -> None:
            current = channel._expect_reply("u1", "one")
            other = channel._expect_reply("u1", "two")
            await channel._on_after_reply({"scope": "user_http_api:u1#one", "reason": "interrupted", "turn_id": "run"})
            with pytest.raises(RuntimeError, match="中止"):
                await current
            assert not other.done()
            other.cancel()

        asyncio.run(scenario())

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

    def test_subsession_reply_frame_names_session(self) -> None:
        channel = HttpApiChannel()
        sub = _StreamSub("user_http_api:u1")
        channel._stream_subs.append(sub)
        result = json.loads(asyncio.run(channel.send_text("u1", "答复", session_id="topic-1")))
        assert result["success"] is True
        assert _frame_json(sub.queue.get_nowait()) == {
            "type": "reply", "content": "答复", "session_id": "topic-1",
        }

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

    def test_pending_replies_are_isolated_by_session(self) -> None:
        channel = HttpApiChannel()

        async def scenario() -> None:
            first = channel._expect_reply("u1", "topic-1")
            second = channel._expect_reply("u1", "topic-2")
            result = json.loads(await channel.send_text("u1", "second", session_id="topic-2"))
            assert result["success"] is True
            assert await asyncio.wait_for(second, timeout=1) == "second"
            assert not first.done()
            result = json.loads(await channel.send_text("u1", "first", session_id="topic-1"))
            assert result["success"] is True
            assert await asyncio.wait_for(first, timeout=1) == "first"

        asyncio.run(scenario())

    def test_new_request_replaces_only_same_session(self) -> None:
        channel = HttpApiChannel()

        async def scenario() -> None:
            old = channel._expect_reply("u1", "topic-1")
            other = channel._expect_reply("u1", "topic-2")
            new = channel._expect_reply("u1", "topic-1")
            with pytest.raises(RuntimeError, match="被取代"):
                await old
            assert not other.done()
            assert channel._pending_replies[channel._delivery_key("u1", "topic-1")] is new

        asyncio.run(scenario())

    def test_no_future_no_subscriber_errors(self) -> None:
        channel = HttpApiChannel()
        result = json.loads(asyncio.run(channel.send_text("nobody", "text")))
        assert result["success"] is False
