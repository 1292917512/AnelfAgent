"""NoneBot 桥接频道 — 完整 NoneBot 子进程客户端的父进程侧适配器。

架构（v3，子进程 + 独立 venv）：
- worker 以独立 venv 运行 ``worker/bot.py``（NoneBot2 + 适配器 + 插件），
  经桥接 WS（127.0.0.1:bridge_ws_port，token 鉴权）回连本频道；
- 入站：worker ``event_preprocessor`` 把平台事件转线协议上报 → 本频道
  转成 AdapterMessage → on_message → ChannelManager → AI；
- 出站：AI 回复经 send_text → 线协议发送请求（seq 关联）→ worker 按
  粘性路由选 Bot 调平台 API；
- 生命周期：start 引导 venv + spawn worker（后台任务，不阻塞频道启动），
  崩溃自动重启（指数退避），配置变更热重启。
"""

from __future__ import annotations

import asyncio
import hmac
import time
from typing import Any, ClassVar, Dict, List, Optional, Set

import aiohttp
from aiohttp import web

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
from agent.channel.channel_types import (
    ChannelCapability,
    ChannelStatus,
    _err,
    _ok,
)
from agent.channel.schemas import (
    AdapterMessage,
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    SendRequest,
    SendResponse,
)
from agent.channel.tool_bridge import channel_tool
from core.log import log

from .config import NONEBOT_BRIDGE_CONFIGS
from .runtime import get_nonebot_runtime
from .wire_in import wire_event_to_adapter_message
from .worker.protocol import (
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
    decode,
    encode,
)

_WS_PATH = "/bridge"
_STICKY_CAP = 2000
_SEND_TIMEOUT = 45.0
_PING_INTERVAL = 30.0


class NoneBotBridgeConfig(ChannelConfig):
    """NoneBot 桥接频道配置（平台相关项经 extra=allow 扩展）。"""


