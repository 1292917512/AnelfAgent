"""ACP 服务端 — ndjson JSON-RPC over stdio，驱动守护进程完成会话与任务。

外部 ACP 客户端（acpx 等）以 ``anelf-acp acp serve`` 拉起本进程；
会话映射：ACP session ↔ 守护进程 http_api 会话（``acp_{sessionId}``），
对话历史持久化在守护进程侧，本进程重启后 ``session/load`` 无损续接。

命令面（兼容 feishu-task-agent 启动器的 traecli readiness 探测）：
- ``acp serve``          启动 ACP 服务
- ``acp serve --help``   帮助文本（含 readiness 探测正则所需的描述）
- ``doctor --json``      就绪诊断 JSON（checks 数组，无 error 即 ready）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, TextIO

from .daemon import DaemonClient, resolve_config

PROTOCOL_VERSION = 1
AGENT_NAME = "anelf-acp"
AGENT_TITLE = "AnelfAgent"
AGENT_VERSION = "1.0.0"

HELP_TEXT = """anelf-acp — AnelfAgent ACP adapter

Usage:
  anelf-acp acp serve            Start the ACP server (ndjson JSON-RPC over stdio)
  anelf-acp acp serve --help     Show this help
  anelf-acp doctor --json        Run readiness diagnostics and print JSON

