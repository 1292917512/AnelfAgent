"""worker 桥接客户端 — 回连主进程的桥接 WS 服务。

职责：
- 维护到主进程的 WebSocket 连接（断线指数退避重连，token 鉴权走查询参数）；
- 上行：事件 / 状态 / 日志 / 请求结果（seq 关联）；
- 下行：处理主进程的发送请求与控制命令（回调注入），回传结果；
- 经 loguru handler 将 worker 日志转发到主进程日志环。

仅在 worker venv 中以脚本方式运行（裸导入同目录模块）。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets
from protocol import (
    CMD_GET_PLUGINS,
    CMD_GET_STATUS,
    CMD_RUN_COMMAND,
    MSG_CMD,
    MSG_CMD_RESULT,
    MSG_EVENT,
    MSG_HELLO,
    MSG_LOG,
    MSG_PING,
    MSG_PONG,
    MSG_SEND,
    MSG_SEND_RESULT,
    MSG_STATUS,
    WIRE_VERSION,
    decode,
    encode,
)

# 处理器签名：接收 payload dict，返回结果 dict（异常会被捕获并回传错误）
CommandHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]

_RECONNECT_BASE = 1.0
_RECONNECT_MAX = 30.0


class BridgeClient:
    """桥接 WS 客户端。"""

    def __init__(
        self,
        ws_url: str,
        token: str,
        *,
        on_send: CommandHandler,
        on_command: CommandHandler,
        status_provider: Callable[[], Dict[str, Any]],
    ) -> None:
        self._ws_url = ws_url
        self._token = token
        self._on_send = on_send
        self._on_command = on_command
        self._status_provider = status_provider

        self._ws: Optional[Any] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._log_task_installed = False
        self._dropped_events = 0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def start(self) -> None:
        """启动连接循环（由 driver.on_startup 触发）。"""
        self._install_log_forwarder()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="bridge_client")

    async def stop(self) -> None:
        """停止连接循环并关闭连接。"""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._close_ws()

    # ------------------------------------------------------------------
    # 上行接口
    # ------------------------------------------------------------------

    async def send_event(self, wire_event: Dict[str, Any]) -> None:
        """上报平台事件（未连接时丢弃并计数）。"""
        await self._send_json(wire_event)

    async def push_status(self) -> None:
        """上报当前状态快照。"""
        payload = self._status_provider()
        payload["type"] = MSG_STATUS
        await self._send_json(payload)

    async def send_log(self, line: str) -> None:
        """上报一行日志。"""
        await self._send_json({"type": MSG_LOG, "line": line})

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """连接循环：断线指数退避重连。"""
        backoff = _RECONNECT_BASE
        url = f"{self._ws_url}?token={self._token}"
        while True:
            try:
                async with websockets.connect(url, max_size=16 * 1024 * 1024) as ws:
                    self._ws = ws
                    backoff = _RECONNECT_BASE
                    if self._dropped_events:
                        await self.send_log(
                            f"bridge: 重连成功（此前丢弃 {self._dropped_events} 条事件）"
                        )
                        self._dropped_events = 0
                    hello = self._status_provider()
                    hello.update({"type": MSG_HELLO, "wire_version": WIRE_VERSION})
                    await ws.send(encode(hello))
                    await self._recv_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 连接层错误统一退避重连
                self._ws = None
                await self._log_local(f"bridge: 连接断开 ({exc})，{backoff:.0f}s 后重连")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX)

    async def _recv_loop(self, ws: Any) -> None:
        """接收循环：按消息类型分发。"""
        async for raw in ws:
            payload = decode(raw) if isinstance(raw, str) else None
            if payload is None:
                continue
            msg_type = payload.get("type", "")
            seq = payload.get("seq")
            try:
                if msg_type == MSG_PING:
                    await ws.send(encode({"type": MSG_PONG, "seq": seq}))
                elif msg_type == MSG_SEND:
                    result = await self._on_send(payload)
                    result.update({"type": MSG_SEND_RESULT, "seq": seq})
                    await ws.send(encode(result))
                elif msg_type == MSG_CMD:
                    await self._handle_cmd(ws, payload)
                elif msg_type == MSG_PONG:
                    continue
            except Exception as exc:  # noqa: BLE001 - 单条消息处理失败不断连
                await self._log_local(f"bridge: 消息处理异常 ({msg_type}): {exc}")

    async def _handle_cmd(self, ws: Any, payload: Dict[str, Any]) -> None:
        """处理控制命令并回传结果。"""
        action = payload.get("action", "")
        try:
            if action in (CMD_GET_STATUS, CMD_GET_PLUGINS, CMD_RUN_COMMAND):
                result = await self._on_command(payload)
            else:
                result = {"ok": False, "error": f"未知命令: {action}"}
        except Exception as exc:  # noqa: BLE001 - 命令异常回传给调用方
            result = {"ok": False, "error": str(exc)}
        result.update({"type": MSG_CMD_RESULT, "seq": payload.get("seq")})
        await ws.send(encode(result))

    async def _send_json(self, payload: Dict[str, Any]) -> None:
        """发送 JSON（未连接时事件类消息丢弃计数）。"""
        ws = self._ws
        if ws is None:
            if payload.get("type") == MSG_EVENT:
                self._dropped_events += 1
            return
        try:
            await ws.send(encode(payload))
        except Exception:  # noqa: BLE001 - 发送失败交给重连逻辑
            if payload.get("type") == MSG_EVENT:
                self._dropped_events += 1

    def _install_log_forwarder(self) -> None:
        """安装 loguru handler，把 worker 日志转发到主进程。"""
        if self._log_task_installed:
            return
        self._log_task_installed = True
        try:
            from loguru import logger
        except ImportError:
            return

        client = self

        def _sink(message: Any) -> None:
            record = message.record
            line = (
                f"{record['time'].strftime('%H:%M:%S')} | {record['level'].name} | "
                f"{record['name']} | {record['message']}"
            )
            # 复用现有事件循环异步发送（loguru sink 是同步调用）
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(client.send_log(line))
            except RuntimeError:
                pass

        logger.add(_sink, level="INFO")

    async def _log_local(self, line: str) -> None:
        """本地日志（同时尽力转发）。"""
        try:
            from loguru import logger

            logger.info(line)
        except ImportError:
            pass

    async def _close_ws(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 - 关闭异常忽略
                pass


def bridge_env() -> tuple[str, str]:
    """读取父进程注入的桥接环境变量。"""
    return (
        os.environ.get("ANELF_BRIDGE_WS_URL", "ws://127.0.0.1:8197/bridge"),
        os.environ.get("ANELF_BRIDGE_TOKEN", ""),
    )
