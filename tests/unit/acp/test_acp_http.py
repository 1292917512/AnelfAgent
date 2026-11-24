"""ACP 与 HTTP 频道的回环协议回归，执行方用可协作取消的轮次替身。"""

from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import httpx
import pytest

from acp.daemon import DaemonClient, DaemonConfig
from acp.server import AcpServer
from agent.mind.interrupt import InterruptRegistry
from channels.http_api.adapter import HttpApiChannel
from channels.http_api.config import HttpApiConfig
from core.activity import activity_owner, activity_scope
from core.event_bus import EVENT_AFTER_REPLY, event_bus
from core.tool_context import tool_request


@pytest.mark.parametrize("queued", [False, True], ids=["running", "queued-past-client-timeout"])
async def test_cancel_then_immediate_prompt_over_real_sse(monkeypatch, unused_tcp_port, queued):
    channel = HttpApiChannel()
    channel._config = HttpApiConfig(port=unused_tcp_port, api_token="loopback-test")
    daemon = DaemonClient(DaemonConfig(channel.config.base_url, channel.config.api_token))
    output = io.StringIO()
    acp = AcpServer(daemon=daemon, out=output)
    session = acp._session_new({})
    sid = session["sessionId"]
    user = acp._sessions[sid].user_id
    scope = channel._delivery_key(user)
    interrupts = InterruptRegistry()
    started = asyncio.Event()
    dispatched = asyncio.Event()
    allow_start = asyncio.Event()
    if queued:
        monkeypatch.setattr("acp.server._CANCEL_CONFIRM_TIMEOUT", 1)
    else:
        allow_start.set()
    messages: list[str] = []
    stopped: list[str] = []
    responders: list[asyncio.Task[None]] = []

    async def respond(message_id, run_id):
        if run_id == "first":
            await allow_start.wait()
        with tool_request(scope, user, message_id=message_id), interrupts.execution(run_id, scope):
            with activity_owner(kind="task", label="task owner"):
                async with activity_scope(run_id, scope, kind="conversation", message_id=message_id):
                    if run_id == "first":
                        started.set()
                        await interrupts.event(scope).wait()
                        interrupts.consume(scope)
                        reason = "interrupted"
                    else:
                        await event_bus.emit(EVENT_AFTER_REPLY, {"scope": scope, "turn_id": "first", "reason": "failed"})
                        await channel.send_text(user, "second result")
                        reason = "completed"
                    await event_bus.emit(EVENT_AFTER_REPLY, {"scope": scope, "turn_id": run_id, "reason": reason})

    async def dispatch(adapter, message):
        messages.append(message.message_id)
        responders.append(asyncio.create_task(respond(message.message_id, message.content)))
        dispatched.set()

    async def stop(*, scope, run_id, reason):
        assert interrupts.scope_for_run(run_id) == scope
        stopped.append(run_id)
        interrupts.request(scope, reason)
        return {"status": "ok"}

    monkeypatch.setattr("agent.channel.manager.get_channel_manager", lambda: SimpleNamespace(dispatch_inbound=dispatch))
    monkeypatch.setattr("agent.execution_control.request_execution_stop", stop)
    await channel.start()
    try:
        async with asyncio.timeout(10):
            acp._spawn_prompt(1, {**session, "prompt": "first"})
            first = acp._prompt_tasks[sid]
            await (dispatched if queued else started).wait()
            acp._session_cancel(session)
            await first
            if queued:
                while channel._stream_subs:
                    await asyncio.sleep(.01)
                assert (scope, messages[0]) in channel._queued_stops and not stopped
            acp._spawn_prompt(2, {**session, "prompt": "second"})
            await acp._prompt_tasks[sid]
            allow_start.set()
            await asyncio.gather(*responders)
            assert not channel._queued_stops
        replies = [json.loads(s) for s in output.getvalue().splitlines()]
        first_response, second_response = (r for r in replies if "id" in r)
        assert first_response["id"] == 1
        if queued:
            assert "not confirmed" in first_response["error"]["message"]
        else:
            assert first_response["result"]["stopReason"] == "cancelled"
        assert second_response == {"jsonrpc": "2.0", "id": 2, "result": {"stopReason": "end_turn"}}
        assert [r["params"]["update"]["content"]["text"] for r in replies if "method" in r] == ["second result"]
        assert stopped == ["first"] and len(set(messages)) == 2
    finally:
        for task in [*responders, *acp._prompt_tasks.values()]:
            if not task.done():
                task.cancel()
        await asyncio.gather(*responders, *list(acp._prompt_tasks.values()), return_exceptions=True)
        await channel.stop()


@pytest.mark.parametrize("old_daemon", [False, True])
async def test_transport_rejects_incompatible_stream_or_unaccepted_post(old_daemon, monkeypatch):
    daemon = DaemonClient(DaemonConfig("http://test", ""))
    requests = []

    def respond(req):
        requests.append(req)
        if req.method == "GET":
            return httpx.Response(200, headers={} if old_daemon else {"X-Anelf-Stream-Scope": "message"})
        return httpx.Response(200, json={"status": "error", "error": "rejected input"})

    monkeypatch.setattr(daemon, "_client", lambda timeout: httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(respond)))
    out = io.StringIO()
    server = AcpServer(daemon, out)
    session = server._session_new({})
    await server._session_prompt(1, {**session, "prompt": "test"})
    assert json.loads(out.getvalue())["error"]["code"] == -32603
    assert [r.method for r in requests] == (["GET"] if old_daemon else ["GET", "POST"])
    if not old_daemon:
        assert requests[0].url.params["message_id"] == json.loads(requests[1].content)["message_id"]
