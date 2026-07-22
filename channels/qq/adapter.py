"""QQ 频道 — 通过 OneBot v11 协议对接 NapCat / Lagrange 等 QQ 机器人实现。

支持正向 WebSocket（主动连接）和反向 WebSocket（被动接收）两种模式。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import aiohttp
from aiohttp import web

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus, _ok, _err
from agent.channel.schemas import (
    AdapterChannel, ChannelType, SendRequest, SendResponse,
    ChannelInfo, ChannelUser, ChannelUserRole, HealthStatus,
)
import time
from pydantic import Field
from agent.channel.schemas import ChannelType, SegmentType
from agent.channel.tool_bridge import channel_tool
from core.log import log

from .parser import parse_event, parse_event_async


_AT_PATTERN = re.compile(r'\[at_uid:([^\]]+)\]')
_SECTION_SPLIT_RE = re.compile(r'={3,}')


def _ok_raw(data: Any) -> str:
    """构造成功响应，非 dict 的 data 自动包装为 data 字段。"""
    return _ok(data if isinstance(data, dict) else {"data": data})


def _split_forward_sections(text: str, max_lines_per_section: int = 20) -> List[str]:
    """将长文本智能拆分为合并转发的多段内容。

    拆分优先级：分隔符 ``===`` > 双换行 > 固定行数。
    """
    if _SECTION_SPLIT_RE.search(text):
        parts = re.split(r'\n(?=={3,})', text)
    elif '\n\n' in text:
        parts = text.split('\n\n')
    else:
        lines = text.split('\n')
        parts = [
            '\n'.join(lines[i:i + max_lines_per_section])
            for i in range(0, len(lines), max_lines_per_section)
        ]
    return [p.strip() for p in parts if p.strip()]




class QQConfig(ChannelConfig):
    """QQ 频道配置（OneBot v11）。"""

    ws_url: str = Field(default="ws://127.0.0.1:3001", description="OneBot WebSocket 地址")
    access_token: str = Field(default="", description="OneBot Access Token")
    require_mention: bool = Field(default=True, description="群聊中是否需要 @Bot 才触发")
    reply_to_mode: str = Field(default="first", description="回复引用策略 (first/all/off)")
    reconnect_interval: int = Field(default=5, description="重连间隔（秒）")


class OneBotV11Channel(BaseChannel[QQConfig]):
    """QQ 频道 — 通过 OneBot v11 协议通信（支持正向/反向 WS）。"""

    _entity_description = "QQ 频道（OneBot v11）"

    metadata = ChannelMetadata(
        name="QQ (OneBot v11)",
        description="基于 OneBot v11 协议的 QQ 频道（通过 NapCat/go-cqhttp 桥接）",
        version="2.0.0",
        author="AnelfAgent",
        tags=["qq", "onebot", "napcat"],
    )
    _Configs = QQConfig

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._listen_task: Optional[asyncio.Task[None]] = None
        self._pending_echoes: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._self_id: str = ""
        self._reverse_runner: Optional[web.AppRunner] = None
        super().__init__()

    channel_id = "qq"

    display_name = "QQ"

    capabilities: Set[ChannelCapability] = {
            ChannelCapability.SEND_TEXT,
            ChannelCapability.SEND_PHOTO,
            ChannelCapability.SEND_VOICE,
            ChannelCapability.SEND_FILE,
            ChannelCapability.DELETE_MESSAGE,
            ChannelCapability.FORWARD_MESSAGE,
            ChannelCapability.GET_CHAT_INFO,
            ChannelCapability.GET_CHAT_MEMBERS,
            ChannelCapability.BAN_USER,
            ChannelCapability.UNBAN_USER,
            ChannelCapability.SET_CHAT_TITLE,
            ChannelCapability.REPLY_TO,
            ChannelCapability.MESSAGE_REACTION,
        }

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()

        mode = self._cfg("ws_mode", "reverse")
        if mode == "reverse":
            await self._start_reverse_ws()
        else:
            self._status = ChannelStatus.RECONNECTING
            self._listen_task = asyncio.create_task(
                self._connect_loop(), name="qq_ws"
            )
            log(f"QQ: forward WS mode, connecting to {self._cfg('ws_url', '?')} ...")

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._ws:
            try:
                await self._ws.close()
            except (OSError, asyncio.CancelledError):
                pass
            self._ws = None
        if self._reverse_runner:
            try:
                await self._reverse_runner.cleanup()
            except (asyncio.CancelledError, Exception):
                pass
            self._reverse_runner = None
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except (asyncio.CancelledError, Exception):
                pass
            self._session = None

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """通过 OneBot v11 发送文本消息，解析 [at_uid:xxx] 并转换为 OneBot at 段。"""
        channel_type = kwargs.get("channel_type", "private")
        ob_message: list = []
        reply_to = kwargs.get("reply_to")
        if reply_to:
            ob_message.append({"type": "reply", "data": {"id": str(reply_to)}})

        # 解析文本中的 @ 格式并转换为 OneBot 消息段
        segments = self._parse_at_in_text(text, channel_type)
        ob_message.extend(segments)

        log(f"QQ 发送{'群' if channel_type == 'group' else '私聊'}消息: {chat_id}, text={text[:50]}", "DEBUG", tag="通道")
        ok = await self._send_to(chat_id, channel_type, ob_message)
        return _ok({"chat_id": chat_id}) if ok else _err("发送失败")

    def _parse_at_in_text(self, text: str, channel_type: str) -> List[Dict[str, Any]]:
        """解析 [at_uid:xxx] 标签，转换为 OneBot 消息段。"""
        segments: List[Dict[str, Any]] = []
        last_end = 0

        for match in _AT_PATTERN.finditer(text):
            if match.start() > last_end:
                prev = text[last_end:match.start()]
                if prev:
                    segments.append({"type": "text", "data": {"text": prev}})

            uid = match.group(1)
            if channel_type == "group" and uid != self._self_id:
                segments.append({"type": "at", "data": {"qq": uid}})
            elif channel_type != "group" and uid not in ("all", self._self_id):
                segments.append({"type": "text", "data": {"text": f"@{uid}"}})

            last_end = match.end()

        if last_end < len(text):
            remaining = text[last_end:]
            if remaining:
                segments.append({"type": "text", "data": {"text": remaining}})

        if not segments:
            segments.append({"type": "text", "data": {"text": text}})

        return segments

    @staticmethod
    def _resolve_local_file_path(path: str) -> str:
        """解析媒体路径：支持绝对路径、项目相对路径和 workspace 相对路径。"""
        raw = (path or "").strip()
        if not raw:
            return raw
        if raw.startswith(("http://", "https://", "base64://", "data:", "file://")):
            return raw

        expanded = os.path.expandvars(os.path.expanduser(raw))
        if os.path.isabs(expanded):
            return os.path.normpath(expanded)

        candidates = [os.path.normpath(expanded)]

        workspace_root = "workspace"
        try:
            from core.config import ConfigManager
            workspace_root = str(ConfigManager.get("workspace_root", "workspace") or "workspace")
        except Exception:
            pass

        ws_norm = os.path.normpath(workspace_root)
        norm_expanded = os.path.normpath(expanded)
        if norm_expanded.startswith(ws_norm + os.sep) or norm_expanded == ws_norm:
            candidates.append(norm_expanded)
        else:
            candidates.append(os.path.normpath(os.path.join(ws_norm, norm_expanded)))

        for cand in candidates:
            if os.path.isfile(cand):
                return os.path.abspath(cand)
        return os.path.abspath(candidates[-1])

    @staticmethod
    def _to_ob_file(path: str) -> str:
        """将本地文件路径转为 OneBot ``base64://`` 格式，URL 和已有 base64 格式原样返回。

        NapCat / QQ 运行在 macOS App Sandbox 内，无法读取外部路径（如 /tmp），
        转为 base64 可彻底绕过文件权限与沙箱限制。
        """
        if path.startswith(("http://", "https://", "base64://", "data:")):
            return path
        resolved = OneBotV11Channel._resolve_local_file_path(path)
        if os.path.isfile(resolved):
            with open(resolved, "rb") as f:
                return "base64://" + base64.b64encode(f.read()).decode()
        return path

    @staticmethod
    def _to_ob_upload_uri(path: str) -> str:
        """将文件路径转为 OneBot upload_*_file 可识别的 URI。"""
        if path.startswith(("http://", "https://", "file://", "base64://", "data:")):
            return path
        resolved = OneBotV11Channel._resolve_local_file_path(path)
        if os.path.isfile(resolved):
            return Path(resolved).as_uri()
        return path

    async def send_photo(self, chat_id: str, photo: str, caption: str = "", **kwargs: Any) -> str:
        channel_type = kwargs.get("channel_type", "private")
        file_value = self._to_ob_file(photo)
        ob_message: list = [{"type": "image", "data": {"file": file_value}}]
        if caption:
            ob_message.append({"type": "text", "data": {"text": caption}})
        reply_to = kwargs.get("reply_to")
        if reply_to:
            ob_message.insert(0, {"type": "reply", "data": {"id": str(reply_to)}})
        ok = await self._send_to(chat_id, channel_type, ob_message)
        return _ok({"chat_id": chat_id}) if ok else _err("发送图片失败")

    async def send_voice(self, chat_id: str, voice: str, caption: str = "", **kwargs: Any) -> str:
        channel_type = kwargs.get("channel_type", "private")
        file_value = self._to_ob_file(voice)
        ob_message = [{"type": "record", "data": {"file": file_value}}]
        ok = await self._send_to(chat_id, channel_type, ob_message)
        return _ok({"chat_id": chat_id}) if ok else _err("发送语音失败")

    async def send_file(self, chat_id: str, file_path: str, caption: str = "", **kwargs: Any) -> str:
        channel_type = kwargs.get("channel_type", "private")
        try:
            cid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: {chat_id}")
        file_value = self._to_ob_upload_uri(file_path)
        resolved = self._resolve_local_file_path(file_path)
        file_name = os.path.basename(resolved if os.path.isfile(resolved) else file_path) or "file"
        if caption:
            # OneBot upload_*_file 不支持独立 caption 字段，这里仅记录提示，文件名仍使用真实文件名。
            log("QQ send_file 暂不支持 caption，已忽略说明文字", "DEBUG", tag="通道")
        action = "upload_group_file" if channel_type == "group" else "upload_private_file"
        params: Dict[str, Any] = {
            "name": file_name,
            "file": file_value,
            ("group_id" if channel_type == "group" else "user_id"): cid,
        }
        result = await self._call_api_raw(action, params)
        if result and result.get("retcode") == 0:
            return _ok({"chat_id": chat_id})

        # NapCat 在 macOS App Sandbox 下可能无法直接读取外部本地路径（EPERM），
        # 回退到 base64:// 可绕过路径权限问题。
        message = ""
        wording = ""
        if result:
            message = str(result.get("message") or "")
            wording = str(result.get("wording") or "")
        if "EPERM" in f"{message} {wording}" and os.path.isfile(resolved):
            params["file"] = self._to_ob_file(resolved)
            log("QQ send_file 检测到 EPERM，回退 base64 上传", "WARNING", tag="通道")
            result = await self._call_api_raw(action, params)
            if result and result.get("retcode") == 0:
                return _ok({"chat_id": chat_id})

        if result:
            log(f"OneBot v11 API 失败: {action} -> {result}", "WARNING")
        return _err("发送文件失败")

    @channel_tool(description="撤回指定消息")
    async def delete_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        ok = await self._call_api("delete_msg", {"message_id": int(message_id)})
        return _ok() if ok else _err("撤回失败")

    @channel_tool(description="转发单条消息到指定会话")
    async def forward_msg(self, chat_id: str, from_chat_id: str, message_id: str, **kwargs: Any) -> str:
        channel_type = kwargs.get("channel_type", "private")
        try:
            mid = int(message_id)
            cid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: chat_id={chat_id}, message_id={message_id}")
        if channel_type == "group":
            ok = await self._call_api("forward_group_single_msg", {"message_id": mid, "group_id": cid})
        else:
            ok = await self._call_api("forward_friend_single_msg", {"message_id": mid, "user_id": cid})
        return _ok({"chat_id": chat_id}) if ok else _err("转发失败")

    @channel_tool(description="获取会话信息（群聊为群信息，私聊为用户信息）")
    async def get_chat_info(self, chat_id: str, **kwargs: Any) -> str:
        channel_type = kwargs.get("channel_type", "group")
        try:
            cid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: {chat_id}")
        if channel_type == "group":
            result = await self._call_api_data("get_group_info", {"group_id": cid})
        else:
            result = await self._call_api_data("get_stranger_info", {"user_id": cid})
        return json.dumps({"success": True, "data": result}, ensure_ascii=False) if result else _err("查询失败")

    @channel_tool(description="获取群成员列表")
    async def get_chat_members(self, chat_id: str, **kwargs: Any) -> str:
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        result = await self._call_api_data("get_group_member_list", {"group_id": gid})
        return json.dumps({"success": True, "data": result}, ensure_ascii=False) if result else _err("查询失败")

    @channel_tool(sensitive=True, description="禁言群成员（默认 30 分钟）")
    async def ban_user(self, chat_id: str, user_id: str, duration: int = 1800, **kwargs: Any) -> str:
        """禁言群成员。

        Args:
            chat_id: 群号
            user_id: 用户 QQ 号
            duration: 禁言时长（秒），默认 1800（30 分钟）
        """
        duration = int(duration)
        try:
            ok = await self._call_api("set_group_ban", {
                "group_id": int(chat_id), "user_id": int(user_id), "duration": duration,
            })
        except (ValueError, TypeError):
            return _err(f"无效的 ID: group={chat_id}, user={user_id}")
        return _ok() if ok else _err("禁言失败")

    @channel_tool(description="解除群成员禁言")
    async def unban_user(self, chat_id: str, user_id: str, **kwargs: Any) -> str:
        try:
            ok = await self._call_api("set_group_ban", {
                "group_id": int(chat_id), "user_id": int(user_id), "duration": 0,
            })
        except (ValueError, TypeError):
            return _err(f"无效的 ID: group={chat_id}, user={user_id}")
        return _ok() if ok else _err("解禁失败")

    @channel_tool(description="设置群名称")
    async def set_chat_title(self, chat_id: str, title: str, **kwargs: Any) -> str:
        try:
            ok = await self._call_api("set_group_name", {"group_id": int(chat_id), "group_name": title})
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        return _ok() if ok else _err("设置群名失败")

    @channel_tool()
    async def set_group_card(self, chat_id: str, user_id: str, card: str = "", **kwargs: Any) -> str:
        """设置群成员名片（群昵称）。card 为空则取消名片。"""
        try:
            gid, uid = int(chat_id), int(user_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: group={chat_id}, user={user_id}")
        ok = await self._call_api("set_group_card", {
            "group_id": gid, "user_id": uid, "card": card,
        })
        return _ok({"chat_id": chat_id, "user_id": user_id, "card": card}) if ok else _err("设置群名片失败")

    # ------------------------------------------------------------------
    # 用户信息查询（新增）
    # ------------------------------------------------------------------

    @channel_tool()
    async def get_stranger_info(self, user_id: str, **kwargs: Any) -> str:
        """获取陌生人信息（昵称、性别、年龄、QQ 等级）。"""
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return _err(f"无效的用户 ID: {user_id}")
        data = await self._call_api_data("get_stranger_info", {"user_id": uid})
        if data is None:
            return _err("获取用户信息失败")
        return _ok({
            "user_id": str(data.get("user_id", "")),
            "nickname": data.get("nickname", ""),
            "sex": data.get("sex", "unknown"),
            "age": data.get("age", 0),
            "level": data.get("level", 0),
        })

    @channel_tool()
    async def get_group_member_info(self, chat_id: str, user_id: str, **kwargs: Any) -> str:
        """获取群成员详细信息（群名片、角色、入群时间）。"""
        try:
            gid, uid = int(chat_id), int(user_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: group={chat_id}, user={user_id}")
        data = await self._call_api_data("get_group_member_info", {
            "group_id": gid, "user_id": uid,
        })
        if data is None:
            return _err("获取群成员信息失败")
        return _ok({
            "group_id": str(data.get("group_id", "")),
            "user_id": str(data.get("user_id", "")),
            "nickname": data.get("nickname", ""),
            "card": data.get("card", ""),
            "sex": data.get("sex", "unknown"),
            "age": data.get("age", 0),
            "join_time": data.get("join_time", 0),
            "role": data.get("role", "member"),
            "title": data.get("title", ""),
        })

    # ------------------------------------------------------------------
    # 群管理扩展（新增）
    # ------------------------------------------------------------------

    @channel_tool(sensitive=True)
    async def set_group_admin(self, chat_id: str, user_id: str, enable: bool = True, **kwargs: Any) -> str:
        """设置/取消群管理员。"""
        try:
            gid, uid = int(chat_id), int(user_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: group={chat_id}, user={user_id}")
        ok = await self._call_api("set_group_admin", {
            "group_id": gid, "user_id": uid, "enable": enable,
        })
        action = "设置" if enable else "取消"
        return _ok({"chat_id": chat_id, "user_id": user_id, "enable": enable}) if ok else _err(f"{action}管理员失败")

    @channel_tool(sensitive=True)
    async def set_group_whole_ban(self, chat_id: str, enable: bool = True, **kwargs: Any) -> str:
        """全员禁言。"""
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        ok = await self._call_api("set_group_whole_ban", {
            "group_id": gid, "enable": enable,
        })
        action = "开启" if enable else "关闭"
        return _ok({"chat_id": chat_id, "enable": enable}) if ok else _err(f"{action}全员禁言失败")

    # ------------------------------------------------------------------
    # 好友/群列表（新增）
    # ------------------------------------------------------------------

    @channel_tool()
    async def get_friend_list(self, **kwargs: Any) -> str:
        """获取好友列表。"""
        data = await self._call_api_data("get_friend_list", {})
        if data is None:
            return _err("获取好友列表失败")
        friends = [
            {
                "user_id": str(f.get("user_id", "")),
                "nickname": f.get("nickname", ""),
                "remark": f.get("remark", ""),
            }
            for f in data
        ]
        return _ok({"friends": friends, "count": len(friends)})

    @channel_tool()
    async def get_group_list(self, **kwargs: Any) -> str:
        """获取群列表。"""
        data = await self._call_api_data("get_group_list", {})
        if data is None:
            return _err("获取群列表失败")
        groups = [
            {
                "group_id": str(g.get("group_id", "")),
                "group_name": g.get("group_name", ""),
                "member_count": g.get("member_count", 0),
                "max_member_count": g.get("max_member_count", 0),
            }
            for g in data
        ]
        return _ok({"groups": groups, "count": len(groups)})

    # ------------------------------------------------------------------
    # 账号信息（新增）
    # ------------------------------------------------------------------

    @channel_tool()
    async def get_login_info(self, **kwargs: Any) -> str:
        """获取登录账号信息（Bot 自身）。"""
        data = await self._call_api_data("get_login_info", {})
        if data is None:
            return _err("获取登录信息失败")
        return _ok({
            "user_id": str(data.get("user_id", "")),
            "nickname": data.get("nickname", ""),
        })

    # ------------------------------------------------------------------
    # 消息操作扩展（新增）
    # ------------------------------------------------------------------

    @channel_tool()
    async def get_message(self, message_id: str, **kwargs: Any) -> str:
        """获取单条消息详情。"""
        try:
            mid = int(message_id)
        except (ValueError, TypeError):
            return _err(f"无效的消息 ID: {message_id}")
        data = await self._call_api_data("get_msg", {"message_id": mid})
        if data is None:
            return _err("获取消息失败")
        return _ok({
            "message_id": str(data.get("message_id", "")),
            "sender": data.get("sender", {}),
            "message": data.get("message", ""),
            "time": data.get("time", 0),
        })

    @channel_tool()
    async def get_forward_msg(self, forward_id: str, **kwargs: Any) -> str:
        """获取合并转发消息内容。"""
        data = await self._call_api_data("get_forward_msg", {"id": forward_id})
        if data is None:
            return _err("获取合并转发消息失败")
        messages = data.get("messages", [])
        return _ok({
            "messages": messages,
            "count": len(messages),
        })

    # ------------------------------------------------------------------
    # 申请管理（新增）
    # ------------------------------------------------------------------

    @channel_tool(sensitive=True)
    async def set_group_add_request(self, flag: str, approve: bool = True, reason: str = "", **kwargs: Any) -> str:
        """处理加群申请。

        Args:
            flag: 请求标识（从事件中获取）
            approve: 是否同意
            reason: 拒绝理由（approve=False 时有效）
        """
        ok = await self._call_api("set_group_add_request", {
            "flag": flag,
            "approve": approve,
            "reason": reason,
        })
        action = "同意" if approve else "拒绝"
        return _ok({"flag": flag, "approve": approve}) if ok else _err(f"{action}加群申请失败")

    @channel_tool(sensitive=True)
    async def set_friend_add_request(self, flag: str, approve: bool = True, remark: str = "", **kwargs: Any) -> str:
        """处理好友申请。

        Args:
            flag: 请求标识（从事件中获取）
            approve: 是否同意
            remark: 好友备注（approve=True 时有效）
        """
        ok = await self._call_api("set_friend_add_request", {
            "flag": flag,
            "approve": approve,
            "remark": remark,
        })
        action = "同意" if approve else "拒绝"
        return _ok({"flag": flag, "approve": approve}) if ok else _err(f"{action}好友申请失败")

    # ------------------------------------------------------------------
    # 消息记录（新增）
    # ------------------------------------------------------------------

    @channel_tool()
    async def get_group_msg_history(self, chat_id: str, count: int = 20, **kwargs: Any) -> str:
        """获取群消息历史记录。

        Args:
            chat_id: 群号
            count: 获取消息数量（最大 200）
        """
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        data = await self._call_api_data("get_group_msg_history", {
            "group_id": gid,
            "count": min(count, 200),
        })
        if data is None:
            return _err("获取群消息历史失败")
        messages = data.get("messages", [])
        return _ok({
            "messages": messages,
            "count": len(messages),
        })

    # ------------------------------------------------------------------
    # NapCat 扩展 API（新增）
    # ------------------------------------------------------------------

    @channel_tool(sensitive=True)
    async def set_group_kick(self, chat_id: str, user_id: str, reject_add_request: bool = False, **kwargs: Any) -> str:
        """踢出群成员。

        Args:
            chat_id: 群号
            user_id: 用户 QQ 号
            reject_add_request: 是否拒绝此人的加群申请
        """
        try:
            gid, uid = int(chat_id), int(user_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: group={chat_id}, user={user_id}")
        ok = await self._call_api("set_group_kick", {
            "group_id": gid,
            "user_id": uid,
            "reject_add_request": reject_add_request,
        })
        return _ok({"chat_id": chat_id, "user_id": user_id}) if ok else _err("踢出群成员失败")

    @channel_tool(sensitive=True)
    async def set_group_leave(self, chat_id: str, is_dismiss: bool = False, **kwargs: Any) -> str:
        """退出群聊。

        Args:
            chat_id: 群号
            is_dismiss: 是否解散群（仅群主可用）
        """
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        ok = await self._call_api("set_group_leave", {
            "group_id": gid,
            "is_dismiss": is_dismiss,
        })
        return _ok({"chat_id": chat_id}) if ok else _err("退出群聊失败")

    @channel_tool()
    async def get_friend_msg_history(self, user_id: str, count: int = 20, **kwargs: Any) -> str:
        """获取好友消息历史记录。

        Args:
            user_id: 好友 QQ 号
            count: 获取消息数量（最大 200）
        """
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return _err(f"无效的用户 ID: {user_id}")
        data = await self._call_api_data("get_friend_msg_history", {
            "user_id": uid,
            "count": min(count, 200),
        })
        if data is None:
            return _err("获取好友消息历史失败")
        messages = data.get("messages", [])
        return _ok({
            "messages": messages,
            "count": len(messages),
        })

    @channel_tool()
    async def get_group_system_msg(self, **kwargs: Any) -> str:
        """获取群系统消息（加群申请、被邀请入群等）。"""
        data = await self._call_api_data("get_group_system_msg", {})
        if data is None:
            return _err("获取群系统消息失败")
        return _ok({
            "invited_requests": data.get("invited_requests", []),
            "join_requests": data.get("join_requests", []),
        })

    @channel_tool()
    async def get_image(self, file_id: str, **kwargs: Any) -> str:
        """获取图片信息。

        Args:
            file_id: 图片文件 ID（从消息中获取）
        """
        data = await self._call_api_data("get_image", {"file": file_id})
        if data is None:
            return _err("获取图片信息失败")
        return _ok({
            "file": data.get("file", ""),
            "filename": data.get("filename", ""),
            "url": data.get("url", ""),
            "size": data.get("size", 0),
        })

    @channel_tool()
    async def get_record(self, file_id: str, out_format: str = "mp3", **kwargs: Any) -> str:
        """获取语音信息。

        Args:
            file_id: 语音文件 ID（从消息中获取）
            out_format: 输出格式（mp3/amr/wma/m4a/spx/ogg/wav/flac）
        """
        data = await self._call_api_data("get_record", {
            "file": file_id,
            "out_format": out_format,
        })
        if data is None:
            return _err("获取语音信息失败")
        return _ok({
            "file": data.get("file", ""),
            "url": data.get("url", ""),
        })

    @channel_tool()
    async def upload_group_file(self, chat_id: str, file_path: str, name: str = "", folder: str = "/", **kwargs: Any) -> str:
        """上传群文件。

        Args:
            chat_id: 群号
            file_path: 本地文件路径
            name: 文件名（为空则使用原文件名）
            folder: 上传到的文件夹路径（默认根目录）
        """
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")

        # 检查文件是否存在
        import os
        if not os.path.exists(file_path):
            return _err(f"文件不存在: {file_path}")

        # 读取文件内容并转为 base64
        import base64
        with open(file_path, "rb") as f:
            file_content = base64.b64encode(f.read()).decode()

        file_name = name or os.path.basename(file_path)
        ok = await self._call_api("upload_group_file", {
            "group_id": gid,
            "file": file_content,
            "name": file_name,
            "folder": folder,
        })
        return _ok({"chat_id": chat_id, "file_name": file_name}) if ok else _err("上传群文件失败")

    @channel_tool()
    async def get_group_file_url(self, chat_id: str, file_id: str, busid: int = 102, **kwargs: Any) -> str:
        """获取群文件下载链接。

        Args:
            chat_id: 群号
            file_id: 文件 ID
            busid: 文件类型（默认 102）
        """
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        data = await self._call_api_data("get_group_file_url", {
            "group_id": gid,
            "file_id": file_id,
            "busid": busid,
        })
        if data is None:
            return _err("获取群文件下载链接失败")
        return _ok({
            "url": data.get("url", ""),
        })

    @channel_tool()
    async def send_group_notice(self, chat_id: str, content: str, **kwargs: Any) -> str:
        """发送群公告。

        Args:
            chat_id: 群号
            content: 公告内容
        """
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        ok = await self._call_api("_send_group_notice", {
            "group_id": gid,
            "content": content,
        })
        return _ok({"chat_id": chat_id}) if ok else _err("发送群公告失败")

    @channel_tool()
    async def get_group_honor_info(self, chat_id: str, honor_type: str = "all", **kwargs: Any) -> str:
        """获取群荣誉信息（龙王、群聊之火等）。

        Args:
            chat_id: 群号
            honor_type: 荣誉类型（talkative/performer/legend/strong_newbie/emotion/all）
        """
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        data = await self._call_api_data("get_group_honor_info", {
            "group_id": gid,
            "type": honor_type,
        })
        if data is None:
            return _err("获取群荣誉信息失败")
        return _ok_raw(data)

    # ------------------------------------------------------------------
    # NapCat 扩展 API（新增）
    # ------------------------------------------------------------------

    @channel_tool()
    async def set_group_sign(self, chat_id: str, **kwargs: Any) -> str:
        """群签到。

        Args:
            chat_id: 群号
        """
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        ok = await self._call_api("set_group_sign", {
            "group_id": gid,
        })
        return _ok({"chat_id": chat_id}) if ok else _err("群签到失败")

    @channel_tool()
    async def get_ai_record(self, text: str, character: str = "", **kwargs: Any) -> str:
        """AI 文字转语音。

        Args:
            text: 要转换的文本
            character: AI 语音角色（为空则使用默认角色）
        """
        data = await self._call_api_data("get_ai_record", {
            "text": text,
            "character": character,
        })
        if data is None:
            return _err("AI 文字转语音失败")
        return _ok({
            "file": data.get("file", ""),
            "url": data.get("url", ""),
        })

    @channel_tool()
    async def get_ai_characters(self, **kwargs: Any) -> str:
        """获取 AI 语音角色列表。"""
        data = await self._call_api_data("get_ai_characters", {})
        if data is None:
            return _err("获取 AI 语音角色列表失败")
        if isinstance(data, dict):
            data = data.get("characters", [])
        return _ok({
            "characters": data,
        })

    @channel_tool()
    async def send_group_ai_record(self, chat_id: str, text: str, character: str = "", **kwargs: Any) -> str:
        """群聊发送 AI 语音。

        Args:
            chat_id: 群号
            text: 要转换的文本
            character: AI 语音角色（为空则使用默认角色）
        """
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        ok = await self._call_api("send_group_ai_record", {
            "group_id": gid,
            "text": text,
            "character": character,
        })
        return _ok({"chat_id": chat_id}) if ok else _err("发送 AI 语音失败")

    @channel_tool()
    async def get_friends_with_category(self, **kwargs: Any) -> str:
        """获取分类的好友列表。"""
        data = await self._call_api_data("get_friends_with_category", {})
        if data is None:
            return _err("获取分类好友列表失败")
        return _ok_raw(data)

    @channel_tool(sensitive=True)
    async def set_qq_avatar(self, file_path: str, **kwargs: Any) -> str:
        """设置 QQ 头像。

        Args:
            file_path: 头像文件路径
        """
        import os
        if not os.path.exists(file_path):
            return _err(f"文件不存在: {file_path}")

        import base64
        with open(file_path, "rb") as f:
            file_content = base64.b64encode(f.read()).decode()

        ok = await self._call_api("set_qq_avatar", {
            "file": file_content,
        })
        return _ok({}) if ok else _err("设置 QQ 头像失败")

    async def forward_friend_single_msg(self, user_id: str, message_id: str, **kwargs: Any) -> str:
        """转发单条消息到私聊。

        Args:
            user_id: 目标用户 QQ 号
            message_id: 要转发的消息 ID
        """
        try:
            uid = int(user_id)
            mid = int(message_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: user={user_id}, message={message_id}")
        ok = await self._call_api("forward_friend_single_msg", {
            "user_id": uid,
            "message_id": mid,
        })
        return _ok({"user_id": user_id, "message_id": message_id}) if ok else _err("转发消息失败")

    async def forward_group_single_msg(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """转发单条消息到群聊。

        Args:
            chat_id: 目标群号
            message_id: 要转发的消息 ID
        """
        try:
            gid = int(chat_id)
            mid = int(message_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: group={chat_id}, message={message_id}")
        ok = await self._call_api("forward_group_single_msg", {
            "group_id": gid,
            "message_id": mid,
        })
        return _ok({"chat_id": chat_id, "message_id": message_id}) if ok else _err("转发消息失败")

    @channel_tool()
    async def translate_en2zh(self, text: str, **kwargs: Any) -> str:
        """英译中。

        Args:
            text: 要翻译的英文文本
        """
        data = await self._call_api_data("translate_en2zh", {
            "text": text,
        })
        if data is None:
            return _err("翻译失败")
        return _ok_raw(data)

    @channel_tool()
    async def mark_private_msg_as_read(self, user_id: str, **kwargs: Any) -> str:
        """设置私聊消息已读。

        Args:
            user_id: 用户 QQ 号
        """
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return _err(f"无效的用户 ID: {user_id}")
        ok = await self._call_api("mark_private_msg_as_read", {
            "user_id": uid,
        })
        return _ok({"user_id": user_id}) if ok else _err("设置已读失败")

    @channel_tool()
    async def mark_group_msg_as_read(self, chat_id: str, **kwargs: Any) -> str:
        """设置群聊消息已读。

        Args:
            chat_id: 群号
        """
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        ok = await self._call_api("mark_group_msg_as_read", {
            "group_id": gid,
        })
        return _ok({"chat_id": chat_id}) if ok else _err("设置已读失败")

    @channel_tool(sensitive=True)
    async def set_self_longnick(self, longnick: str, **kwargs: Any) -> str:
        """设置签名。

        Args:
            longnick: 签名内容
        """
        ok = await self._call_api("set_self_longnick", {
            "longnick": longnick,
        })
        return _ok({}) if ok else _err("设置签名失败")

    @channel_tool()
    async def get_recent_contact(self, **kwargs: Any) -> str:
        """获取最近联系人列表。"""
        data = await self._call_api_data("get_recent_contact", {})
        if data is None:
            return _err("获取最近联系人失败")
        return _ok_raw(data)

    @channel_tool()
    async def send_forward_msg(self, chat_id: str, content: str, **kwargs: Any) -> str:
        """将长文本以合并转发消息形式发送，自动按段落拆分。"""
        channel_type = kwargs.get("channel_type")
        if not channel_type:
            from agent.channel.manager import get_channel_manager
            channel_type = get_channel_manager().resolve_channel_type(self.channel_id, chat_id)

        try:
            cid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: {chat_id}")

        sections = _split_forward_sections(content)
        if not sections:
            return _err("消息内容为空，无法发送合并转发")

        bot_name = "Bot"
        nodes = [
            {
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": self._self_id or "0",
                    "content": [{"type": "text", "data": {"text": sec}}],
                },
            }
            for sec in sections
        ]

        if channel_type == "group":
            ok = await self._call_api("send_group_forward_msg", {
                "group_id": cid, "messages": nodes,
            })
        else:
            ok = await self._call_api("send_private_forward_msg", {
                "user_id": cid, "messages": nodes,
            })
        return _ok({"chat_id": chat_id, "sections": len(sections)}) if ok else _err("发送合并转发失败")

    # ------------------------------------------------------------------
    # NapCat 扩展 API（剩余功能）
    # ------------------------------------------------------------------

    @channel_tool()
    async def get_file(self, file_id: str, **kwargs: Any) -> str:
        """获取文件信息。

        Args:
            file_id: 文件 ID
        """
        data = await self._call_api_data("get_file", {"file_id": file_id})
        if data is None:
            return _err("获取文件信息失败")
        return _ok({
            "file": data.get("file", ""),
            "url": data.get("url", ""),
            "size": data.get("size", 0),
        })

    @channel_tool()
    async def create_collection(self, title: str, content: str, **kwargs: Any) -> str:
        """创建收藏。

        Args:
            title: 收藏标题
            content: 收藏内容
        """
        ok = await self._call_api("create_collection", {
            "title": title,
            "content": content,
        })
        return _ok({}) if ok else _err("创建收藏失败")

    @channel_tool()
    async def get_collection_list(self, **kwargs: Any) -> str:
        """获取收藏列表。"""
        data = await self._call_api_data("get_collection_list", {})
        if data is None:
            return _err("获取收藏列表失败")
        if isinstance(data, dict):
            data = data.get("collections", [])
        return _ok({
            "collections": data,
        })

    @channel_tool()
    async def mark_all_as_read(self, **kwargs: Any) -> str:
        """标记所有消息已读。"""
        ok = await self._call_api("_mark_all_as_read", {})
        return _ok({}) if ok else _err("标记所有已读失败")

    @channel_tool()
    async def get_profile_like(self, **kwargs: Any) -> str:
        """获取自身点赞列表。"""
        data = await self._call_api_data("get_profile_like", {})
        if data is None:
            return _err("获取点赞列表失败")
        return _ok_raw(data)

    @channel_tool()
    async def fetch_custom_face(self, count: int = 10, **kwargs: Any) -> str:
        """获取自定义表情。

        Args:
            count: 获取数量
        """
        data = await self._call_api_data("fetch_custom_face", {"count": count})
        if data is None:
            return _err("获取自定义表情失败")
        if isinstance(data, dict):
            data = data.get("faces", [])
        return _ok({
            "faces": data,
        })

    @channel_tool(sensitive=True)
    async def set_online_status(self, status: str, **kwargs: Any) -> str:
        """设置在线状态。

        Args:
            status: 在线状态（online/away/busy/invisible/offline）
        """
        ok = await self._call_api("set_online_status", {
            "status": status,
        })
        return _ok({}) if ok else _err("设置在线状态失败")

    @channel_tool()
    async def get_robot_uin_range(self, **kwargs: Any) -> str:
        """获取机器人账号范围。"""
        data = await self._call_api_data("get_robot_uin_range", {})
        if data is None:
            return _err("获取机器人账号范围失败")
        return _ok_raw(data)

    @channel_tool(name="ark_share_peer")
    async def ArkSharePeer(self, user_id: str, **kwargs: Any) -> str:
        """获取推荐好友/群聊卡片。

        Args:
            user_id: 用户 QQ 号
        """
        data = await self._call_api_data("ArkSharePeer", {"user_id": user_id})
        if data is None:
            return _err("获取推荐卡片失败")
        return _ok_raw(data)

    @channel_tool(name="ark_share_group")
    async def ArkShareGroup(self, chat_id: str, **kwargs: Any) -> str:
        """获取推荐群聊卡片。

        Args:
            chat_id: 群号
        """
        data = await self._call_api_data("ArkShareGroup", {"group_id": chat_id})
        if data is None:
            return _err("获取推荐群聊卡片失败")
        return _ok_raw(data)

    @channel_tool()
    async def send_poke(self, chat_id: str, user_id: str, **kwargs: Any) -> str:
        """向指定用户发送戳一戳互动。群聊中 chat_id 为群号，私聊中 chat_id 与 user_id 相同。"""
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return _err(f"无效的用户 ID: {user_id}")

        # 推断群聊/私聊：chat_id != user_id 视为群聊，也可通过 kwargs 显式指定
        channel_type = kwargs.get("channel_type")
        if not channel_type:
            channel_type = "group" if chat_id != user_id else "private"

        try:
            if channel_type == "group":
                try:
                    gid = int(chat_id)
                except (ValueError, TypeError):
                    return _err(f"无效的群 ID: {chat_id}")
                ok = await self._call_api("group_poke", {"group_id": gid, "user_id": uid})
            else:
                ok = await self._call_api("friend_poke", {"user_id": uid})
            return _ok({"chat_id": chat_id, "user_id": user_id}) if ok else _err("戳一戳失败")
        except Exception as exc:
            # 捕获 NapCat 版本不兼容错误
            error_msg = str(exc)
            if "packetBackend" in error_msg or "不支持当前QQ版本" in error_msg:
                log(f"戳一戳功能不可用（NapCat 版本不兼容）: {error_msg}", "WARNING")
                return _err("戳一戳功能当前不可用（NapCat 版本不兼容，请检查 QQ 版本或升级 NapCat）")
            return _err(f"戳一戳失败: {error_msg}")

    @channel_tool()
    async def message_reaction(self, chat_id: str, message_id: str, emoji_id: str = "212", **kwargs: Any) -> str:
        """对指定消息添加表情回应（NapCat 扩展 API）。"""
        try:
            mid = int(message_id)
        except (ValueError, TypeError):
            return _err(f"无效的消息 ID: {message_id}")
        ok = await self._call_api("set_msg_emoji_like", {
            "message_id": mid, "emoji_id": str(emoji_id),
        })
        return _ok({"message_id": message_id, "emoji_id": emoji_id}) if ok else _err("表情回应失败")

    # ------------------------------------------------------------------
    # 发送辅助
    # ------------------------------------------------------------------

    def is_known_group(self, target_id: str) -> bool:
        """判断 target_id 是否为已知群组（来自白名单配置）。

        用于重启后、尚未收到群消息时的主动发送路由判断。
        """
        raw_groups: str = self._cfg("group_whitelist", "")
        group_wl = {g.strip() for g in raw_groups.split(",") if g.strip()}
        return target_id in group_wl

    async def _send_to(self, chat_id: str, channel_type: str, ob_message: list) -> bool:
        """根据 channel_type 发送到群或私聊。"""
        try:
            cid = int(chat_id)
        except (ValueError, TypeError):
            return False
        if channel_type == "group":
            return await self._send_group_msg(cid, ob_message)
        return await self._send_private_msg(cid, ob_message)

    def get_status_info(self) -> Dict[str, Any]:
        info = super().get_status_info()
        mode = self._cfg("ws_mode", "reverse")
        info["ws_mode"] = mode
        info["ws_connected"] = self._ws is not None and not getattr(self._ws, "closed", True)
        if mode == "reverse":
            info["listen_port"] = int(self._cfg("reverse_ws_port", 8095))
            info["listen_path"] = "/onebot/v11/ws"
            info["detail"] = (
                f"listening on :{info['listen_port']}/onebot/v11/ws"
                + (", client connected" if info["ws_connected"] else ", waiting for client")
            )
        else:
            info["ws_url"] = self._cfg("ws_url", "")
            info["detail"] = (
                f"connected to {info['ws_url']}" if info["ws_connected"]
                else f"connecting to {info['ws_url']}"
            )
        if self._self_id:
            info["self_id"] = self._self_id
        return info

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def _cfg(self, key: str, default: Any = None) -> Any:
        """读取配置（使用 self.config）。"""
        return getattr(self.config, key, default)

    # ------------------------------------------------------------------
    # 反向 WebSocket（OneBot 端连我们）
    # ------------------------------------------------------------------

    async def _start_reverse_ws(self) -> None:
        """启动反向 WS Server，等待 OneBot 端连接。"""
        port = int(self._cfg("reverse_ws_port", 8095))
        token = self._cfg("access_token", "")

        app = web.Application()
        app.router.add_get("/onebot/v11/ws", self._reverse_ws_handler)
        app.router.add_get("/onebot/v11/ws/", self._reverse_ws_handler)

        self._reverse_runner = web.AppRunner(app)
        await self._reverse_runner.setup()
        site = web.TCPSite(self._reverse_runner, "0.0.0.0", port)
        await site.start()
        self._status = ChannelStatus.RUNNING
        log(f"QQ: 反向 WS Server 已启动，等待连接 ws://0.0.0.0:{port}/onebot/v11/ws")

    async def _reverse_ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        """处理 OneBot 端的反向 WS 连接。"""
        token = self._cfg("access_token", "")
        if token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {token}" and request.query.get("access_token") != token:
                return web.Response(status=403, text="Forbidden")

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        if self._ws:
            try:
                await self._ws.close()
            except (OSError, asyncio.CancelledError):
                pass
        self._ws = ws
        log("QQ: 客户端已连接（反向 WS）")

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    await self._handle_ws_data(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            if self._ws is ws:
                self._ws = None
            log("QQ: 客户端断开连接（反向 WS）")

        return ws

    # ------------------------------------------------------------------
    # 正向 WebSocket（我们连 OneBot 端）
    # ------------------------------------------------------------------

    async def _connect_loop(self) -> None:
        """持续尝试连接 WebSocket，断线后自动重连。"""
        reconnect_interval: int = self._cfg("reconnect_interval", 5)
        max_attempts: int = self._cfg("max_reconnect_attempts", 0)
        attempt = 0

        while self._status != ChannelStatus.STOPPED:
            ws_url: str = self._cfg("ws_url", "ws://127.0.0.1:3001")
            access_token: str = self._cfg("access_token", "")

            headers: Dict[str, str] = {}
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"

            try:
                log(f"QQ: 正在连接 {ws_url} ...")
                assert self._session is not None
                self._ws = await self._session.ws_connect(
                    ws_url, headers=headers, heartbeat=30.0
                )
                self._status = ChannelStatus.RUNNING
                attempt = 0
                log(f"QQ: WebSocket 已连接 ({ws_url})")

                await self._listen()

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"QQ: 连接异常 -> {exc}", "WARNING")

            if self._status == ChannelStatus.STOPPED:
                break

            attempt += 1
            if max_attempts > 0 and attempt >= max_attempts:
                log("QQ: 达到最大重连次数，停止重连", "ERROR")
                self._status = ChannelStatus.ERROR
                break

            self._status = ChannelStatus.RECONNECTING
            log(f"QQ: {reconnect_interval}s 后重连 (第 {attempt} 次) ...")
            await asyncio.sleep(reconnect_interval)

    async def _listen(self) -> None:
        """监听 WebSocket 消息。"""
        assert self._ws is not None
        async for ws_msg in self._ws:
            if ws_msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(ws_msg.data)
                except json.JSONDecodeError:
                    continue
                await self._handle_ws_data(data)
            elif ws_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _handle_ws_data(self, data: Dict[str, Any]) -> None:
        """处理单条 WebSocket 数据。"""
        echo = data.get("echo")
        if echo and echo in self._pending_echoes:
            fut = self._pending_echoes.pop(echo)
            if not fut.done():
                fut.set_result(data)
            return

        self_id = data.get("self_id")
        if self_id:
            self._self_id = str(self_id)

        if not self._check_whitelist(data):
            log(f"QQ 白名单拦截: group={data.get('group_id')} user={data.get('user_id')} "
                f"的消息已被丢弃（不在白名单内）", "WARNING", tag="通道")
            return

        # 使用异步解析，支持获取引用消息内容、群成员昵称和合并转发
        message = await parse_event_async(data, self._api_caller)
        if message:
            # require_mention: 群聊中非 @ 消息仍记录到历史，但不触发思考
            if (
                self._cfg("require_mention", False)
                and not message.is_to_me
                and message.channel.channel_type == ChannelType.GROUP
            ):
                message.trigger_mind = False
            await self.on_message(message)

    async def _api_caller(self, action: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """API 回调函数，供 parser 调用获取消息、成员信息等。"""
        return await self._call_api_raw(action, params)

    def _check_whitelist(self, data: Dict[str, Any]) -> bool:
        """检查事件来源是否在白名单中。message 和 notice 事件都受白名单控制。"""
        post_type = data.get("post_type")
        if post_type not in ("message", "notice"):
            return True

        enabled = self._cfg("whitelist_enabled", False)
        if not enabled:
            return True

        raw_groups: str = self._cfg("group_whitelist", "")
        raw_users: str = self._cfg("user_whitelist", "")
        group_wl = {g.strip() for g in raw_groups.split(",") if g.strip()}
        user_wl = {u.strip() for u in raw_users.split(",") if u.strip()}

        if not group_wl and not user_wl:
            return True

        group_id = data.get("group_id")
        if group_id is not None:
            return str(group_id) in group_wl

        user_id = data.get("user_id")
        if user_id is not None:
            return str(user_id) in user_wl

        return True

    # ------------------------------------------------------------------
    # 发送消息
    # ------------------------------------------------------------------

    async def _send_group_msg(self, group_id: int, message: Any) -> bool:
        return await self._call_api("send_group_msg", {
            "group_id": group_id,
            "message": message,
        })

    async def _send_private_msg(self, user_id: int, message: Any) -> bool:
        return await self._call_api("send_msg", {
            "message_type": "private",
            "user_id": user_id,
            "message": message,
        })

    async def _call_api_data(self, action: str, params: Dict[str, Any]) -> Optional[Any]:
        """调用 API 并返回 data 字段，失败返回 None。"""
        result = await self._call_api_raw(action, params)
        if result and result.get("retcode") == 0:
            return result.get("data")
        return None

    async def _call_api_raw(self, action: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """调用 API 并返回完整响应（HTTP 优先，降级 WS）。"""
        http_url = self._cfg("http_api_url", "")
        if http_url:
            return await self._call_api_http_raw(http_url, action, params)
        return await self._call_api_ws_raw(action, params)

    async def _call_api(self, action: str, params: Dict[str, Any]) -> bool:
        """调用 OneBot v11 API（优先 HTTP，降级到 WS），返回成功与否。"""
        result = await self._call_api_raw(action, params)
        if result and result.get("retcode") == 0:
            return True
        if result:
            log(f"OneBot v11 API 失败: {action} -> {result}", "WARNING")
        return False

    async def _call_api_http_raw(
        self, base_url: str, action: str, params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """通过 HTTP POST 调用 OneBot API，返回完整响应体。"""
        url = f"{base_url.rstrip('/')}/{action}"
        access_token = self._cfg("access_token", "")
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            assert self._session is not None
            async with self._session.post(url, json=params, headers=headers) as resp:
                return await resp.json()
        except Exception as exc:
            log(f"OneBot v11 HTTP API 异常: {action} -> {exc}", "ERROR")
            return None

    async def _call_api_ws_raw(self, action: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """通过 WebSocket 调用 OneBot API，返回完整响应体。"""
        if not self._ws or self._ws.closed:
            log("QQ: WebSocket 未连接，无法发送", "WARNING")
            return None

        echo = uuid.uuid4().hex[:12]
        payload = {"action": action, "params": params, "echo": echo}
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Dict[str, Any]] = loop.create_future()
        self._pending_echoes[echo] = fut

        try:
            await self._ws.send_json(payload)
            return await asyncio.wait_for(fut, timeout=15.0)
        except asyncio.TimeoutError:
            self._pending_echoes.pop(echo, None)
            log(f"OneBot v11 WS API 超时: {action}", "WARNING")
            return None
        except Exception as exc:
            self._pending_echoes.pop(echo, None)
            log(f"OneBot v11 WS API 异常: {action} -> {exc}", "ERROR")
            return None


    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """统一发送入口（协议）。"""
        try:
            chat_id = request.channel.channel_id
            message_ids: list[str] = []
            for seg in request.segments:
                seg_type = seg.type.value
                if seg_type == "text":
                    result_json = await self.send_text(chat_id, seg.content, reply_to=request.reply_to)
                    result = json.loads(result_json)
                    if result.get("success") and result.get("message_id"):
                        message_ids.append(result["message_id"])
                elif seg_type == "image":
                    result_json = await self.send_photo(chat_id, seg.file_path, caption=seg.caption)
                    result = json.loads(result_json)
                    if result.get("success") and result.get("message_id"):
                        message_ids.append(result["message_id"])
            if message_ids:
                return SendResponse(success=True, message_id=message_ids[0], message_ids=message_ids)
            return SendResponse(success=True, message_id="empty")
        except Exception as exc:
            return SendResponse(success=False, error=str(exc))

    async def get_self_info(self) -> ChannelUser:
        if not hasattr(self, "_self_id") or not self._self_id:
            raise RuntimeError("QQ 频道未初始化")
        return ChannelUser(
            platform=self.channel_id,
            user_id=str(self._self_id),
            user_name=getattr(self, "_self_nickname", "") or "QQ Bot",
            role=ChannelUserRole.MEMBER,
            is_bot=True,
        )

    async def get_user_info(self, user_id: str, channel_id: str) -> ChannelUser:
        return ChannelUser(
            platform=self.channel_id,
            user_id=user_id,
            user_name=user_id,
        )

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        chat_type = ChannelType.GROUP if len(channel_id) > 6 and channel_id.isdigit() else ChannelType.PRIVATE
        return ChannelInfo(
            channel_id=channel_id,
            channel_name=channel_id,
            channel_type=chat_type,
        )

    async def health_check(self) -> HealthStatus:
        if not hasattr(self, "_ws") or self._ws is None:
            return HealthStatus(healthy=False, detail="WebSocket not connected", last_error="no_ws")
        try:
            started = time.time()
            return HealthStatus(
                healthy=True,
                detail=f"QQ OK (self_id={getattr(self, '_self_id', 'unknown')})",
                latency_ms=(time.time() - started) * 1000,
                last_success_at=time.time(),
            )
        except Exception as exc:
            return HealthStatus(healthy=False, detail=str(exc), last_error=str(exc))

    async def render_approval_prompt(self, ctx) -> SendRequest:
        """渲染批准提示（QQ 关键词回复）。"""
        from agent.channel.base import ApprovalPromptRenderContext
        from agent.channel.schemas import AdapterChannel, ChannelType, SendSegment

        text = (
            f"⚠️ 工具调用需要批准\n"
            f"工具: {ctx.tool_name}\n"
            f"参数: {ctx.tool_args_summary[:200]}\n"
            f"风险: {ctx.risk_level}\n"
            f"原因: {ctx.reason}\n"
            f"超时: {ctx.timeout_seconds:.0f}s\n"
            f"\n"
            f"回复以下命令之一：\n"
            f"  approve {ctx.request_id}\n"
            f"  deny {ctx.request_id}"
        )

        return SendRequest(
            adapter_key=self.channel_id,
            channel=AdapterChannel(
                channel_id="",  # 由 approval/gate.py 填充
                channel_type=ChannelType.PRIVATE,
            ),
            segments=[SendSegment(type="text", content=text)],
        )
