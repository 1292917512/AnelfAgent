"""桥协议测试：报文构造/解析往返、事件缓冲 seq 与断线重放。"""

from __future__ import annotations

from channels.wcauto.bridge.wcauto_bridge import protocol


class TestEnvelope:
    def test_hello_roundtrip(self) -> None:
        hello = protocol.make_hello("tok", 42)
        assert hello["type"] == protocol.TYPE_HELLO
        assert hello["token"] == "tok"
        assert hello["last_seq"] == 42
        assert hello["v"] == protocol.PROTOCOL_VERSION

    def test_rpc_roundtrip(self) -> None:
        req = protocol.make_rpc("send_msg", {"chat": "c", "text": "t"}, rpc_id="abc")
        assert req["type"] == protocol.TYPE_RPC and req["id"] == "abc"
        ok = protocol.make_rpc_result(req["id"], True, data={"status": "ok"})
        assert ok["ok"] is True and ok["data"]["status"] == "ok"
        err = protocol.make_rpc_result(req["id"], False, error="窗口不可见")
        assert err["ok"] is False and err["error"] == "窗口不可见"

    def test_rpc_id_autogen_unique(self) -> None:
        ids = {protocol.make_rpc("ping")["id"] for _ in range(100)}
        assert len(ids) == 100


class TestEventBuffer:
    def test_seq_monotonic(self) -> None:
        buf = protocol.EventBuffer(capacity=500)
        a = buf.append(protocol.EVENT_MESSAGE, {"n": 1})
        b = buf.append(protocol.EVENT_MESSAGE, {"n": 2})
        assert a["seq"] == 1 and b["seq"] == 2
        assert buf.seq == 2

    def test_replay_all(self) -> None:
        buf = protocol.EventBuffer()
        for i in range(5):
            buf.append(protocol.EVENT_MESSAGE, {"n": i})
        replay = buf.replay_since(0)
        assert replay is not None and len(replay) == 5
        assert [x["seq"] for x in replay] == [1, 2, 3, 4, 5]

    def test_replay_partial(self) -> None:
        buf = protocol.EventBuffer()
        for i in range(5):
            buf.append(protocol.EVENT_MESSAGE, {"n": i})
        replay = buf.replay_since(3)
        assert replay is not None and [x["seq"] for x in replay] == [4, 5]

    def test_replay_gap_detected(self) -> None:
        buf = protocol.EventBuffer(capacity=100)
        for i in range(200):
            buf.append(protocol.EVENT_MESSAGE, {"n": i})
        # last_seq=1 已被挤出缓冲（最旧是 101），须报缺口
        assert buf.replay_since(1) is None
        # last_seq 在覆盖范围内则正常
        replay = buf.replay_since(150)
        assert replay is not None and replay[0]["seq"] == 151

    def test_capacity_eviction(self) -> None:
        buf = protocol.EventBuffer(capacity=100)
        for i in range(150):
            buf.append(protocol.EVENT_MESSAGE, {"n": i})
        assert len(buf._items) == 100
        assert buf._items[0]["seq"] == 51
