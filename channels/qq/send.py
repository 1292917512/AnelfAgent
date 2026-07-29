"""QQ 发送路径 — 出站消息构造（文本/图片/语音/文件）与媒体格式转换。"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.channel.channel_types import _err, _ok
from core.log import log

if TYPE_CHECKING:
    from .adapter import OneBotV11Channel


_AT_PATTERN = re.compile(r'\[at_uid:([^\]]+)\]')
_SECTION_SPLIT_RE = re.compile(r'={3,}')

_MAX_FILE_BASE64_BYTES = 100 * 1024 * 1024  # 出站文件 base64 上限，防止大文件打爆内存


def _read_file_base64(path: str) -> str:
    """读取文件并 base64 编码（同步实现，供 to_thread 调用）。"""
    size = os.path.getsize(path)
    if size > _MAX_FILE_BASE64_BYTES:
        raise ValueError(f"文件过大（{size / 1024 / 1024:.1f}MB），超过出站发送上限 100MB: {path}")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


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


class QQSender:
    """QQ 出站发送器：构造 OneBot 消息段并调用频道 API 完成发送。"""

    def __init__(self, channel: "OneBotV11Channel") -> None:
        self._ch = channel

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
        data = await self._send_to(chat_id, channel_type, ob_message)
        return self._send_result(chat_id, data, "发送失败")

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
            if channel_type == "group" and uid != self._ch._self_id:
                segments.append({"type": "at", "data": {"qq": uid}})
            elif channel_type != "group" and uid not in ("all", self._ch._self_id):
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
            log("_resolve_local_file_path 异常已忽略", "DEBUG")

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
    async def _to_ob_file(path: str) -> str:
        """将本地文件路径转为 OneBot ``base64://`` 格式，URL 和已有 base64 格式原样返回。

        NapCat / QQ 运行在 macOS App Sandbox 内，无法读取外部路径（如 /tmp），
        转为 base64 可彻底绕过文件权限与沙箱限制。
        大文件读取 + base64 编码移入线程，避免阻塞事件循环。
        """
        if path.startswith(("http://", "https://", "base64://", "data:")):
            return path
        resolved = QQSender._resolve_local_file_path(path)
        if os.path.isfile(resolved):
            return "base64://" + await asyncio.to_thread(_read_file_base64, resolved)
        return path

    @staticmethod
    def _to_ob_upload_uri(path: str) -> str:
        """将文件路径转为 OneBot upload_*_file 可识别的 URI。"""
        if path.startswith(("http://", "https://", "file://", "base64://", "data:")):
            return path
        resolved = QQSender._resolve_local_file_path(path)
        if os.path.isfile(resolved):
            return Path(resolved).as_uri()
        return path

    async def send_photo(self, chat_id: str, photo: str, caption: str = "", **kwargs: Any) -> str:
        """发送图片消息（本地文件自动转 base64）。"""
        channel_type = kwargs.get("channel_type", "private")
        file_value = await self._to_ob_file(photo)
        ob_message: list = [{"type": "image", "data": {"file": file_value}}]
        if caption:
            ob_message.append({"type": "text", "data": {"text": caption}})
        reply_to = kwargs.get("reply_to")
        if reply_to:
            ob_message.insert(0, {"type": "reply", "data": {"id": str(reply_to)}})
        data = await self._send_to(chat_id, channel_type, ob_message)
        return self._send_result(chat_id, data, "发送图片失败")

    async def send_voice(self, chat_id: str, voice: str, caption: str = "", **kwargs: Any) -> str:
        """发送语音消息（本地文件自动转 base64）。"""
        channel_type = kwargs.get("channel_type", "private")
        file_value = await self._to_ob_file(voice)
        ob_message = [{"type": "record", "data": {"file": file_value}}]
        data = await self._send_to(chat_id, channel_type, ob_message)
        return self._send_result(chat_id, data, "发送语音失败")

    async def send_file(self, chat_id: str, file_path: str, caption: str = "", **kwargs: Any) -> str:
        """上传文件到群或私聊，沙盒 EPERM 时回退 base64 上传。"""
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
        result = await self._ch._call_api_raw(action, params)
        if result and result.get("retcode") == 0:
            return self._send_result(chat_id, result.get("data") or {}, "发送文件失败")

        # NapCat 在 macOS App Sandbox 下可能无法直接读取外部本地路径（EPERM），
        # 回退到 base64:// 可绕过路径权限问题。
        message = ""
        wording = ""
        if result:
            message = str(result.get("message") or "")
            wording = str(result.get("wording") or "")
        if "EPERM" in f"{message} {wording}" and os.path.isfile(resolved):
            params["file"] = await self._to_ob_file(resolved)
            log("QQ send_file 检测到 EPERM，回退 base64 上传", "WARNING", tag="通道")
            result = await self._ch._call_api_raw(action, params)
            if result and result.get("retcode") == 0:
                return self._send_result(chat_id, result.get("data") or {}, "发送文件失败")

        if result:
            log(f"OneBot v11 API 失败: {action} -> {result}", "WARNING")
        return _err("发送文件失败")

    async def _send_to(self, chat_id: str, channel_type: str, ob_message: list) -> Optional[Any]:
        """根据 channel_type 发送到群或私聊，返回 OneBot 响应 data（含 message_id），失败返回 None。"""
        try:
            cid = int(chat_id)
        except (ValueError, TypeError):
            return None
        if channel_type == "group":
            return await self._send_group_msg(cid, ob_message)
        return await self._send_private_msg(cid, ob_message)

    async def _send_group_msg(self, group_id: int, message: Any) -> Optional[Any]:
        return await self._ch._call_api_data("send_group_msg", {
            "group_id": group_id,
            "message": message,
        })

    async def _send_private_msg(self, user_id: int, message: Any) -> Optional[Any]:
        return await self._ch._call_api_data("send_msg", {
            "message_type": "private",
            "user_id": user_id,
            "message": message,
        })

    @staticmethod
    def _send_result(chat_id: str, data: Optional[Any], err_msg: str) -> str:
        """构造发送结果：成功时透传 OneBot 返回的 message_id（供后续引用/撤回/表情回应）。"""
        if data is None:
            return _err(err_msg)
        payload: Dict[str, Any] = {"chat_id": chat_id}
        if isinstance(data, dict) and data.get("message_id") is not None:
            payload["message_id"] = str(data["message_id"])
        return _ok(payload)
