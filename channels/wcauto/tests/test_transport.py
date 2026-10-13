"""wcauto 传输层测试：RPC 配对/超时/断线失败化、握手处理（全部走假 WS，不触网）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from channels.wcauto.bridge.wcauto_bridge import protocol
from channels.wcauto.transport import WcautoRpcError, WcautoTransport


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list = []
        self.closed = False

    async def send_str(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self) -> None:
        self.closed = True


class _FakeStateStore:
    def __init__(self) -> None:
        self.seq = 0
        self.saved: list = []

    def load_seq(self) -> int:
        return self.seq

    def save_seq(self, seq: int) -> None:
        self.seq = seq
        self.saved.append(seq)

    def flush(self) -> None:
        pass


class _FakeChannel:
    def __init__(self, rpc_timeout: float = 5.0) -> None:
        self._rpc_timeout = rpc_timeout
        self._state_store = _FakeStateStore()
        self._status = None
        self.events: list = []

    def _cfg(self, key: str, default=None):
        return {"rpc_timeout": self._rpc_timeout}.get(key, default)

    async def _on_bridge_event(self, data) -> None:
        self.events.append(data)


def _transport(rpc_timeout: float = 5.0) -> tuple:
    ch = _FakeChannel(rpc_timeout)
    return WcautoTransport(ch), ch


class TestRpcPairing:
    async def test_rpc_success(self) -> None:
        transport, _ = _transport()
        transport._ws = _FakeWS()
        task = asyncio.create_task(transport.call_rpc("ping", {}))
        await asyncio.sleep(0)
        req = transport._ws.sent[0]
        assert req["type"] == protocol.TYPE_RPC and req["method"] == "ping"
        transport._resolve_rpc(
            protocol.make_rpc_result(req["id"], True, data={"pong": True}))
        assert await task == {"pong": True}
        assert not transport._pending  # 配对完成后清理

    async def test_rpc_business_error(self) -> None:
        transport, _ = _transport()
        transport._ws = _FakeWS()
        task = asyncio.create_task(transport.call_rpc("send_msg", {}))
        await asyncio.sleep(0)
        req = transport._ws.sent[0]
        transport._resolve_rpc(
            protocol.make_rpc_result(req["id"], False, error="微信窗口不可见"))
        with pytest.raises(WcautoRpcError, match="窗口不可见"):
            await task

    async def test_rpc_timeout(self) -> None:
        transport, _ = _transport(rpc_timeout=0.05)
        transport._ws = _FakeWS()
        with pytest.raises(WcautoRpcError, match="超时"):
            await transport.call_rpc("send_msg", {})
        assert not transport._pending

    async def test_rpc_offline_rejected(self) -> None:
        transport, _ = _transport()
        with pytest.raises(WcautoRpcError, match="桥未连接"):
            await transport.call_rpc("ping", {})

    async def test_disconnect_fails_pending(self) -> None:
        transport, _ = _transport()
        transport._ws = _FakeWS()
        task = asyncio.create_task(transport.call_rpc("send_msg", {}))
        await asyncio.sleep(0)
        transport._fail_pending("ws disconnected")
        with pytest.raises(WcautoRpcError, match="disconnected"):
            await task


class TestHandshake:
    async def test_hello_ok(self) -> None:
        transport, ch = _transport()
        ch._state_store.seq = 41

        class _HandshakeWS(_FakeWS):
            async def receive_str(self) -> str:
                return json.dumps(protocol.make_hello_ok(
                    50, {"username": "wxid_me", "nick_name": "梦璃"}, replayed=9))

        ws = _HandshakeWS()
        ok = await transport._handshake(ws)
        assert ok is True
        hello = ws.sent[0]
        assert hello["type"] == protocol.TYPE_HELLO
        assert hello["last_seq"] == 41  # 续传水位带上
        assert transport.self_info["nick_name"] == "梦璃"

    async def test_hello_auth_failed(self) -> None:
        transport, _ = _transport()

        class _RejectWS(_FakeWS):
            async def receive_str(self) -> str:
                return json.dumps(protocol.make_hello_err("auth_failed"))

        assert await transport._handshake(_RejectWS()) is False


class TestEventDispatch:
    async def test_event_saves_seq_and_dispatches(self) -> None:
        transport, ch = _transport()
        event = protocol.make_event(77, protocol.EVENT_MESSAGE, {"content": "hi"})
        # 复刻 _recv_loop 的事件分支：存水位 + 分发
        seq = int(event.get("seq") or 0)
        ch._state_store.save_seq(seq)
        await transport._dispatch_event(event)
        assert ch._state_store.seq == 77
        assert ch.events[0]["event"] == protocol.EVENT_MESSAGE

    async def test_dispatch_exception_swallowed(self) -> None:
        transport, ch = _transport()

        async def _boom(_data) -> None:
            raise RuntimeError("boom")

        ch._on_bridge_event = _boom
        # 分发异常不应传播（只记日志）
        await transport._dispatch_event(protocol.make_event(1, "message", {}))
