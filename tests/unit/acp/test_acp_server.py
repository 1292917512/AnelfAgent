"""ACP 服务端协议层测试（不触网，守护进程以帧序列替身驱动）。

契约来源：feishu-task-agent 官方桥 → acpx CLI → 标准 ACP v1
（ndjson JSON-RPC over stdio）；readiness 探测要求见
docs/feishu-task-binding.md。
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import pytest

from acp.server import AcpServer, main


class _FakeDaemon:
    """帧序列替身：stream_frames 依次吐出预设帧。"""

    def __init__(self, frames: Optional[List[Dict[str, Any]]] = None) -> None:
        self.frames = frames or []
        self.sent: List[Tuple[str, str]] = []
        self.subscriptions: list[str] = []

    @property
    def config(self) -> Any:
        class _Cfg:
            base_url = "http://127.0.0.1:9999"
        return _Cfg()

    async def health(self) -> Dict[str, Any]:
        return {"url": self.config.base_url}

    async def send_message(self, user_id: str, text: str, images: Any = None, *, message_id: str) -> None:
        assert message_id == self.subscriptions[-1]
        self.sent.append((user_id, text))

    async def stream_frames(self, user_id: str, *, message_id: str) -> AsyncIterator[Dict[str, Any]]:
        self.subscriptions.append(message_id)
        yield {"type": "open"}
        for frame in self.frames:
            yield frame


class _HangingDaemon(_FakeDaemon):
    """流挂起替身：open 后停在原地（驱动取消路径）。"""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.stops: list[tuple[str, str]] = []

    async def stop_message(self, user_id: str, message_id: str) -> None:
        self.stops.append((user_id, message_id))
        self.release.set()

    async def stream_frames(self, user_id: str, *, message_id: str) -> AsyncIterator[Dict[str, Any]]:
        self.subscriptions.append(message_id)
        yield {"type": "open"}
        self.started.set()
        yield {"type": "turn_started", "turn_id": "active-run"}
        await self.release.wait()
        yield {"type": "turn_end", "reason": "interrupted"}


class _Out(io.StringIO):
    """行缓冲输出替身（每行一条 ndjson 消息）。"""

    def flush(self) -> None:  # noqa: D102 — 接口占位
        pass


def _make_server(daemon: _FakeDaemon) -> tuple[AcpServer, _Out]:
    out = _Out()
    return AcpServer(daemon=daemon, out=out, err=io.StringIO()), out


def _messages(out: _Out) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


class TestInitialize:
    def test_initialize_shape(self) -> None:
        server, out = _make_server(_FakeDaemon())
        asyncio.run(server._dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": 1},
        }))
        msgs = _messages(out)
        assert msgs[0]["id"] == 1
        result = msgs[0]["result"]
        assert result["protocolVersion"] == 1
        assert result["agentCapabilities"]["loadSession"] is True
        assert result["agentInfo"]["name"] == "anelf-acp"

    def test_initialized_notification_no_response(self) -> None:
        server, out = _make_server(_FakeDaemon())
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "method": "initialized"}))
        assert out.getvalue() == ""


class TestSessionLifecycle:
    def test_session_new_registers_session(self) -> None:
        server, out = _make_server(_FakeDaemon())
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}))
        session_id = _messages(out)[0]["result"]["sessionId"]
        assert session_id in server._sessions
        assert server._sessions[session_id].user_id == f"acp_{session_id}"

    def test_session_load_adopts_known_session(self) -> None:
        server, _ = _make_server(_FakeDaemon())
        asyncio.run(server._dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "session/load",
            "params": {"sessionId": "abc", "cwd": "/tmp", "mcpServers": []},
        }))
        assert "abc" in server._sessions

    def test_unknown_method_returns_error(self) -> None:
        server, out = _make_server(_FakeDaemon())
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "id": 7, "method": "foo/bar"}))
        assert _messages(out)[0]["error"]["code"] == -32601


def _dispatch_and_drain(server: AcpServer, message: Dict[str, Any]) -> None:
    """单事件循环内分发并等待 prompt 轮次结束。

    session/prompt 已并发化（dispatch 只 spawn 任务），测试须在同一事件
    循环内 drain——跨 asyncio.run 的 future 绑定已关闭的 loop。
    """
    async def _go() -> None:
        await server._dispatch(message)
        pending = list(server._prompt_tasks.values())
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(_go())


class TestPromptFlow:
    def test_full_turn_streams_updates_and_end_turn(self) -> None:
        daemon = _FakeDaemon(frames=[
            {"type": "delta", "delta": "正在"},
            {"type": "delta", "delta": "执行"},
            {"type": "tool_call", "call_id": "t1", "name": "read_file", "status": "running"},
            {"type": "tool_call", "call_id": "t1", "name": "read_file", "status": "done", "result_preview": "ok"},
            {"type": "reply", "content": "任务完成"},
            {"type": "turn_end"},
        ])
        server, out = _make_server(daemon)
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}))
        session_id = _messages(out)[0]["result"]["sessionId"]

        _dispatch_and_drain(server, {
            "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "处理这个任务"}]},
        })

        msgs = _messages(out)
        # 消息投递到达守护进程，user 键按会话派生
        assert daemon.sent == [(f"acp_{session_id}", "处理这个任务")]
        updates = [m for m in msgs if m.get("method") == "session/update"]
        kinds = [u["params"]["update"]["sessionUpdate"] for u in updates]
        assert "agent_message_chunk" in kinds
        assert "tool_call" in kinds and "tool_call_update" in kinds
        # 正文增量已流出 → reply 文本不重复补发
        chunk_texts = [
            u["params"]["update"]["content"]["text"]
            for u in updates if u["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
        ]
        assert "".join(chunk_texts) == "正在执行"
        final = msgs[-1]
        assert final["id"] == 2 and final["result"] == {"stopReason": "end_turn"}

    def test_silent_turn_emits_reply_text_once(self) -> None:
        """无 delta 的静默轮次由 reply 帧补发最终文本。"""
        daemon = _FakeDaemon(frames=[
            {"type": "reply", "content": "最终答复"},
            {"type": "turn_end"},
        ])
        server, out = _make_server(daemon)
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}))
        session_id = _messages(out)[0]["result"]["sessionId"]
        _dispatch_and_drain(server, {
            "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "任务"}]},
        })
        updates = [m for m in _messages(out) if m.get("method") == "session/update"]
        chunks = [u for u in updates if u["params"]["update"]["sessionUpdate"] == "agent_message_chunk"]
        assert len(chunks) == 1
        assert chunks[0]["params"]["update"]["content"]["text"] == "最终答复"

    def test_concurrent_prompt_same_session_rejected(self) -> None:
        """同会话已有进行中 prompt 时，新 prompt 立即报错（turn 边界唯一）。"""
        daemon = _HangingDaemon()
        server, out = _make_server(daemon)
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}))
        session_id = _messages(out)[0]["result"]["sessionId"]

        async def scenario() -> None:
            await server._dispatch({
                "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
                "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "长任务"}]},
            })
            await server._dispatch({
                "jsonrpc": "2.0", "id": 3, "method": "session/prompt",
                "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "插队"}]},
            })
            server._session_cancel({"sessionId": session_id})
            await asyncio.gather(*list(server._prompt_tasks.values()), return_exceptions=True)

        asyncio.run(scenario())
        assert daemon.sent == [] and daemon.stops == []
        rejected = [m for m in _messages(out) if m.get("id") == 3][0]
        assert "already in progress" in rejected["error"]["message"]

    def test_cancel_returns_cancelled_stop_reason(self) -> None:
        daemon = _HangingDaemon()
        server, out = _make_server(daemon)
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}))
        session_id = _messages(out)[0]["result"]["sessionId"]

        async def scenario() -> None:
            await server._dispatch({
                "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
                "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "长任务"}]},
            })
            await asyncio.wait_for(daemon.started.wait(), 1)
            server._session_cancel({"sessionId": session_id})
            await asyncio.gather(*list(server._prompt_tasks.values()), return_exceptions=True)

        asyncio.run(scenario())
        final = _messages(out)[-1]
        assert daemon.stops == [(f"acp_{session_id}", daemon.subscriptions[0])]
        assert final["id"] == 2 and final["result"] == {"stopReason": "cancelled"}

    def test_daemon_failure_maps_to_jsonrpc_error(self) -> None:
        class _BrokenDaemon(_FakeDaemon):
            async def stream_frames(self, user_id: str, *, message_id: str) -> AsyncIterator[Dict[str, Any]]:
                raise RuntimeError("connection refused")
                yield {"type": "open"}  # pragma: no cover - 生成器签名需要

        server, out = _make_server(_BrokenDaemon())
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}))
        session_id = _messages(out)[0]["result"]["sessionId"]
        _dispatch_and_drain(server, {
            "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "任务"}]},
        })
        error = _messages(out)[-1]["error"]
        assert error["code"] == -32603
        assert "connection refused" in error["message"]

    @pytest.mark.parametrize(("terminal", "expected"), [
        ({"reason": "interrupted"}, "cancelled"), ({"reason": "budget_exhausted"}, "max_turn_requests"),
        ({"reason": "failed"}, None), ({"reason": "completed", "error": True}, None),
        ({"reason": "unexpected"}, None), (None, None),
    ])
    def test_only_terminal_frame_confirms_outcome(self, terminal, expected) -> None:
        frames = [{"type": "delta", "delta": chunk} for chunk in ["one", "two"]]
        if terminal is not None:
            frames.append({"type": "turn_end", **terminal})
        server, out = _make_server(_FakeDaemon(frames))
        session = server._session_new({})
        _dispatch_and_drain(server, {"id": 2, "method": "session/prompt", "params": {**session, "prompt": "go"}})
        *updates, response = _messages(out)
        assert "".join(m["params"]["update"]["content"]["text"] for m in updates) == "onetwo"
        if expected is None:
            assert response["error"]["code"] == -32603
        else:
            assert response["result"]["stopReason"] == expected

    async def test_cancel_while_queued_requires_terminal_confirmation(self, monkeypatch) -> None:
        class Queued(_HangingDaemon):
            async def stream_frames(self, user_id, *, message_id):
                self.subscriptions.append(message_id)
                yield {"type": "open"}
                self.started.set()
                await asyncio.Event().wait()

        daemon = Queued()
        server, out = _make_server(daemon)
        session = server._session_new({})
        monkeypatch.setattr("acp.server._CANCEL_CONFIRM_TIMEOUT", .1)
        server._spawn_prompt(2, {**session, "prompt": "first"})
        await asyncio.wait_for(daemon.started.wait(), 1)
        server._session_cancel(session)
        await asyncio.gather(*list(server._prompt_tasks.values()))
        assert daemon.stops == [(f"acp_{session['sessionId']}", daemon.subscriptions[0])]
        assert "not confirmed" in _messages(out)[-1]["error"]["message"]

    async def test_matching_terminal_finishes_while_stop_ack_is_delayed(self, monkeypatch) -> None:
        class SlowAck(_HangingDaemon):
            async def stop_message(self, user_id: str, message_id: str) -> None:
                await super().stop_message(user_id, message_id)
                await asyncio.Event().wait()

        daemon = SlowAck()
        server, out = _make_server(daemon)
        session = server._session_new({})
        monkeypatch.setattr("acp.server._CANCEL_CONFIRM_TIMEOUT", .2)
        server._spawn_prompt(2, {**session, "prompt": "first"})
        await asyncio.wait_for(daemon.started.wait(), 1)
        server._session_cancel(session)
        await asyncio.gather(*list(server._prompt_tasks.values()))
        assert _messages(out)[-1]["result"] == {"stopReason": "cancelled"}

    async def test_queued_stop_waits_for_its_terminal(self) -> None:
        class Queued(_HangingDaemon):
            async def stream_frames(self, user_id, *, message_id):
                self.subscriptions.append(message_id)
                yield {"type": "open"}
                self.started.set()
                await allow_start.wait()
                yield {"type": "turn_started", "turn_id": "late"}
                await self.release.wait()
                yield {"type": "turn_end", "reason": "interrupted"}

        allow_start = asyncio.Event()
        daemon = Queued()
        server, out = _make_server(daemon)
        session = server._session_new({})
        server._spawn_prompt(1, {**session, "prompt": "queued"})
        task = server._prompt_tasks[session["sessionId"]]
        await asyncio.wait_for(daemon.started.wait(), 1)
        server._session_cancel(session)
        await asyncio.wait_for(daemon.release.wait(), 1)
        assert not task.done()
        allow_start.set()
        await asyncio.wait_for(task, 2)
        assert daemon.stops == [(f"acp_{session['sessionId']}", daemon.subscriptions[0])]
        assert _messages(out)[-1]["result"] == {"stopReason": "cancelled"}

    def test_unknown_session_rejected(self) -> None:
        server, out = _make_server(_FakeDaemon())
        _dispatch_and_drain(server, {
            "jsonrpc": "2.0", "id": 1, "method": "session/prompt",
            "params": {"sessionId": "nope", "prompt": [{"type": "text", "text": "x"}]},
        })
        assert _messages(out)[-1]["error"]["code"] == -32603

    def test_empty_prompt_rejected(self) -> None:
        server, out = _make_server(_FakeDaemon())
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}))
        session_id = _messages(out)[0]["result"]["sessionId"]
        _dispatch_and_drain(server, {
            "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": []},
        })
        assert _messages(out)[-1]["error"]["code"] == -32603


class TestPromptJoining:
    def test_text_blocks_joined(self) -> None:
        assert AcpServer._join_prompt([
            {"type": "text", "text": "part1"},
            {"type": "resource_link", "uri": "file:///x"},
            {"type": "text", "text": "part2"},
        ]) == "part1\npart2"

    def test_non_list_prompt(self) -> None:
        assert AcpServer._join_prompt("plain") == "plain"
        assert AcpServer._join_prompt(None) == ""
        assert AcpServer._join_prompt(42) == ""


class TestReadinessContract:
    """启动器 probe-acp / doctor 的两个文本契约。"""

    def test_serve_help_matches_probe_regexes(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["acp", "serve", "--help"])
        assert exc_info.value.code == 0
        text = capsys.readouterr().out
        # traecode-readiness.mjs probe-acp 的两个判定正则
        assert re.search(r"\bacp\s+serve\b", text, re.I)
        assert re.search(r"Start the ACP server", text, re.I)

    def test_doctor_ready_and_blocked(self, capsys: pytest.CaptureFixture[str]) -> None:
        from acp.server import run_doctor

        assert run_doctor(daemon=_FakeDaemon()) == 0
        payload = json.loads(capsys.readouterr().out)
        assert all(c["severity"] == "info" for c in payload["checks"])
        assert {c["name"] for c in payload["checks"]} == {"config", "daemon"}

        class _DeadDaemon(_FakeDaemon):
            async def health(self) -> Dict[str, Any]:
                raise RuntimeError("refused")

        assert run_doctor(daemon=_DeadDaemon()) == 11
        payload = json.loads(capsys.readouterr().out)
        assert any(c["severity"] == "error" for c in payload["checks"])
