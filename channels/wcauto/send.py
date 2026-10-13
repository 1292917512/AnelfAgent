"""wcauto 出站发送 — 文本分块/@转换/媒体 base64 化，经桥 RPC 落地。

与 QQ 发送器的差异：微信侧无消息段概念，@ 只能走「@成员弹层 + 正文」
一次成形（at_member），多 @ 时首个走真 @，其余降级为文本「@昵称」。
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from typing import TYPE_CHECKING, Any, Dict, List

from agent.channel.channel_types import _err, _ok
from channels._shared.text import split_text
from core.log import log

from .bridge.wcauto_bridge import protocol
from .transport import WcautoRpcError

if TYPE_CHECKING:
    from .adapter import WcautoChannel

_AT_PATTERN = re.compile(r"\[at_uid:([^\]]+)\]")

_MAX_OUTBOUND_BYTES = 100 * 1024 * 1024  # 出站文件上限（桥侧同样兜底）


def _read_file_base64(path: str) -> str:
    size = os.path.getsize(path)
    if size > _MAX_OUTBOUND_BYTES:
        raise ValueError(f"文件过大（{size / 1024 / 1024:.1f}MB），超过出站上限 100MB: {path}")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


class WcautoSender:
    """wcauto 出站发送器（注入频道引用，经桥 RPC 发送）。"""

    def __init__(self, channel: "WcautoChannel") -> None:
        self._ch = channel

    # ------------------------------------------------------------------
    # 文本
    # ------------------------------------------------------------------

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """发送文本：长文本分块（换行边界），群内 [at_uid:] 转真 @。"""
        channel_type = kwargs.get("channel_type", "private")
        max_len = int(self._ch._cfg("text_chunk_length", 1500))
        verify = bool(self._ch._cfg("send_verify", True))
        if not text.strip():
            return _err("发送内容为空")
        chunks = split_text(text, max_len, max_chunks=5)
        if not chunks:
            return _err("发送内容为空")

        at_members, chunks = self._extract_ats(chunks, channel_type)
        last_id = ""
        for i, chunk in enumerate(chunks):
            # 真 @ 只附加在首块（at_member 一次成形），其余块纯文本
            if i == 0 and at_members:
                data = await self._rpc(protocol.RPC_SEND_AT, {
                    "chat": chat_id, "member": at_members[0],
                    "text": chunk, "verify": verify})
                for extra in at_members[1:]:
                    log(f"wcauto: 多 @ 降级为纯文本: @{extra}", "DEBUG", tag="通道")
            else:
                data = await self._rpc(protocol.RPC_SEND_MSG, {
                    "chat": chat_id, "text": chunk, "verify": verify})
            if data is None:
                return _err("发送失败" if i == 0 else f"第 {i + 1} 段发送失败（前 {i} 段已发）")
            last_id = str((data.get("data") or {}).get("local_id") or "") if isinstance(data, dict) else ""
            if i + 1 < len(chunks):
                await asyncio.sleep(1.0)  # 分段间隔，避免撞桥侧拟人节流
        return _ok({"chat_id": chat_id, "message_id": last_id})

    def _extract_ats(self, chunks: List[str], channel_type: str) -> tuple:
        """从首块提取 [at_uid:xxx]（仅群聊、非自己），返回 (成员名列表, 清洗后分块)。"""
        if channel_type != "group" or not chunks:
            return [], chunks
        first = chunks[0]
        members: List[str] = []
        self_names = set(self._ch._self_names())

        def _sub(m: re.Match) -> str:
            uid = m.group(1).strip()
            if uid and uid not in self_names and uid not in members:
                members.append(uid)
            return ""

        cleaned = _AT_PATTERN.sub(_sub, first).strip()
        chunks = ([cleaned] if cleaned else []) + chunks[1:]
        if not chunks and members:
            chunks = [" "]
        return members, chunks

    # ------------------------------------------------------------------
    # 媒体
    # ------------------------------------------------------------------

    async def send_photo(self, chat_id: str, photo: str, caption: str = "",
                         **kwargs: Any) -> str:
        """发送图片（本地文件 base64 化经桥上送）。"""
        return await self._send_media(chat_id, photo, caption, image=True)

    async def send_video(self, chat_id: str, video: str, caption: str = "",
                         **kwargs: Any) -> str:
        """发送视频（微信桌面端以文件形态发送 mp4）。"""
        return await self._send_media(chat_id, video, caption, image=False)

    async def send_file(self, chat_id: str, file_path: str, caption: str = "",
                        **kwargs: Any) -> str:
        """发送文件（本地文件 base64 化经桥上送）。"""
        return await self._send_media(chat_id, file_path, caption, image=False)

    async def _send_media(self, chat_id: str, path: str, caption: str,
                          *, image: bool) -> str:
        resolved = self._resolve_path(path)
        if not resolved:
            return _err(f"发送失败: 本地文件不存在: {path}")
        try:
            data_b64 = await asyncio.to_thread(_read_file_base64, resolved)
        except (OSError, ValueError) as exc:
            return _err(f"发送失败: {exc}")
        method = protocol.RPC_SEND_IMAGE if image else protocol.RPC_SEND_FILE
        data = await self._rpc(method, {
            "chat": chat_id,
            "filename": os.path.basename(resolved),
            "data_b64": data_b64,
            "verify": bool(self._ch._cfg("send_verify", True)),
        })
        if data is None:
            return _err("发送图片失败" if image else "发送文件失败")
        if caption.strip():
            await asyncio.sleep(1.0)
            await self.send_text(chat_id, caption.strip())
        return _ok({"chat_id": chat_id})

    @staticmethod
    def _resolve_path(path: str) -> str:
        """解析媒体路径：绝对路径 / workspace 相对路径。"""
        raw = (path or "").strip()
        if not raw or raw.startswith(("http://", "https://", "data:", "base64://")):
            return ""
        expanded = os.path.expandvars(os.path.expanduser(raw))
        if os.path.isabs(expanded) and os.path.isfile(expanded):
            return expanded
        try:
            from core.config import ConfigManager
            ws = str(ConfigManager.get("workspace_root", "workspace") or "workspace")
        except Exception:
            ws = "workspace"
        for cand in (expanded, os.path.join(ws, expanded)):
            if os.path.isfile(cand):
                return os.path.abspath(cand)
        return ""

    # ------------------------------------------------------------------
    # RPC 通道
    # ------------------------------------------------------------------

    async def _rpc(self, method: str, params: Dict[str, Any]) -> Any:
        """调桥 RPC；失败记日志返回 None（错误文案由桥透传进日志）。"""
        try:
            return await self._ch._transport.call_rpc(method, params)
        except WcautoRpcError as exc:
            log(f"wcauto: {method} 失败 -> {exc}", "WARNING", tag="通道")
            return None