The `acp serve` subcommand speaks the Agent Client Protocol; task prompts
are executed by the local AnelfAgent daemon via its http_api channel."""

# 方法未注册（JSON-RPC 2.0）
_ERR_METHOD_NOT_FOUND = -32601
# 内部错误（守护进程不可达 / 执行失败）
_ERR_INTERNAL = -32603


class _Session:
    """一个 ACP 会话的本地状态（守护进程会话键 + 取消旗标）。"""

    __slots__ = ("session_id", "user_id", "cancel_event")

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.user_id = f"acp_{session_id}"
        self.cancel_event = asyncio.Event()


class AcpServer:
    """ACP 服务端：stdio 上的 ndjson JSON-RPC 分发循环。"""

    def __init__(
        self,
        daemon: Optional[DaemonClient] = None,
        out: Optional[TextIO] = None,
        err: Optional[TextIO] = None,
    ) -> None:
        self._daemon = daemon or DaemonClient()
        self._sessions: Dict[str, _Session] = {}
        self._out: TextIO = out or sys.stdout
        self._err: TextIO = err or sys.stderr

    # ------------------------------------------------------------------
    # 传输
    # ------------------------------------------------------------------

    def _send(self, payload: Dict[str, Any]) -> None:
        self._out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._out.flush()

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _respond(self, request_id: Any, result: Dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _respond_error(self, request_id: Any, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    async def run(self) -> None:
        """主循环：后台线程读 stdin（跨平台，Windows 无 connect_read_pipe）。"""
        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue[str]" = asyncio.Queue()

        def _pipe_stdin() -> None:
            for raw in sys.stdin:
                loop.call_soon_threadsafe(queue.put_nowait, raw)
            loop.call_soon_threadsafe(queue.put_nowait, "")

        threading.Thread(target=_pipe_stdin, daemon=True).start()
        while True:
            line = await queue.get()
            if not line:
                break
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            try:
                await self._dispatch(message)
            except Exception as exc:  # noqa: BLE001 — 分发层兜底，单条消息异常不终止服务
                if "id" in message:
                    self._respond_error(message["id"], _ERR_INTERNAL, f"internal error: {exc}")

    # ------------------------------------------------------------------
    # 分发
    # ------------------------------------------------------------------

    async def _dispatch(self, message: Dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        is_request = "id" in message
        if method == "initialize":
            self._respond(request_id, self._initialize(message.get("params") or {}))
        elif method == "initialized":
            pass  # 客户端就绪通知，无需应答
        elif method == "session/new":
            self._respond(request_id, self._session_new(message.get("params") or {}))
        elif method == "session/load":
            self._respond(request_id, self._session_load(message.get("params") or {}))
        elif method == "session/prompt":
            if not is_request:
                return
            await self._session_prompt(request_id, message.get("params") or {})
        elif method == "session/cancel":
            self._session_cancel(message.get("params") or {})
            if is_request:
                self._respond(request_id, {})
        elif method == "session/set_mode":
            # 单模式智能体：确认即可（模式语义由守护进程自身配置决定）
            self._respond(request_id, {})
        else:
            if is_request:
                self._respond_error(request_id, _ERR_METHOD_NOT_FOUND, f"method not found: {method}")

    # ------------------------------------------------------------------
    # 协议方法
    # ------------------------------------------------------------------

    def _initialize(self, _params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "agentCapabilities": {"loadSession": True},
            "agentInfo": {
                "name": AGENT_NAME,
                "title": AGENT_TITLE,
                "version": AGENT_VERSION,
            },
        }

    def _session_new(self, _params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = uuid.uuid4().hex
        self._sessions[session_id] = _Session(session_id)
        return {"sessionId": session_id}

    def _session_load(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # 守护进程侧历史按 user_id 持久化，load 即按原 session 键续接
        session_id = str(params.get("sessionId") or "")
        if session_id and session_id not in self._sessions:
            self._sessions[session_id] = _Session(session_id)
        return {}

    async def _session_prompt(self, request_id: Any, params: Dict[str, Any]) -> None:
        session_id = str(params.get("sessionId") or "")
        session = self._sessions.get(session_id)
        if session is None:
            self._respond_error(request_id, _ERR_INTERNAL, f"unknown session: {session_id}")
            return
        prompt_text = self._join_prompt(params.get("prompt"))
        if not prompt_text:
            self._respond_error(request_id, _ERR_INTERNAL, "empty prompt")
            return

        stop_reason = "end_turn"
        try:
            await self._run_turn(session, prompt_text)
        except _CancelledPrompt:
            stop_reason = "cancelled"
        except Exception as exc:  # noqa: BLE001 — 守护进程链路失败归因为 JSON-RPC 错误
            self._respond_error(request_id, _ERR_INTERNAL, f"daemon execution failed: {exc}")
            return
        self._respond(request_id, {"stopReason": stop_reason})

    async def _next_frame(
        self, frames: AsyncIterator[Dict[str, Any]], session: _Session
    ) -> Optional[Dict[str, Any]]:
        """取下一帧；会话取消时立即返回 None（不等下一帧到达）。"""
        anext = asyncio.ensure_future(frames.__anext__())
        cancel_wait = asyncio.ensure_future(session.cancel_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {anext, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if anext not in done:
                # 取消先于下一帧：作废在途取帧任务（CancelledError 传入生成器）
                anext.cancel()
                try:
                    await anext
                except (asyncio.CancelledError, StopAsyncIteration, RuntimeError):
                    pass
                return None
            return anext.result()
        finally:
            cancel_wait.cancel()

    async def _run_turn(self, session: _Session, prompt_text: str) -> None:
        """驱动守护进程一轮执行：订阅流 → 投递消息 → 消费帧至 turn_end。

        delta 增量合帧流出；未流出过正文增量时由 reply 帧补发最终文本。
        """
        session.cancel_event.clear()
        emitted_any = False
        emitted_message_text = False
        reply_text: Optional[str] = None
        buffer_text: List[str] = []
        buffer_thought: List[str] = []
        last_flush = 0.0

        def _flush(force: bool) -> None:
            nonlocal last_flush, emitted_message_text
            now = time.monotonic()
            if not force and now - last_flush < 0.15:
                return
            if buffer_text:
                self._emit_chunk(session.session_id, "".join(buffer_text), thought=False)
                buffer_text.clear()
                emitted_message_text = True
            if buffer_thought:
                self._emit_chunk(session.session_id, "".join(buffer_thought), thought=True)
                buffer_thought.clear()
            last_flush = now

        frames = self._daemon.stream_frames(session.user_id)
        try:
            # 先等待订阅通道建立（open 哨兵帧）再投递消息，避免竞态丢帧
            open_frame = await frames.__anext__()
            if open_frame.get("type") != "open":
                raise RuntimeError("unexpected first stream frame")
            await self._daemon.send_message(session.user_id, prompt_text)
            turn_done = False
            while True:
                try:
                    frame = await self._next_frame(frames, session)
                except StopAsyncIteration:
                    break  # 流正常关闭（未见 turn_end，走下方中断容忍判定）
                if frame is None:
                    raise _CancelledPrompt()
                ftype = str(frame.get("type") or "")
                if ftype == "delta":
                    target = buffer_thought if frame.get("reasoning") else buffer_text
                    target.append(str(frame.get("delta") or ""))
                    emitted_any = True
                    _flush(force=False)
                elif ftype == "tool_call":
                    _flush(force=True)
                    self._emit_tool_call(session.session_id, frame)
                elif ftype == "reply":
                    reply_text = str(frame.get("content") or "")
                elif ftype == "turn_end":
                    turn_done = True
                    break
            _flush(force=True)
            if not turn_done and not session.cancel_event.is_set():
                # 流中断且未见终帧：若有最终文本或已流出增量则视为完成，否则上抛
                if reply_text is None and not emitted_any:
                    raise RuntimeError("stream closed before turn_end")
            # 静默轮次（无正文增量流出）由 reply 帧补发最终文本
            if reply_text is not None and not emitted_message_text:
                self._emit_chunk(session.session_id, reply_text, thought=False)
        finally:
            try:
                await frames.aclose()
            except (RuntimeError, StopAsyncIteration):
                pass

    def _session_cancel(self, params: Dict[str, Any]) -> None:
        session = self._sessions.get(str(params.get("sessionId") or ""))
        if session is not None:
            session.cancel_event.set()

    # ------------------------------------------------------------------
    # session/update 发射
    # ------------------------------------------------------------------

    def _emit_chunk(self, session_id: str, text: str, thought: bool) -> None:
        update: Dict[str, Any] = {
            "sessionUpdate": "agent_thought_chunk" if thought else "agent_message_chunk",
            "content": {"type": "text", "text": text},
        }
        self._notify("session/update", {"sessionId": session_id, "update": update})

    def _emit_tool_call(self, session_id: str, frame: Dict[str, Any]) -> None:
        status = str(frame.get("status") or "")
        call_id = str(frame.get("call_id") or uuid.uuid4().hex[:8])
        name = str(frame.get("name") or "tool")
        if status == "running":
            update: Dict[str, Any] = {
                "sessionUpdate": "tool_call",
                "toolCallId": call_id,
                "title": name,
                "kind": "other",
                "status": "in_progress",
            }
        else:
            update = {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call_id,
                "status": "completed" if status == "done" else "failed",
                "rawOutput": str(frame.get("result_preview") or ""),
            }
        self._notify("session/update", {"sessionId": session_id, "update": update})

    @staticmethod
    def _join_prompt(prompt: Any) -> str:
        """拼接 prompt 内容块的文本部分（其余块类型忽略）。"""
        if not isinstance(prompt, list):
            return str(prompt) if isinstance(prompt, str) else ""
        parts: List[str] = []
        for block in prompt:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "\n".join(p for p in parts if p).strip()


class _CancelledPrompt(Exception):
    """客户端取消当前 prompt。"""


# ------------------------------------------------------------------
# 就绪诊断（feishu-task-agent 启动器 traecli readiness 契约）
# ------------------------------------------------------------------

def run_doctor(daemon: Optional[DaemonClient] = None) -> int:
    """doctor --json：探测守护进程可达性，输出 checks JSON。

    退出码：0=ready（仅 info/warning），11=blocked（存在 error 项）。
    """
    client = daemon or DaemonClient(resolve_config())
    checks: List[Dict[str, str]] = [
        {
            "name": "config",
            "severity": "info",
            "message": f"daemon url: {client.config.base_url}",
        }
    ]
    try:
        asyncio.run(client.health())
        checks.append({
            "name": "daemon",
            "severity": "info",
            "message": "守护进程可达（http_api 频道健康）",
        })
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 — 诊断面如实归因任意连接错误
        checks.append({
            "name": "daemon",
            "severity": "error",
            "message": f"守护进程不可达: {exc}",
            "fix": "启动 AnelfAgent 守护进程并确认 http_api 频道已启用",
        })
        exit_code = 11
    print(json.dumps({"checks": checks}, ensure_ascii=False))
    return exit_code


# ------------------------------------------------------------------
# CLI 入口
# ------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="anelf-acp",
        description=HELP_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    acp_parser = sub.add_parser("acp", help="ACP adapter subcommands")
    serve_sub = acp_parser.add_subparsers(dest="acp_command")
    # help/description 文本须保留 "acp serve" 与 "Start the ACP server" 字样
    # —— feishu-task-agent 启动器的 probe-acp 就绪探测按这两个正则判定
    serve_sub.add_parser(
        "serve",
        help="Start the ACP server",
        description="Start the ACP server (ndjson JSON-RPC over stdio)",
    )
    doctor_parser = sub.add_parser("doctor", help="Readiness diagnostics")
    doctor_parser.add_argument("--json", action="store_true", help="Output JSON checks")
    args = parser.parse_args(argv)

    if args.command == "acp" and getattr(args, "acp_command", None) == "serve":
        asyncio.run(AcpServer().run())
        return 0
    if args.command == "doctor":
        return run_doctor()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
