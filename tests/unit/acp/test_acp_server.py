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

    @property
    def config(self) -> Any:
        class _Cfg:
            base_url = "http://127.0.0.1:9999"
        return _Cfg()

    async def health(self) -> Dict[str, Any]:
        return {"url": self.config.base_url}

    async def send_message(self, user_id: str, text: str, images: Any = None) -> None:
        self.sent.append((user_id, text))

    async def stream_frames(self, user_id: str) -> AsyncIterator[Dict[str, Any]]:
        yield {"type": "open"}
        for frame in self.frames:
            yield frame


class _HangingDaemon(_FakeDaemon):
    """流挂起替身：open 后停在原地（驱动取消路径）。"""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def stream_frames(self, user_id: str) -> AsyncIterator[Dict[str, Any]]:
        yield {"type": "open"}
        await self.release.wait()
        yield {"type": "turn_end"}


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

        asyncio.run(server._dispatch({
            "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "处理这个任务"}]},
        }))

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
        asyncio.run(server._dispatch({
            "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "任务"}]},
        }))
        updates = [m for m in _messages(out) if m.get("method") == "session/update"]
        chunks = [u for u in updates if u["params"]["update"]["sessionUpdate"] == "agent_message_chunk"]
        assert len(chunks) == 1
        assert chunks[0]["params"]["update"]["content"]["text"] == "最终答复"

    def test_cancel_returns_cancelled_stop_reason(self) -> None:
        daemon = _HangingDaemon()
        server, out = _make_server(daemon)
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}))
        session_id = _messages(out)[0]["result"]["sessionId"]

        async def scenario() -> None:
            prompt_task = asyncio.create_task(server._dispatch({
                "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
                "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "长任务"}]},
            }))
            await asyncio.sleep(0.05)
            server._session_cancel({"sessionId": session_id})
            await prompt_task

        asyncio.run(scenario())
        final = _messages(out)[-1]
        assert final["id"] == 2 and final["result"] == {"stopReason": "cancelled"}

    def test_daemon_failure_maps_to_jsonrpc_error(self) -> None:
        class _BrokenDaemon(_FakeDaemon):
            async def stream_frames(self, user_id: str) -> AsyncIterator[Dict[str, Any]]:
                raise RuntimeError("connection refused")
                yield {"type": "open"}  # pragma: no cover - 生成器签名需要

        server, out = _make_server(_BrokenDaemon())
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}))
        session_id = _messages(out)[0]["result"]["sessionId"]
        asyncio.run(server._dispatch({
            "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "任务"}]},
        }))
        error = _messages(out)[-1]["error"]
        assert error["code"] == -32603
        assert "connection refused" in error["message"]

    def test_unknown_session_rejected(self) -> None:
        server, out = _make_server(_FakeDaemon())
        asyncio.run(server._dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "session/prompt",
            "params": {"sessionId": "nope", "prompt": [{"type": "text", "text": "x"}]},
        }))
        assert _messages(out)[-1]["error"]["code"] == -32603

    def test_empty_prompt_rejected(self) -> None:
        server, out = _make_server(_FakeDaemon())
        asyncio.run(server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}))
        session_id = _messages(out)[0]["result"]["sessionId"]
        asyncio.run(server._dispatch({
            "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": []},
        }))
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