class NoneBotBridgeChannel(BaseChannel[NoneBotBridgeConfig]):
    """NoneBot 桥接频道 — 所有 NoneBot 适配器/插件的统一入口。"""

    _entity_description = "NoneBot 桥接频道（子进程客户端）"

    metadata = ChannelMetadata(
        name="NoneBot Bridge",
        description="完整 NoneBot 子进程客户端，统一桥接其全部适配器与插件生态",
        version="3.0.0",
        author="AnelfAgent",
    )
    _Configs = NoneBotBridgeConfig
    _adapter_configs = NONEBOT_BRIDGE_CONFIGS

    _SEGMENT_SENDERS: ClassVar[Dict[str, str]] = {
        "text": "send_text",
        "image": "send_photo",
    }

    channel_id = "nonebot_bridge"
    display_name = "NoneBot 桥接"

    capabilities: Set[ChannelCapability] = {
        ChannelCapability.SEND_TEXT,
        ChannelCapability.SEND_PHOTO,
    }

    def __init__(self) -> None:
        self._ws: Optional[web.WebSocketResponse] = None
        self._ws_runner: Optional[web.AppRunner] = None
        self._seq: int = 0
        self._pending: Dict[int, "asyncio.Future[Dict[str, Any]]"] = {}
        self._sticky_bot: Dict[str, str] = {}
        self._sticky_group: Dict[str, bool] = {}
        self._worker_snapshot: Dict[str, Any] = {}
        self._bootstrap_task: Optional[asyncio.Task[None]] = None
        self._ping_task: Optional[asyncio.Task[None]] = None
        self._last_pong: float = 0.0
        self._applied_cfg_sig: str = ""
        self._restart_lock = asyncio.Lock()
        super().__init__()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动桥接：先起 WS 服务，再后台引导 venv 并 spawn worker。"""
        if not self._cfg("adapters", []):
            log("NoneBot Bridge: 未配置适配器列表，频道不启动", "WARNING", tag="通道")
            self._status = ChannelStatus.STOPPED
            return

        self._status = ChannelStatus.STARTING
        await self._start_ws_server()

        runtime = get_nonebot_runtime()
        runtime.on_worker_restart = self._on_worker_restart
        self._applied_cfg_sig = self._cfg_sig()

        self._bootstrap_task = asyncio.create_task(
            self._bootstrap(), name="nb_bootstrap"
        )

    async def stop(self) -> None:
        """停止桥接：停 worker 进程、断开 WS、释放服务资源。"""
        runtime = get_nonebot_runtime()
        runtime.on_worker_restart = None
        await runtime.stop_worker()

        if self._bootstrap_task:
            self._bootstrap_task.cancel()
            try:
                await self._bootstrap_task
            except asyncio.CancelledError:
                pass
            self._bootstrap_task = None
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
            self._ping_task = None

        self._fail_pending("频道已停止")
        await self._stop_ws_server()
        self._status = ChannelStatus.STOPPED
        log("NoneBot Bridge: 频道已停止", tag="通道")

    async def restart_worker(self) -> str:
        """重启 worker 子进程（Web / AI 工具共用入口，串行化防双 spawn）。"""
        if self._status == ChannelStatus.STOPPED:
            return _err("频道未启用")
        async with self._restart_lock:
            runtime = get_nonebot_runtime()
            self._applied_cfg_sig = self._cfg_sig()
            await runtime.stop_worker()
            await self._spawn_worker_locked()
        return _ok({"message": "worker 重启已触发"})

    async def _bootstrap(self) -> None:
        """后台引导：确保 venv → 渲染 worker 文件 → spawn。"""
        runtime = get_nonebot_runtime()
        python_exec = str(self._cfg("python_exec", "") or "")
        uv_exec = str(self._cfg("uv_exec", "") or "")
        try:
            await runtime.ensure_venv(python_exec=python_exec, uv_exec=uv_exec)
            runtime.write_worker_files(self._worker_cfg_dict())
            await runtime.start_worker(
                int(self._cfg("bridge_ws_port", 8197)),
                auto_restart=bool(self._cfg("auto_restart", True)),
                python_exec=python_exec,
                uv_exec=uv_exec,
            )
            self._start_ping()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"NoneBot Bridge: worker 启动失败 - {exc}", "ERROR", tag="通道")
            runtime.append_log(f"worker 启动失败: {exc}")
            self._status = ChannelStatus.ERROR

    async def _on_worker_restart(self) -> None:
        """runtime 崩溃自动重启回调：刷新 worker 文件并重新 spawn。"""
        async with self._restart_lock:
            await self._spawn_worker_locked()

    async def _spawn_worker_locked(self) -> None:
        """渲染 worker 文件并 spawn（调用方须持有 _restart_lock）。"""
        runtime = get_nonebot_runtime()
        runtime.write_worker_files(self._worker_cfg_dict())
        await runtime.start_worker(
            int(self._cfg("bridge_ws_port", 8197)),
            auto_restart=bool(self._cfg("auto_restart", True)),
            python_exec=str(self._cfg("python_exec", "") or ""),
            uv_exec=str(self._cfg("uv_exec", "") or ""),
        )

    def reload_config(self) -> bool:
        """热重载配置；worker 相关配置变更时自动热重启 worker。"""
        result = super().reload_config()
        if self._status == ChannelStatus.STOPPED:
            self._applied_cfg_sig = self._cfg_sig()
            return result

        current_sig = self._cfg_sig()
        if current_sig != self._applied_cfg_sig:
            self._applied_cfg_sig = current_sig
            log("NoneBot Bridge: 检测到 worker 配置变更，触发热重启", tag="通道")
            asyncio.create_task(self.restart_worker(), name="nb_hot_restart")
        return result

    # ------------------------------------------------------------------
    # 桥接 WS 服务端
    # ------------------------------------------------------------------

    async def _start_ws_server(self) -> None:
        """启动桥接 WS 服务（仅回环，token 鉴权）。"""
        app = web.Application()
        app.router.add_get(_WS_PATH, self._bridge_ws_handler)
        self._ws_runner = web.AppRunner(app)
        await self._ws_runner.setup()
        port = int(self._cfg("bridge_ws_port", 8197))
        site = web.TCPSite(self._ws_runner, "127.0.0.1", port)
        await site.start()
        log(
            f"NoneBot Bridge: 桥接 WS 服务已启动 ws://127.0.0.1:{port}{_WS_PATH}",
            tag="通道",
        )

    async def _stop_ws_server(self) -> None:
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except OSError:
                log("NoneBot Bridge: 关闭桥接 WS 异常", "DEBUG", tag="通道")
        self._ws = None
        if self._ws_runner:
            try:
                await self._ws_runner.cleanup()
            except Exception as exc:
                log(f"NoneBot Bridge: 清理 WS runner 异常 -> {exc}", "DEBUG", tag="通道")
            self._ws_runner = None

    async def _bridge_ws_handler(self, request: web.Request) -> web.StreamResponse:
        """处理 worker 回连（token 鉴权，重复连接顶替旧连接）。"""
        runtime = get_nonebot_runtime()
        provided = request.query.get("token", "")
        if not runtime.token or not hmac.compare_digest(provided, runtime.token):
            return web.Response(status=403, text="Forbidden")

        ws = web.WebSocketResponse(max_msg_size=32 * 1024 * 1024)
        await ws.prepare(request)

        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except OSError:
                pass
        self._ws = ws
        self._last_pong = time.time()
        if self._status in (ChannelStatus.STARTING, ChannelStatus.RECONNECTING):
            self._status = ChannelStatus.RUNNING
            log("NoneBot Bridge: worker 已回连，频道运行中", tag="通道")

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    payload = decode(msg.data)
                    if payload is not None:
                        await self._handle_wire(payload)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            if self._ws is ws:
                self._ws = None
                self._fail_pending("worker 连接已断开")
                runtime = get_nonebot_runtime()
                if self._status == ChannelStatus.RUNNING and runtime.is_process_alive():
                    self._status = ChannelStatus.RECONNECTING
                    log("NoneBot Bridge: worker 连接断开，等待重连", "WARNING", tag="通道")
        return ws

    async def _handle_wire(self, payload: Dict[str, Any]) -> None:
        """按消息类型分发线协议消息。"""
        msg_type = payload.get("type", "")

        if msg_type == MSG_EVENT:
            message = wire_event_to_adapter_message(payload)
            if message is not None:
                await self._process_inbound(message, payload)
        elif msg_type in (MSG_HELLO, MSG_STATUS):
            self._worker_snapshot = payload
        elif msg_type == MSG_LOG:
            get_nonebot_runtime().append_log(str(payload.get("line", "")))
        elif msg_type == MSG_SEND_RESULT:
            self._resolve_pending(payload)
        elif msg_type == MSG_CMD_RESULT:
            self._resolve_pending(payload)
        elif msg_type == MSG_PONG:
            self._last_pong = time.time()

    async def _process_inbound(self, message: AdapterMessage, payload: Dict[str, Any]) -> None:
        """处理入站事件：更新粘性路由，按需下载图片，分发到频道系统。"""
        chat_key = message.channel.channel_id
        bot_id = str(payload.get("bot_id", "") or "")
        if bot_id:
            self._set_sticky(self._sticky_bot, chat_key, bot_id)
        self._set_sticky(
            self._sticky_group, chat_key,
            message.channel.channel_type == ChannelType.GROUP,
        )

        if message.is_to_me:
            await self._auto_download_images(message)

        await self.on_message(message)

    async def _auto_download_images(self, message: AdapterMessage) -> None:
        """to_me 消息的图片自动下载到 uploads（供视觉模型直接读取）。"""
        from agent.channel.media import download_to_uploads
        from agent.channel.schemas import SegmentType

        for seg in message.segments:
            if seg.type != SegmentType.IMAGE:
                continue
            if seg.file_path and not seg.url.startswith(("http://", "https://")):
                continue
            if not seg.url.startswith(("http://", "https://")):
                continue
            try:
                path = await download_to_uploads(seg.url, SegmentType.IMAGE)
                if path:
                    seg.file_path = path
            except Exception as exc:
                log(f"NoneBot Bridge: 图片下载失败 -> {exc}", "DEBUG", tag="通道")

    @staticmethod
    def _set_sticky(store: Dict[str, Any], key: str, value: Any) -> None:
        """粘性路由表写入（超限时淘汰最早插入项）。"""
        store.pop(key, None)
        store[key] = value
        if len(store) > _STICKY_CAP:
            excess = len(store) - _STICKY_CAP
            for stale in list(store.keys())[:excess]:
                store.pop(stale, None)

    def is_known_group(self, target_id: str) -> bool:
        """判断 target 是否为已知群聊（主动发送场景的群/私聊推断）。"""
        return bool(self._sticky_group.get(target_id, False))

    # ------------------------------------------------------------------
    # 出站发送（WS 请求 + seq 关联）
    # ------------------------------------------------------------------

    def _resolve_bot_id(self, chat_id: str, **kwargs: Any) -> str:
        """发送路由：显式 bot_id > 粘性路由 > 空（worker 取首个在线）。"""
        bot_id = str(kwargs.get("bot_id", "") or "")
        if bot_id:
            return bot_id
        return self._sticky_bot.get(chat_id, "")

    async def _ws_request(
        self, payload: Dict[str, Any], timeout: float = _SEND_TIMEOUT
    ) -> Optional[Dict[str, Any]]:
        """向 worker 发送 seq 关联请求并等待结果。"""
        ws = self._ws
        if ws is None or ws.closed:
            return {"ok": False, "error": "worker 未连接"}

        self._seq += 1
        seq = self._seq
        payload["seq"] = seq
        loop = asyncio.get_running_loop()
        future: "asyncio.Future[Dict[str, Any]]" = loop.create_future()
        self._pending[seq] = future

        try:
            await ws.send_str(encode(payload))
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "worker 响应超时"}
        except Exception as exc:
            return {"ok": False, "error": f"发送异常: {exc}"}
        finally:
            self._pending.pop(seq, None)

    def _resolve_pending(self, payload: Dict[str, Any]) -> None:
        """回填 seq 关联的请求结果。"""
        seq = payload.get("seq")
        if not isinstance(seq, int):
            return
        future = self._pending.get(seq)
        if future is not None and not future.done():
            future.set_result(payload)

    def _fail_pending(self, reason: str) -> None:
        """断开时失败化所有挂起请求。"""
        for future in self._pending.values():
            if not future.done():
                future.set_result({"ok": False, "error": reason})
        self._pending.clear()

    async def send_text(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: Optional[str] = None,
        parse_mode: Optional[str] = None,
        silent: bool = False,
        channel_type: str = "private",
        **kwargs: Any,
    ) -> str:
        """经 worker 向平台发送文本（粘性路由选 Bot）。"""
        channel_type = channel_type or (
            "group" if self.is_known_group(chat_id) else "private"
        )
        result = await self._ws_request({
            "type": MSG_SEND,
            "bot_id": self._resolve_bot_id(chat_id, **kwargs),
            "adapter": str(kwargs.get("adapter", "") or ""),
            "target": chat_id,
            "channel_type": channel_type,
            "text": self.normalize_at_mentions(text),
            "reply_to": reply_to or "",
        })
        if result and result.get("ok"):
            return _ok({
                "chat_id": chat_id,
                "message_id": str(result.get("message_id", "") or ""),
            })
        return _err(str((result or {}).get("error") or "发送失败"))

    async def send_photo(
        self,
        chat_id: str,
        photo: str,
        caption: str = "",
        **kwargs: Any,
    ) -> str:
        """经 worker 向平台发送图片（本地路径或 URL）。"""
        channel_type = str(kwargs.get("channel_type", "") or "") or (
            "group" if self.is_known_group(chat_id) else "private"
        )
        result = await self._ws_request({
            "type": MSG_SEND,
            "bot_id": self._resolve_bot_id(chat_id, **kwargs),
            "adapter": str(kwargs.get("adapter", "") or ""),
            "target": chat_id,
            "channel_type": channel_type,
            "text": self.normalize_at_mentions(caption),
            "image": photo,
        })
        if result and result.get("ok"):
            return _ok({
                "chat_id": chat_id,
                "message_id": str(result.get("message_id", "") or ""),
            })
        return _err(str((result or {}).get("error") or "发送失败"))

    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """统一发送入口（段分发模板见 BaseChannel._forward_via_segment_map）。"""
        return await self._forward_via_segment_map(request)

    async def get_self_info(self) -> ChannelUser:
        bots = self._worker_snapshot.get("bots") or []
        bot_id = ""
        if isinstance(bots, list) and bots:
            first = bots[0]
            if isinstance(first, dict):
                bot_id = str(first.get("bot_id", "") or "")
        return ChannelUser(
            platform=self.channel_id,
            user_id=bot_id or "nonebot_bridge_bot",
            user_name="NoneBot Bridge",
            role=ChannelUserRole.MEMBER,
            is_bot=True,
        )

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        return ChannelInfo(
            channel_id=channel_id,
            channel_name=channel_id,
            channel_type=ChannelType.GROUP if self.is_known_group(channel_id) else ChannelType.PRIVATE,
        )

    async def health_check(self) -> HealthStatus:
        """健康探针：worker 进程存活 + 桥接 WS 已回连。"""
        runtime = get_nonebot_runtime()
        if not runtime.is_venv_ready():
            return HealthStatus(
                healthy=False, detail="worker venv 未就绪", last_error="venv_missing"
            )
        if not runtime.is_process_alive():
            return HealthStatus(
                healthy=False, detail="worker 进程未运行", last_error="process_dead"
            )
        if self._ws is None or self._ws.closed:
            return HealthStatus(
                healthy=False, detail="worker 未回连桥接服务", last_error="bridge_disconnected"
            )
        bots = self._worker_snapshot.get("bots") or []
        return HealthStatus(
            healthy=True,
            detail=f"worker OK: {len(bots)} 个 Bot 在线",
            last_success_at=time.time(),
        )

    def get_status_info(self) -> Dict[str, Any]:
        info = super().get_status_info()
        worker_host = str(self._cfg("worker_host", "127.0.0.1"))
        worker_port = int(self._cfg("worker_port", 8198))
        info.update({
            "bridge_connected": self._ws is not None and not self._ws.closed,
            "worker": get_nonebot_runtime().get_process_status(),
            "worker_snapshot": {
                "nonebot_version": self._worker_snapshot.get("nonebot_version", ""),
                "adapters": self._worker_snapshot.get("adapters", []),
                "bots": self._worker_snapshot.get("bots", []),
                "plugins": self._worker_snapshot.get("plugins", []),
            },
            "worker_base_url": f"http://{worker_host}:{worker_port}",
            "intercept_all": bool(self._cfg("intercept_all", False)),
        })
        return info

    # ------------------------------------------------------------------
    # worker 状态查询（Web / AI 共用）
    # ------------------------------------------------------------------

    async def fetch_worker_status(self) -> Dict[str, Any]:
        """向 worker 拉取实时状态快照（未连接时回退最近缓存）。"""
        result = await self._ws_request(
            {"type": MSG_CMD, "action": "get_status"}, timeout=10.0
        )
        if result and result.get("ok") and isinstance(result.get("status"), dict):
            self._worker_snapshot = result["status"]
        return dict(self._worker_snapshot)

    async def run_worker_command(
        self, command: str, bot_id: str = "", adapter: str = ""
    ) -> Dict[str, Any]:
        """经 worker 合成事件触发插件命令并捕获回复。"""
        return await self._ws_request({
            "type": MSG_CMD,
            "action": CMD_RUN_COMMAND,
            "command": command,
            "bot_id": bot_id,
            "adapter": adapter,
        }, timeout=70.0) or {"ok": False, "error": "worker 未连接"}

    # ------------------------------------------------------------------
    # 配置签名（worker 相关配置变更检测）
    # ------------------------------------------------------------------

    def _worker_cfg_dict(self) -> Dict[str, Any]:
        """提取渲染 worker 文件所需的配置子集。"""
        return {
            "adapters": self._cfg("adapters", []) or [],
            "plugins": self._cfg("plugins", []) or [],
            "nonebot_env": self._cfg("nonebot_env", {}) or {},
            "intercept_all": bool(self._cfg("intercept_all", False)),
            "worker_host": str(self._cfg("worker_host", "127.0.0.1")),
            "worker_port": int(self._cfg("worker_port", 8198)),
        }

    def _cfg_sig(self) -> str:
        import json

        return json.dumps(self._worker_cfg_dict(), sort_keys=True, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------

    def _start_ping(self) -> None:
        if self._ping_task and not self._ping_task.done():
            return
        self._ping_task = asyncio.create_task(self._ping_loop(), name="nb_ping")

    async def _ping_loop(self) -> None:
        """定期 ping worker；连续未响应仅告警（连接层自会报错断开）。"""
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            ws = self._ws
            if ws is None or ws.closed:
                continue
            try:
                self._seq += 1
                await ws.send_str(encode({"type": MSG_PING, "seq": self._seq}))
            except Exception:
                continue
            if time.time() - self._last_pong > _PING_INTERVAL * 3:
                log("NoneBot Bridge: worker 心跳超时", "WARNING", tag="通道")

    # ------------------------------------------------------------------
    # AI 工具（@channel_tool，group=nonebot）
    # ------------------------------------------------------------------

    @channel_tool(
        name="nonebot_status",
        group="nonebot",
        extra_tags=["always"],
        description="查询 NoneBot 桥接状态：worker 进程、在线 Bot、已加载适配器与插件",
    )
    async def tool_nonebot_status(self) -> str:
        """查询 NoneBot 桥接运行状态（进程 / Bot / 适配器 / 插件全景）。"""
        snapshot = await self.fetch_worker_status()
        runtime = get_nonebot_runtime()
        return _ok({
            "process": runtime.get_process_status(),
            "nonebot_version": snapshot.get("nonebot_version", ""),
            "adapters": snapshot.get("adapters", []),
            "bots": snapshot.get("bots", []),
            "plugins": [
                p.get("module", "") for p in snapshot.get("plugins", []) if isinstance(p, dict)
            ],
            "intercept_all": bool(self._cfg("intercept_all", False)),
        })

    @channel_tool(
        name="nonebot_restart",
        group="nonebot",
        extra_tags=["always"],
        sensitive=True,
        description="重启 NoneBot worker 子进程（适配器/插件/env 配置变更后生效）",
    )
    async def tool_nonebot_restart(self) -> str:
        """重启 NoneBot worker 子进程。"""
        return await self.restart_worker()

    @channel_tool(
        name="nonebot_list_plugins",
        group="nonebot",
        extra_tags=["always"],
        description="列出 NoneBot 已加载插件及其功能说明与用法",
    )
    async def tool_nonebot_list_plugins(self) -> str:
        """列出 worker 中已加载的 NoneBot 插件（含用法说明）。"""
        snapshot = await self.fetch_worker_status()
        plugins: List[Dict[str, Any]] = [
            p for p in snapshot.get("plugins", []) if isinstance(p, dict)
        ]
        if not plugins:
            return _ok({"plugins": [], "hint": "暂无已加载插件，可用 nonebot_install_plugin 安装"})
        return _ok({"count": len(plugins), "plugins": plugins})

    @channel_tool(
        name="nonebot_store_search",
        group="nonebot",
        extra_tags=["always"],
        description="搜索 NoneBot 插件商店（registry.nonebot.dev），返回名称/描述/作者/版本",
    )
    async def tool_nonebot_store_search(self, keyword: str, limit: int = 8) -> str:
        """按关键词搜索 NoneBot 插件商店。"""
        from services.nonebot import NoneBotService

        results = await NoneBotService().search_store_plugins(keyword, limit=limit)
        if not results:
            return _ok({"keyword": keyword, "results": [], "hint": "未找到匹配插件"})
        return _ok({"keyword": keyword, "count": len(results), "results": results})

    @channel_tool(
        name="nonebot_install_plugin",
        group="nonebot",
        extra_tags=["always"],
        sensitive=True,
        description="从 NoneBot 插件商店安装插件到 worker 并重启生效",
    )
    async def tool_nonebot_install_plugin(self, module_name: str) -> str:
        """安装商店插件（module_name 需与商店一致，如 nonebot_plugin_status）。"""
        from services.nonebot import NoneBotService

        result = await NoneBotService().install_plugin(module_name)
        return _ok(result) if result.get("success") else _err(str(result.get("error") or "安装失败"))

    @channel_tool(
        name="nonebot_run_command",
        group="nonebot",
        extra_tags=["always"],
        description="以虚拟用户身份触发 NoneBot 插件命令并捕获回复（不发送到平台）",
    )
    async def tool_nonebot_run_command(
        self, command: str, bot_id: str = "", adapter: str = ""
    ) -> str:
        """触发插件命令（如 /status），返回插件捕获的回复。"""
        result = await self.run_worker_command(command, bot_id=bot_id, adapter=adapter)
        if not result.get("ok"):
            return _err(str(result.get("error") or "命令执行失败"))
        return _ok({
            "command": command,
            "replies": result.get("replies", []),
            "timeout": bool(result.get("timeout", False)),
        })

    @channel_tool(
        name="nonebot_send",
        group="nonebot",
        extra_tags=["always"],
        description="经 NoneBot 桥接向指定平台目标发送消息（可指定 bot_id / adapter）",
    )
    async def tool_nonebot_send(
        self,
        chat_id: str,
        text: str,
        channel_type: str = "private",
        bot_id: str = "",
        adapter: str = "",
    ) -> str:
        """向桥接平台显式发送消息（目标为群号/用户号，channel_type 为 group/private）。"""
        return await self.send_text(
            chat_id,
            text,
            channel_type=channel_type,
            bot_id=bot_id,
            adapter=adapter,
        )


CHANNEL_CLASS = NoneBotBridgeChannel
