"""realtime_hub 订阅/背压/分级丢弃语义测试。"""

from __future__ import annotations

import asyncio

import pytest

from core import realtime_hub
from core.realtime_hub import TERMINAL_EVENTS


@pytest.fixture(autouse=True)
def clean_hub():
    realtime_hub.reset()
    yield
    realtime_hub.reset()


def _drain(sub) -> list:
    items = []
    while True:
        try:
            items.append(sub.queue.get_nowait())
        except asyncio.QueueEmpty:
            return items


class TestSubscribe:
    def test_default_subscriber_receives_all(self):
        sub = realtime_hub.subscribe()
        realtime_hub.publish({"event": "reply", "content": "hi"})
        assert _drain(sub) == [{"event": "reply", "content": "hi"}]

    def test_topic_filter(self):
        sub = realtime_hub.subscribe(topics={"reply"})
        realtime_hub.publish({"event": "delta", "delta": "x"})
        realtime_hub.publish({"event": "reply", "content": "y"})
        assert [e["event"] for e in _drain(sub)] == ["reply"]

    def test_count_by_client_kind(self):
        realtime_hub.subscribe(client_kind="web")
        realtime_hub.subscribe(client_kind="desktop")
        assert realtime_hub.subscriber_count() == 2
        assert realtime_hub.subscriber_count(client_kind="web") == 1
        assert realtime_hub.subscriber_count(client_kind="desktop") == 1

    def test_unsubscribe_idempotent(self):
        sub = realtime_hub.subscribe()
        realtime_hub.unsubscribe(sub)
        realtime_hub.unsubscribe(sub)
        assert realtime_hub.subscriber_count() == 0

    def test_connection_id_auto_assigned_unique(self):
        a, b = realtime_hub.subscribe(), realtime_hub.subscribe()
        assert a.connection_id and b.connection_id
        assert a.connection_id != b.connection_id


class TestBackpressure:
    def _fill(self, sub, event: str, n: int) -> None:
        for i in range(n):
            sub.queue.put_nowait({"event": event, "i": i})

    def test_full_queue_sheds_oldest_droppable_for_new_frame(self):
        """队列满时丢最旧的增量帧腾位（增量帧可丢，新帧入队）。"""
        sub = realtime_hub.subscribe()
        self._fill(sub, "delta", 256)
        realtime_hub.publish({"event": "reply", "content": "终态"})
        events = [e["event"] for e in _drain(sub)]
        assert "reply" in events
        assert len(events) == 256  # 丢了一帧旧 delta
        assert not sub.dead

    def test_terminal_frames_never_shed(self):
        """队列里混有终态帧时，腾位只丢增量，终态帧全部保留。"""
        sub = realtime_hub.subscribe()
        self._fill(sub, "delta", 200)
        for t in sorted(TERMINAL_EVENTS):
            sub.queue.put_nowait({"event": t})
        self._fill(sub, "delta", 256 - 200 - len(TERMINAL_EVENTS))
        realtime_hub.publish({"event": "media", "url": "u"})
        events = [e["event"] for e in _drain(sub)]
        for t in TERMINAL_EVENTS:
            assert t in events
        assert "media" in events
        assert not sub.dead

    def test_terminal_overflow_marks_subscriber_dead(self):
        """队列全是终态帧腾不出位时判死（由连接侧重连，好过静默丢回复）。"""
        sub = realtime_hub.subscribe()
        self._fill(sub, "reply", 256)
        realtime_hub.publish({"event": "reply", "content": "溢出"})
        assert sub.dead
        # 判死后不再接收任何帧
        realtime_hub.publish({"event": "delta", "delta": "x"})
        assert len(_drain(sub)) == 256

    def test_nonterminal_dropped_silently_when_no_room(self):
        """非终态帧在腾不出位时直接丢弃，不判死。"""
        sub = realtime_hub.subscribe()
        self._fill(sub, "reply", 256)
        realtime_hub.publish({"event": "delta", "delta": "x"})
        assert not sub.dead
        assert len(_drain(sub)) == 256

    def test_dead_subscriber_skipped_but_others_served(self):
        dead_sub = realtime_hub.subscribe()
        live_sub = realtime_hub.subscribe()
        self._fill(dead_sub, "reply", 256)
        realtime_hub.publish({"event": "reply", "content": "R"})
        assert dead_sub.dead
        assert [e["content"] for e in _drain(live_sub)] == ["R"]


class TestOfferLatest:
    """直挂队列通道（语音 sink）的保最新入队原语。"""

    @staticmethod
    def _droppable(item: dict) -> bool:
        return bool(item.get("__audio__")) or item.get("event") == "rt_partial"

    def test_sheds_oldest_droppable_to_make_room(self):
        """队列满时优先丢最旧可丢帧（partial/音频），关键帧全部保留。"""
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=4)
        q.put_nowait({"event": "rt_state", "state": "listening"})
        q.put_nowait({"event": "rt_partial", "text": "一"})
        q.put_nowait({"__audio__": True, "pcm": b"a"})
        q.put_nowait({"event": "rt_partial", "text": "二"})
        realtime_hub.offer_latest(
            q, {"event": "rt_final", "text": "定稿"}, self._droppable)
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        names = [e.get("event") or "audio" for e in events]
        # 最旧的一帧 partial 被丢，rt_state 与最新 rt_final 都在
        assert names == ["rt_state", "audio", "rt_partial", "rt_final"]

    def test_drops_oldest_when_all_critical(self):
        """极端堆积（无可丢帧）：丢最旧保最新，新帧绝不静默消失。"""
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=2)
        q.put_nowait({"event": "rt_state", "state": "listening"})
        q.put_nowait({"event": "rt_final", "text": "旧"})
        realtime_hub.offer_latest(
            q, {"event": "rt_final", "text": "新"}, self._droppable)
        assert q.get_nowait()["text"] == "旧"
        assert q.get_nowait()["text"] == "新"

    def test_audio_frame_sheds_itself_first(self):
        """音频帧满时优先丢最旧音频帧（实时流丢旧无感）。"""
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=2)
        q.put_nowait({"__audio__": True, "pcm": b"old"})
        q.put_nowait({"event": "rt_state", "state": "speaking"})
        realtime_hub.offer_latest(
            q, {"__audio__": True, "pcm": b"new"}, self._droppable)
        items = [q.get_nowait(), q.get_nowait()]
        assert items[0]["event"] == "rt_state"
        assert items[1]["pcm"] == b"new"
