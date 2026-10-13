"""wcauto 频道 — 微信个人号频道：经 Windows 桥（bridge/）远程对接微信 4.x 客户端。

架构：AnelfAgent（本进程，WS 客户端） ↔ Windows 桥（WS 服务端，贴微信客户端）。
- 收消息：桥轮询微信本地解密数据库推送事件（不操作界面，锁屏可收）
- 发消息：桥驱动微信 GUI（UIA 主路径 + OCR 兜底，窗口须可见）
- 可靠性：hello 握手带 last_seq 断线回补；入站二级去重；RPC 超时/断线显式失败

模块拆分：
- transport.py：WS 连接与 RPC（WcautoTransport，注入频道引用）
- send.py：出站构造（WcautoSender，注入频道引用）
- parser.py：入站事件纯解析
- tools.py：wcauto_* 工具面（WcautoToolsMixin，经多继承装配）
- state.py：水位持久化 + 去重
- bridge/：Windows 侧桥服务（自包含，可整目录拷贝部署）
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from agent.channel.base import BaseChannel, ChannelMetadata
from agent.channel.channel_types import ChannelCapability
from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    MessageKind,
    SegmentType,
    SendRequest,
    SendResponse,
)
from core.log import log

from . import parser as wc_parser
from .bridge.wcauto_bridge import protocol
from .config import WcautoConfig
from .send import WcautoSender
from .state import WcautoStateStore, make_dedup
from .tools import WcautoToolsMixin
from .transport import WcautoRpcError, WcautoTransport


class WcautoChannel(WcautoToolsMixin, BaseChannel[WcautoConfig]):
    """微信个人号频道（wcauto）：远程桥对接 Windows 微信客户端。"""

    _entity_description = "微信个人号频道（wcauto 远程桥）"

    channel_id = "wcauto"
    display_name = "微信（wcauto）"
    display_order = 21

    metadata = ChannelMetadata(
        name="WeChat (wcauto)",
        description="微信个人号频道：经 Windows 桥对接微信 4.x 桌面客户端（远程 WS）",
        version="1.0.0",
        author="AnelfAgent",
        tags=["wechat", "wcauto", "windows-bridge"],
    )
    _Configs = WcautoConfig

    capabilities: Set[ChannelCapability] = {
        ChannelCapability.SEND_TEXT,
        ChannelCapability.SEND_PHOTO,
        ChannelCapability.SEND_VIDEO,
        ChannelCapability.SEND_FILE,
        ChannelCapability.GET_CHAT_INFO,
        ChannelCapability.GET_CHAT_MEMBERS,
    }

    # 段分发映射：微信桌面端可发 文本/图片/文件/视频(以文件形态)
    _SEGMENT_SENDERS = {
        "text": "send_text",
        "image": "send_photo",
        "video": "send_video",
        "file": "send_file",
    }

    def __init__(self) -> None:
        self._state_store = WcautoStateStore()
        self._dedup = make_dedup()
        self._transport = WcautoTransport(self)
        self._sender = WcautoSender(self)
        self._known_chats: Dict[str, Dict[str, Any]] = {}
        super().__init__()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        await self._transport.start()

    async def stop(self) -> None:
        self._state_store.flush()
        await self._transport.stop()

    # ------------------------------------------------------------------
    # 桥事件入口
    # ------------------------------------------------------------------

    async def _on_bridge_event(self, event: Dict[str, Any]) -> None:
        """桥事件分发：message → 解析+策略+媒体 → on_message；recall/status 进会话。"""
        name = event.get("event")
        data = event.get("data") or {}
        if name == protocol.EVENT_MESSAGE:
            await self._handle_message_event(data)
        elif name == protocol.EVENT_RECALL:
            await self._handle_recall_event(data)
        elif name == protocol.EVENT_STATUS:
            log(f"wcauto: 桥状态 [{data.get('state')}] {data.get('detail', '')}",
                tag="通道")

    async def _handle_message_event(self, data: Dict[str, Any]) -> None:
        dedup_key = f"{data.get('username')}:{data.get('shard')}:{data.get('local_id')}"
        if self._dedup.is_duplicate(dedup_key):
            return

        self_wxid = str(self._transport.self_info.get("username") or "")
        message = wc_parser.parse_message_event(
            data,
            self_wxid=self_wxid,
            self_names=self._self_names(),
            require_mention=bool(self._cfg("require_mention", True)),
        )
        if message is None:
            return
        if not self._check_policy(message, data):
            return

        self._remember_chat(message)
        await self._maybe_download_media(message)

        # 群聊未 @我 时只入库不唤醒（与 QQ require_mention 语义一致）
        if message.channel.channel_type == ChannelType.GROUP and not message.is_to_me:
            message.trigger_mind = False
        await self.on_message(message)

    async def _handle_recall_event(self, data: Dict[str, Any]) -> None:
        """撤回事件：以系统消息写入原会话（不唤醒思考，AI 可见上下文）。"""
        if not self._cfg("recall_notify", True):
            return
        chat = str(data.get("chat") or "")
        if not chat:
            return
        revoker = str(data.get("revoker") or "对方")
        original = str(data.get("original_content") or "").strip()
        text = f"[撤回] {revoker} 撤回了一条消息"
        if original:
            text += f"，原文：{original[:300]}"
        is_group = chat.endswith("@chatroom")
        message = AdapterMessage(
            sender=AdapterUser(
                platform=self.channel_id,
                user_id=f"{self.channel_id}_system",
                user_name="微信系统",
            ),
            channel=AdapterChannel(
                channel_id=chat,
                channel_type=ChannelType.GROUP if is_group else ChannelType.PRIVATE,
                channel_name=str(data.get("chat_name") or chat),
            ),
            content=text,
            kind=MessageKind.SYSTEM,
            is_to_me=False,
            trigger_mind=False,
            timestamp=float(data.get("create_time") or time.time()),
        )
        await self.on_message(message)

    # ------------------------------------------------------------------
    # 访问策略
    # ------------------------------------------------------------------

    def _self_names(self) -> List[str]:
        bridge_nick = str(self._transport.self_info.get("nick_name") or "")
        return wc_parser.collect_self_names(str(self._cfg("self_names", "")), bridge_nick)

    def _check_policy(self, message: AdapterMessage, data: Dict[str, Any]) -> bool:
        """私聊/群聊策略 + 忽略清单；白名单同时认 wxid 与昵称。"""
        ignores = {x.strip() for x in str(self._cfg("ignore_chats", "")).split(",") if x.strip()}
        chat_id = message.channel.channel_id
        chat_name = str(data.get("chat_name") or "")
        if chat_id in ignores or (chat_name and chat_name in ignores):
            return False

        if message.channel.channel_type == ChannelType.GROUP:
            policy = str(self._cfg("group_policy", "open"))
            allow = str(self._cfg("group_allow_from", ""))
        else:
            policy = str(self._cfg("dm_policy", "open"))
            allow = str(self._cfg("dm_allow_from", ""))

        if policy == "disabled":
            return False
        if policy == "allowlist":
            allowed = {x.strip() for x in allow.split(",") if x.strip()}
            keys = {chat_id, chat_name, message.sender.user_id, message.sender.user_name}
            return bool(keys & allowed)
        return True

    def _remember_chat(self, message: AdapterMessage) -> None:
        chat_id = message.channel.channel_id
        self._known_chats[chat_id] = {
            "chat_id": chat_id,
            "chat_name": message.channel.channel_name or chat_id,
            "channel_type": message.channel.channel_type.value,
            "last_active": time.time(),
        }
        if len(self._known_chats) > 1000:
            oldest = sorted(self._known_chats.items(), key=lambda kv: kv[1]["last_active"])
            for key, _ in oldest[:200]:
                self._known_chats.pop(key, None)

    # ------------------------------------------------------------------
    # 入站媒体自动下载
    # ------------------------------------------------------------------

    async def _maybe_download_media(self, message: AdapterMessage) -> None:
        """私聊/@我 的媒体按配置自动下载到 workspace/uploads/wcauto/。"""
        if message.channel.channel_type == ChannelType.GROUP and not message.is_to_me:
            return
        auto = {
            SegmentType.IMAGE: bool(self._cfg("auto_download_image", True)),
            SegmentType.VOICE: bool(self._cfg("auto_download_voice", True)),
            SegmentType.FILE: bool(self._cfg("auto_download_file", False)),
            SegmentType.VIDEO: bool(self._cfg("auto_download_video", False)),
        }
        for seg in message.segments:
            if seg.type not in auto or not auto[seg.type] or not seg.file_id:
                continue
            try:
                saved = await self._download_media(
                    message.channel.channel_id, int(seg.file_id))
            except Exception as exc:
                log(f"wcauto: 媒体自动下载失败 {seg.file_id} -> {exc}", "DEBUG", tag="通道")
                continue
            if saved:
                seg.file_path = saved
                seg.content = f"{seg.content}（已下载: {saved}）"

    async def _download_media(self, chat_id: str, local_id: int) -> Optional[str]:
        """经桥下载媒体并落 workspace/uploads/wcauto/，返回本地路径。"""
        data = await self._transport.call_rpc(protocol.RPC_DOWNLOAD_MEDIA, {
            "chat": chat_id, "local_id": int(local_id)})
        if not isinstance(data, dict) or not data.get("data_b64"):
            return None
        import base64
        raw = base64.b64decode(data["data_b64"])
        try:
            from core.config import ConfigManager
            ws = str(ConfigManager.get("workspace_root", "workspace") or "workspace")
        except Exception:
            ws = "workspace"
        sub = os.path.join("wcauto", time.strftime("%Y-%m"))
        out_dir = Path(os.path.abspath(os.path.join(ws, "uploads", sub)))
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = str(data.get("filename") or f"{local_id}.bin")
        safe = "".join(c if c not in '<>:"/\\|?*' else "_" for c in filename)
        path = out_dir / f"{int(time.time())}_{local_id}_{safe}"
        await asyncio.to_thread(path.write_bytes, raw)
        return str(path)

    # ------------------------------------------------------------------
    # 发送路径（委托 WcautoSender）
    # ------------------------------------------------------------------

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        return await self._sender.send_text(chat_id, text, **kwargs)

    async def send_photo(self, chat_id: str, photo: str, caption: str = "",
                         **kwargs: Any) -> str:
        return await self._sender.send_photo(chat_id, photo, caption=caption, **kwargs)

    async def send_video(self, chat_id: str, video: str, caption: str = "",
                         **kwargs: Any) -> str:
        return await self._sender.send_video(chat_id, video, caption=caption, **kwargs)

    async def send_file(self, chat_id: str, file_path: str, caption: str = "",
                        **kwargs: Any) -> str:
        return await self._sender.send_file(chat_id, file_path, caption=caption, **kwargs)

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """统一发送入口：逐段分发（携带群/私语义；单段失败不中断，整体按段汇总）。"""
        import json as _json

        chat_id = request.channel.channel_id
        channel_type = ("group" if request.channel.channel_type == ChannelType.GROUP
                        else "private")
        dispatch = {
            SegmentType.TEXT: lambda s: self.send_text(
                chat_id, s.content, channel_type=channel_type),
            SegmentType.IMAGE: lambda s: self.send_photo(
                chat_id, s.file_path or s.content, caption=s.caption,
                channel_type=channel_type),
            SegmentType.VIDEO: lambda s: self.send_video(
                chat_id, s.file_path or s.content, caption=s.caption,
                channel_type=channel_type),
            SegmentType.FILE: lambda s: self.send_file(
                chat_id, s.file_path or s.content, caption=s.caption,
                channel_type=channel_type),
        }
        message_ids: List[str] = []
        failures: List[str] = []
        for seg in request.segments:
            handler = dispatch.get(seg.type)
            if handler is None:
                failures.append(f"段类型 {seg.type.value} 不受支持")
                continue
            try:
                result = _json.loads(await handler(seg))
            except Exception as exc:
                failures.append(f"段类型 {seg.type.value} 发送异常: {exc}")
                continue
            if not result.get("success"):
                failures.append(f"段类型 {seg.type.value} 发送失败: "
                                f"{result.get('error') or '未知错误'}")
                continue
            if result.get("message_id"):
                message_ids.append(str(result["message_id"]))
        if failures:
            return SendResponse(
                success=False, error="；".join(failures),
                message_id=message_ids[0] if message_ids else "",
                message_ids=message_ids)
        return SendResponse(
            success=True,
            message_id=message_ids[0] if message_ids else "ok",
            message_ids=message_ids)

    # ------------------------------------------------------------------
    # 信息查询
    # ------------------------------------------------------------------

    async def get_self_info(self) -> ChannelUser:
        info = self._transport.self_info
        if not info:
            if not self._transport.connected:
                raise RuntimeError("wcauto 桥未连接")
            try:
                info = await self._transport.call_rpc(protocol.RPC_GET_SELF_INFO, {})
                self._transport.self_info = dict(info or {})
            except WcautoRpcError as exc:
                raise RuntimeError(f"获取微信账号信息失败: {exc}") from exc
        return ChannelUser(
            platform=self.channel_id,
            user_id=str(info.get("username") or ""),
            user_name=str(info.get("nick_name") or "微信"),
            role=ChannelUserRole.MEMBER,
            is_bot=False,
        )

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        known = self._known_chats.get(channel_id, {})
        return ChannelInfo(
            channel_id=channel_id,
            channel_name=known.get("chat_name") or channel_id,
            channel_type=(ChannelType.GROUP if channel_id.endswith("@chatroom")
                          else ChannelType.PRIVATE),
        )

    # ------------------------------------------------------------------
    # 健康探针 / 状态
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """真实探测：桥 ping RPC（桥离线/超时判不健康）。"""
        if not self._transport.connected:
            return HealthStatus(
                healthy=False, detail="桥未连接", last_error="bridge_disconnected")
        started = time.time()
        try:
            data = await self._transport.call_rpc(protocol.RPC_PING, {}, timeout=10.0)
        except Exception as exc:
            return HealthStatus(healthy=False, detail=str(exc), last_error=str(exc))
        latency_ms = (time.time() - started) * 1000
        listener_ok = bool((data or {}).get("listener_running"))
        window = "窗口可见" if (data or {}).get("window_visible") else "窗口不可见（仅收不发）"
        return HealthStatus(
            healthy=listener_ok,
            detail=f"桥在线（{data.get('nickname', '?')}，{window}）",
            latency_ms=latency_ms,
            last_success_at=time.time(),
        )

    def get_status_info(self) -> Dict[str, Any]:
        info = super().get_status_info()
        info["ws_url"] = self._cfg("ws_url", "")
        info["ws_connected"] = self._transport.connected
        info["last_seq"] = self._state_store.load_seq()
        self_info = self._transport.self_info
        if self_info:
            info["account"] = self_info.get("username", "")
            info["nickname"] = self_info.get("nick_name", "")
        info["detail"] = (
            f"已连接桥 {info['ws_url']}（{info.get('nickname') or '?'}）"
            if info["ws_connected"] else f"连接桥 {info['ws_url']} 中"
        )
        return info

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _cfg(self, key: str, default: Any = None) -> Any:
        return getattr(self.config, key, default)


CHANNEL_CLASS = WcautoChannel
