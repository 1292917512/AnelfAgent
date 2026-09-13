"""Home Assistant 连接 — WebSocket API 实时状态同步与服务调用。

协议流程：ws_connect → auth_required → auth → auth_ok →
config/area_registry/list + config/device_registry/list +
config/entity_registry/list（解析房间归属）→ get_states（全量建缓存）→
subscribe_events(state_changed)（增量维护缓存并回调 manager）。
断线按指数退避自动重连（1/2/4/8/16s 上限），上次连接稳定运行超窗口后
重试计数复位（对齐 MCP 重连预算语义）；设备缓存跨重连保留末次快照。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import aiohttp

from core.config import expand_env_refs
from core.log import log

from ..models import DeviceState
from . import home_provider
from .base import SmartHomeProvider

_LOG_TAG = "智能家居"

_COMMAND_TIMEOUT = 15.0
_STABLE_WINDOW_SECONDS = 300.0
_BACKOFF_MAX_SECONDS = 16.0


@home_provider
class HomeAssistantProvider(SmartHomeProvider):
    """Home Assistant 平台接入（WebSocket API，实时状态推送）。"""

    key = "ha"
    display_name = "Home Assistant"
    config_schema = {
        "url": {
            "description": "Home Assistant 地址（如 http://homeassistant.local:8123）",
            "default": "",
        },
        "token": {
            "description": "长期访问令牌（HA 用户资料页生成，支持 ${ENV_VAR} 引用）",
            "default": "",
            "value_type": "password",
        },
    }

    def __init__(self) -> None:
        super().__init__()
        self._devices: Dict[str, DeviceState] = {}
        self._areas: Dict[str, str] = {}
        self._device_area: Dict[str, str] = {}
        self._entity_area: Dict[str, str] = {}
        self._task: Optional[asyncio.Task[None]] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._msg_id = 0
        self._pending: Dict[int, "asyncio.Future[Any]"] = {}
        self._connected = False
        self._last_error = ""
        self._connected_at = 0.0
        self._retry_count = 0
        self._closed = True

    # ---- 状态 ----

    def _token(self) -> str:
        """访问令牌（建连时展开 ${ENV_VAR} 引用）。"""
        return str(expand_env_refs(str(self.get_config("token", "")))).strip()

    def is_configured(self) -> bool:
        return bool(str(self.get_config("url", "")).strip() and self._token())

    def is_connected(self) -> bool:
        return self._connected

    def snapshot(self) -> List[DeviceState]:
        return list(self._devices.values())

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.key,
            "provider_name": self.display_name,
            "configured": self.is_configured(),
            "connected": self._connected,
            "device_count": len(self._devices),
            "last_error": self._last_error or None,
            "connected_at": self._connected_at or None,
        }

    # ---- 生命周期 ----

    async def connect(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._closed = False
        self._task = asyncio.create_task(self._supervise())

    async def close(self) -> None:
        self._closed = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._teardown()

    async def _supervise(self) -> None:
        """托管循环：连接 → 运行 → 断线退避重连（稳定窗口复位重试计数）。"""
        while not self._closed:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                log(f"HA 连接异常: {exc}", "WARNING", tag=_LOG_TAG)
            await self._teardown()
            self._emit_connection()
            if self._closed:
                return
            uptime = time.time() - self._connected_at if self._connected_at else 0.0
            self._connected_at = 0.0
            self._retry_count = (
                1 if uptime > _STABLE_WINDOW_SECONDS else self._retry_count + 1
            )
            delay = min(2.0 ** (self._retry_count - 1), _BACKOFF_MAX_SECONDS)
            await asyncio.sleep(delay)

    async def _teardown(self) -> None:
        """回收连接资源并办结在途命令（设备缓存保留末次快照）。"""
        self._connected = False
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("HA 连接已断开"))
        self._pending.clear()
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        session, self._session = self._session, None
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass

    # ---- 协议 ----

    def _ws_url(self) -> str:
        """把用户配置的地址规整为 WebSocket API URL。"""
        raw = str(self.get_config("url", "")).strip().rstrip("/")
        if raw.startswith(("ws://", "wss://")):
            base = raw
        elif raw.startswith("https://"):
            base = "wss://" + raw[len("https://"):]
        elif raw.startswith("http://"):
            base = "ws://" + raw[len("http://"):]
        else:
            base = "ws://" + raw
        if base.endswith("/api/websocket"):
            return base
        return base + "/api/websocket"

    async def _run_once(self) -> None:
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._ws_url(), heartbeat=30.0)
        await self._handshake()
        await self._full_sync()
        self._connected = True
        self._connected_at = time.time()
        self._last_error = ""
        self._emit_connection()
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                self._dispatch(json.loads(msg.data))
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        raise ConnectionError("HA WebSocket 连接中断")

    async def _handshake(self) -> None:
        """鉴权握手（auth_required → auth → auth_ok）。"""
        assert self._ws is not None
        first = await self._ws.receive(timeout=_COMMAND_TIMEOUT)
        if first.type != aiohttp.WSMsgType.TEXT:
            raise ConnectionError(f"HA 握手异常: {first.type}")
        if json.loads(first.data).get("type") != "auth_required":
            raise ConnectionError("HA 握手异常：未收到 auth_required")
        await self._ws.send_json({"type": "auth", "access_token": self._token()})
        reply = await self._ws.receive(timeout=_COMMAND_TIMEOUT)
        payload = json.loads(reply.data) if reply.type == aiohttp.WSMsgType.TEXT else {}
        if payload.get("type") != "auth_ok":
            raise PermissionError(f"HA 鉴权失败: {payload.get('message') or reply.type}")

    async def _full_sync(self) -> None:
        """全量同步：区域/设备/实体注册表 → 设备状态 → 订阅增量事件。"""
        areas = await self._command({"type": "config/area_registry/list"}) or []
        self._areas = {
            str(a.get("area_id")): str(a.get("name") or a.get("area_id"))
            for a in areas
        }
        devices = await self._command({"type": "config/device_registry/list"}) or []
        self._device_area = {
            str(d.get("id")): self._areas.get(str(d.get("area_id") or ""), "")
            for d in devices
        }
        entities = await self._command({"type": "config/entity_registry/list"}) or []
        self._entity_area = {}
        for entry in entities:
            entity_id = str(entry.get("entity_id") or "")
            area = self._areas.get(str(entry.get("area_id") or ""), "")
            if not area:
                area = self._device_area.get(str(entry.get("device_id") or ""), "")
            self._entity_area[entity_id] = area
        states = await self._command({"type": "get_states"}) or []
        self._devices = {
            str(s.get("entity_id")): self._build_device(s) for s in states
        }
        await self._command({"type": "subscribe_events", "event_type": "state_changed"})
        if self.on_sync is not None:
            self.on_sync()

    def _build_device(self, state: Dict[str, Any]) -> DeviceState:
        """从 HA state 对象构建设备快照（含房间归属解析）。"""
        entity_id = str(state.get("entity_id") or "")
        attributes = state.get("attributes") or {}
        return DeviceState(
            entity_id=entity_id,
            domain=entity_id.split(".", 1)[0],
            name=str(attributes.get("friendly_name") or entity_id),
            state=str(state.get("state") or ""),
            attributes=attributes,
            area=self._entity_area.get(entity_id, ""),
        )

    def _apply_state_changed(self, data: Dict[str, Any]) -> None:
        """增量维护设备缓存（new_state 为 None 表示实体已删除）。"""
        entity_id = str(data.get("entity_id") or "")
        new_state = data.get("new_state")
        if new_state is None:
            if self._devices.pop(entity_id, None) is not None:
                if self.on_sync is not None:
                    self.on_sync()
            return
        device = self._build_device(new_state)
        self._devices[device.entity_id] = device
        if self.on_device_update is not None:
            self.on_device_update(device)

    async def _command(self, payload: Dict[str, Any]) -> Any:
        """发送命令并等待 result（id 配对，超时/断线抛异常）。"""
        assert self._ws is not None
        self._msg_id += 1
        msg_id = self._msg_id
        future: "asyncio.Future[Any]" = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._ws.send_json({"id": msg_id, **payload})
            return await asyncio.wait_for(future, _COMMAND_TIMEOUT)
        finally:
            self._pending.pop(msg_id, None)

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        """分发服务端消息（result 办结命令 / event 维护缓存）。"""
        msg_type = msg.get("type")
        if msg_type == "result":
            future = self._pending.get(int(msg.get("id") or 0))
            if future is None or future.done():
                return
            if msg.get("success"):
                future.set_result(msg.get("result"))
            else:
                error = msg.get("error") or {}
                future.set_exception(
                    RuntimeError(str(error.get("message") or "HA 命令失败"))
                )
        elif msg_type == "event":
            event = msg.get("event") or {}
            if event.get("event_type") == "state_changed":
                self._apply_state_changed(event.get("data") or {})

    # ---- 控制 ----

    async def call_service(
        self, domain: str, service: str, entity_id: str, data: Dict[str, Any],
    ) -> None:
        if not self._connected:
            raise ConnectionError("未连接到 Home Assistant")
        await self._command({
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": {**data, "entity_id": entity_id},
        })

    def _emit_connection(self) -> None:
        if self.on_connection is not None:
            try:
                self.on_connection(self.status())
            except Exception:
                pass
