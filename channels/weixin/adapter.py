"""微信频道 — 通过腾讯 iLink Bot API 接入个人微信。

协议层实现完整移植自 hermes-agent 的 weixin 适配器：
- 长轮询 ``getupdates`` 收消息（无需公网端点 / webhook）
- 文本 / 图片 / 视频 / 文件 / 语音收发，媒体走 AES-128-ECB 加密 CDN
- 每条出站回复回显对端最新 ``context_token``（-14 会话过期自动降级重发）
- typing「正在输入」提示、消息二级去重、游标持久化、文本防抖合批
- 扫码登录见 ``scripts/weixin_setup.py``（凭据存 workspace/weixin/accounts/）

限制：扫码登录连接的是 iLink bot 身份（...@im.bot），大多数情况下只有
发给 bot 的私聊能可靠工作；普通群消息通常无法到达（限制在 iLink 侧）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import Field

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus
from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    MessageSegment,
    SegmentType,
    SendRequest,
    SendResponse,
)
from core.log import log

from . import ilink_client as ilink
from .state import (
    ContextTokenStore,
    MessageDeduplicator,
    TypingTicketCache,
    load_sync_buf,
    load_weixin_account,
    save_sync_buf,
)


class WeixinConfig(ChannelConfig):
    """微信频道配置（iLink Bot API）。"""

    account_id: str = Field(default="", description="iLink Bot 账号 ID（扫码登录获得）")
    token: str = Field(default="", description="iLink Bot Token（留空则从 workspace/weixin/accounts 恢复）")
    base_url: str = Field(default=ilink.ILINK_BASE_URL, description="iLink API 地址")
    cdn_base_url: str = Field(default=ilink.WEIXIN_CDN_BASE_URL, description="微信 CDN 地址")
    dm_policy: str = Field(default="open", description="私聊策略: open/allowlist/disabled")
    group_policy: str = Field(default="disabled", description="群聊策略: open/allowlist/disabled")
    allow_from: str = Field(default="", description="私聊白名单（逗号分隔用户 ID）")
    group_allow_from: str = Field(default="", description="群聊白名单（逗号分隔群 ID）")
    split_multiline_messages: bool = Field(default=False, description="多行消息逐行拆分发送")
    send_chunk_delay_seconds: float = Field(default=1.5, description="文本分块发送间隔（秒）")
    send_chunk_retries: int = Field(default=4, description="单块发送重试次数")
    send_chunk_retry_delay_seconds: float = Field(default=1.0, description="发送重试基础退避（秒）")
    rate_limit_circuit_threshold: int = Field(default=1, description="限频熔断触发次数")
    rate_limit_circuit_window_seconds: float = Field(default=30.0, description="限频统计窗口（秒）")
    rate_limit_circuit_open_seconds: float = Field(default=30.0, description="熔断断开时长（秒）")
    text_batch_delay_seconds: float = Field(default=3.0, description="文本合批静默期（秒）")
    text_batch_split_delay_seconds: float = Field(default=5.0, description="长片段合批静默期（秒）")


class WeixinChannel(BaseChannel[WeixinConfig]):
    """个人微信频道（iLink Bot API 长轮询）。"""

    _entity_description = "个人微信频道（iLink Bot API）"

    channel_id = "weixin"
    display_name = "微信"
    capabilities: Set[ChannelCapability] = {
        ChannelCapability.SEND_TEXT,
        ChannelCapability.SEND_PHOTO,
        ChannelCapability.SEND_VIDEO,
        ChannelCapability.SEND_VOICE,
        ChannelCapability.SEND_FILE,
        ChannelCapability.GET_CHAT_INFO,
    }
    metadata = ChannelMetadata(
        name="Weixin (iLink)",
        description="通过腾讯 iLink Bot API 接入个人微信（扫码登录，长轮询收发）",
        version="1.0.0",
        author="AnelfAgent",
        tags=["weixin", "wechat", "ilink"],
    )
    _Configs = WeixinConfig

    MAX_MESSAGE_LENGTH = ilink.MAX_MESSAGE_LENGTH
    _SPLIT_THRESHOLD = 1800  # iLink 自身约 2048 字符切片

    def __init__(self) -> None:
        self._poll_session: Optional[Any] = None
        self._send_session: Optional[Any] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self._token_store = ContextTokenStore()
        self._typing_cache = TypingTicketCache()
        self._dedup = MessageDeduplicator(ttl_seconds=ilink.MESSAGE_DEDUP_TTL_SECONDS)
        self._send_text_gate = asyncio.Lock()
        self._rate_limit_circuit_until = 0.0
        self._rate_limit_events: List[float] = []
        self._pending_text_batches: Dict[str, AdapterMessage] = {}
        self._pending_text_batch_tasks: Dict[str, asyncio.Task] = {}
        self._last_chunk_lens: Dict[str, int] = {}
        self._known_groups: Set[str] = set()
        self._last_poll_ok: float = 0.0
        self._account_id = ""
        self._token = ""
        self._base_url = ilink.ILINK_BASE_URL
        self._cdn_base_url = ilink.WEIXIN_CDN_BASE_URL
        super().__init__()

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def _cfg(self, key: str, default: Any = None) -> Any:
        return getattr(self.config, key, default)

    @staticmethod
    def _coerce_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    def _resolve_credentials(self) -> None:
        """从配置（含 ANELF_WEIXIN_* 环境变量覆盖）解析凭据，token 缺失时从账号文件恢复。"""
        cfg = self.config
        self._account_id = (cfg.account_id or "").strip()
        self._token = (cfg.token or "").strip()
        self._base_url = (cfg.base_url or ilink.ILINK_BASE_URL).strip().rstrip("/")
        self._cdn_base_url = (cfg.cdn_base_url or ilink.WEIXIN_CDN_BASE_URL).strip().rstrip("/")
        if self._account_id and not self._token:
            persisted = load_weixin_account(self._account_id)
            if persisted:
                self._token = str(persisted.get("token") or "").strip()
                self._base_url = str(persisted.get("base_url") or self._base_url).strip().rstrip("/")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not ilink.check_weixin_requirements():
            raise RuntimeError("微信频道启动失败: 需要 aiohttp 和 cryptography（uv sync 安装）")

        self._resolve_credentials()
        if not self._account_id:
            raise RuntimeError("微信频道启动失败: 缺少 account_id（请先运行 scripts/weixin_setup.py 扫码登录）")
        if not self._token:
            raise RuntimeError("微信频道启动失败: 缺少 token（请先运行 scripts/weixin_setup.py 扫码登录）")

        import aiohttp

        self._poll_session = aiohttp.ClientSession(
            trust_env=True, connector=ilink._make_ssl_connector(),
        )
        # 关闭 aiohttp 内建 ClientTimeout，统一由 ilink_client 的
        # asyncio.wait_for() 控制，避免跨线程提交协程时的上下文错误。
        _no_timeout = aiohttp.ClientTimeout(total=None, connect=None, sock_connect=None, sock_read=None)
        self._send_session = aiohttp.ClientSession(
            trust_env=True, connector=ilink._make_ssl_connector(), timeout=_no_timeout,
        )
        self._token_store.restore(self._account_id)
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop(), name="weixin-poll")
        self._status = ChannelStatus.RUNNING
        log(
            f"微信: 已连接 account={self._account_id[:8]} base={self._base_url}", tag="通道",
        )
        group_policy = str(self._cfg("group_policy", "disabled")).strip().lower()
        if group_policy != "disabled":
            log(
                f"微信: group_policy={group_policy} 已设置，但扫码登录连接的是 iLink bot 身份"
                "（...@im.bot），通常无法被拉入普通微信群，iLink 一般也不推送群事件。"
                "若群消息不到达，限制在 iLink 侧而非本频道。",
                "WARNING",
                tag="通道",
            )

    async def stop(self) -> None:
        self._running = False
        self._status = ChannelStatus.STOPPED
        for task in self._pending_text_batch_tasks.values():
            if not task.done():
                task.cancel()
        self._pending_text_batches.clear()
        self._pending_text_batch_tasks.clear()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None
        if self._poll_session and not self._poll_session.closed:
            await self._poll_session.close()
        self._poll_session = None
        if self._send_session and not self._send_session.closed:
            await self._send_session.close()
        self._send_session = None
        log("微信: 已断开", tag="通道")

    # ------------------------------------------------------------------
    # 长轮询
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        assert self._poll_session is not None
        sync_buf = load_sync_buf(self._account_id)
        timeout_ms = ilink.LONG_POLL_TIMEOUT_MS
        consecutive_failures = 0

        while self._running:
            try:
                response = await ilink.get_updates(
                    self._poll_session,
                    base_url=self._base_url,
                    token=self._token,
                    sync_buf=sync_buf,
                    timeout_ms=timeout_ms,
                )
                suggested_timeout = response.get("longpolling_timeout_ms")
                if isinstance(suggested_timeout, int) and suggested_timeout > 0:
                    timeout_ms = suggested_timeout

                ret = response.get("ret", 0)
                errcode = response.get("errcode", 0)
                if ret not in {0, None} or errcode not in {0, None}:
                    if (
                        ret == ilink.SESSION_EXPIRED_ERRCODE
                        or errcode == ilink.SESSION_EXPIRED_ERRCODE
                        or ilink._is_stale_session_ret(ret, errcode, response.get("errmsg"))
                    ):
                        log("微信: 会话已过期，暂停 10 分钟（请重新扫码登录）", "ERROR", tag="通道")
                        await asyncio.sleep(600)
                        consecutive_failures = 0
                        continue
                    consecutive_failures += 1
                    log(
                        f"微信: getUpdates 失败 ret={ret} errcode={errcode} "
                        f"errmsg={response.get('errmsg', '')} ({consecutive_failures}/{ilink.MAX_CONSECUTIVE_FAILURES})",
                        "WARNING",
                        tag="通道",
                    )
                    await asyncio.sleep(
                        ilink.BACKOFF_DELAY_SECONDS
                        if consecutive_failures >= ilink.MAX_CONSECUTIVE_FAILURES
                        else ilink.RETRY_DELAY_SECONDS
                    )
                    if consecutive_failures >= ilink.MAX_CONSECUTIVE_FAILURES:
                        consecutive_failures = 0
                    continue

                consecutive_failures = 0
                self._last_poll_ok = time.time()
                new_sync_buf = str(response.get("get_updates_buf") or "")
                if new_sync_buf:
                    sync_buf = new_sync_buf
                    save_sync_buf(self._account_id, sync_buf)

                for message in response.get("msgs") or []:
                    asyncio.create_task(self._process_message_safe(message))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                consecutive_failures += 1
                log(
                    f"微信: 轮询异常 ({consecutive_failures}/{ilink.MAX_CONSECUTIVE_FAILURES}): {exc}",
                    "ERROR",
                    tag="通道",
                )
                await asyncio.sleep(
                    ilink.BACKOFF_DELAY_SECONDS
                    if consecutive_failures >= ilink.MAX_CONSECUTIVE_FAILURES
                    else ilink.RETRY_DELAY_SECONDS
                )
                if consecutive_failures >= ilink.MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0

    async def _process_message_safe(self, message: Dict[str, Any]) -> None:
        try:
            await self._process_message(message)
        except Exception as exc:
            log(
                f"微信: 入站消息处理异常 from={str(message.get('from_user_id') or '?')[:8]}: {exc}",
                "ERROR",
                tag="通道",
            )

    # ------------------------------------------------------------------
    # 入站消息处理
    # ------------------------------------------------------------------

    def _is_dm_allowed(self, sender_id: str) -> bool:
        policy = str(self._cfg("dm_policy", "open")).strip().lower()
        if policy == "disabled":
            return False
        if policy == "allowlist":
            return sender_id in self._coerce_list(self._cfg("allow_from", ""))
        return True  # open

    def _is_group_allowed(self, chat_id: str) -> bool:
        policy = str(self._cfg("group_policy", "disabled")).strip().lower()
        if policy == "disabled":
            return False
        if policy == "allowlist":
            return chat_id in self._coerce_list(self._cfg("group_allow_from", ""))
        return True  # open

    async def _process_message(self, message: Dict[str, Any]) -> None:
        assert self._poll_session is not None
        sender_id = str(message.get("from_user_id") or "").strip()
        if not sender_id:
            return
        if sender_id == self._account_id:
            return

        message_id = str(message.get("message_id") or "").strip()
        if message_id and self._dedup.is_duplicate(message_id):
            return

        # 二级：内容指纹去重
        item_list = message.get("item_list") or []
        text = ilink.extract_text(item_list)
        if text:
            content_key = f"content:{sender_id}:{hashlib.md5(text.encode()).hexdigest()}"
            if self._dedup.is_duplicate(content_key):
                log(f"微信: 内容去重，跳过重复消息 from={sender_id[:8]}", "DEBUG", tag="通道")
                return

        chat_type, effective_chat_id = ilink.guess_chat_type(message, self._account_id)
        if chat_type == "group":
            if not self._is_group_allowed(effective_chat_id):
                return
            self._known_groups.add(effective_chat_id)
        elif not self._is_dm_allowed(sender_id):
            log(f"微信: 私聊策略拦截 from={sender_id[:8]}", "DEBUG", tag="通道")
            return

        # 入站必存 context_token，出站回复必须回显
        context_token = str(message.get("context_token") or "").strip()
        if context_token:
            self._token_store.set(self._account_id, sender_id, context_token)
        asyncio.create_task(self._maybe_fetch_typing_ticket(sender_id, context_token or None))

        segments: List[MessageSegment] = []
        for item in item_list:
            await self._collect_media(item, segments)
            ref_message = item.get("ref_msg") or {}
            ref_item = ref_message.get("message_item")
            if isinstance(ref_item, dict):
                await self._collect_media(ref_item, segments)

        if not text and not segments:
            return

        adapter_msg = AdapterMessage(
            message_id=message_id or uuid.uuid4().hex[:16],
            sender=AdapterUser(
                platform=self.channel_id,
                user_id=sender_id,
                user_name=sender_id,
            ),
            channel=AdapterChannel(
                channel_id=effective_chat_id,
                channel_type=ChannelType.GROUP if chat_type == "group" else ChannelType.PRIVATE,
            ),
            content=text,
            segments=segments,
            is_to_me=(chat_type == "dm"),
            trigger_mind=True,
        )
        log(
            f"微信: 入站 from={sender_id[:8]} type={chat_type} media={len(segments)} "
            f"content={text[:60]}",
            "DEBUG",
            tag="通道",
        )
        if not segments and text:
            self._enqueue_text_message(adapter_msg)
        else:
            await self._dispatch(adapter_msg)

    async def _dispatch(self, message: AdapterMessage) -> None:
        """分发入站消息；将触发思考时先亮 typing。"""
        if message.trigger_mind and self._cfg("typing_indicator", True):
            asyncio.create_task(self.send_typing(message.channel.channel_id))
        await self.on_message(message)

    # ------------------------------------------------------------------
    # 文本防抖合批（iLink 逐条推送连发消息，静默期后合并为一条）
    # ------------------------------------------------------------------

    def _text_batch_key(self, message: AdapterMessage) -> str:
        return f"{message.channel.channel_type.value}:{message.channel.channel_id}:{message.sender.user_id}"

    def _enqueue_text_message(self, message: AdapterMessage) -> None:
        key = self._text_batch_key(message)
        chunk_len = len(message.content or "")
        existing = self._pending_text_batches.get(key)
        if existing is None:
            self._pending_text_batches[key] = message
        else:
            if message.content:
                existing.content = f"{existing.content}\n{message.content}" if existing.content else message.content
            existing.segments.extend(message.segments)
            existing.is_to_me = existing.is_to_me or message.is_to_me
            existing.trigger_mind = existing.trigger_mind or message.trigger_mind
        # 用最后一个片段长度决定静默期（长片段可能是 iLink 切片，等更久）
        self._last_chunk_lens[key] = chunk_len

        prior_task = self._pending_text_batch_tasks.get(key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        self._pending_text_batch_tasks[key] = asyncio.create_task(self._flush_text_batch(key))

    async def _flush_text_batch(self, key: str) -> None:
        current_task = asyncio.current_task()
        try:
            last_len = self._last_chunk_lens.get(key, 0)
            delay = (
                float(self._cfg("text_batch_split_delay_seconds", 5.0))
                if last_len >= self._SPLIT_THRESHOLD
                else float(self._cfg("text_batch_delay_seconds", 3.0))
            )
            await asyncio.sleep(delay)
            if self._pending_text_batch_tasks.get(key) is not current_task:
                return
            message = self._pending_text_batches.pop(key, None)
            if not message:
                return
            await self._dispatch(message)
        except asyncio.CancelledError:
            pass
        finally:
            if self._pending_text_batch_tasks.get(key) is current_task:
                self._pending_text_batch_tasks.pop(key, None)

    # ------------------------------------------------------------------
    # 入站媒体下载（AES 解密 → workspace/uploads/）
    # ------------------------------------------------------------------

    @staticmethod
    def _upload_dir(sub: str) -> Path:
        try:
            from core.config import ConfigManager
            ws = str(ConfigManager.get("workspace_root", "workspace") or "workspace")
        except Exception:
            ws = "workspace"
        path = Path(os.path.abspath(os.path.join(ws, "uploads", sub)))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save_inbound_media(self, data: bytes, sub: str, filename: str) -> str:
        safe_name = os.path.basename(filename) or "media.bin"
        path = self._upload_dir(sub) / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        path.write_bytes(data)
        return str(path)

    async def _collect_media(self, item: Dict[str, Any], segments: List[MessageSegment]) -> None:
        item_type = item.get("type")
        try:
            if item_type == ilink.ITEM_IMAGE:
                seg = await self._download_image(item)
            elif item_type == ilink.ITEM_VIDEO:
                seg = await self._download_video(item)
            elif item_type == ilink.ITEM_FILE:
                seg = await self._download_file(item)
            elif item_type == ilink.ITEM_VOICE:
                seg = await self._download_voice(item)
            else:
                seg = None
        except Exception as exc:
            log(f"微信: 媒体下载失败 type={item_type}: {exc}", "WARNING", tag="通道")
            seg = None
        if seg is not None:
            segments.append(seg)

    async def _download_image(self, item: Dict[str, Any]) -> Optional[MessageSegment]:
        media = ilink.media_reference(item, "image_item")
        image_item = item.get("image_item") or {}
        # 入站图片的 aeskey 是 hex 字符串，需先 fromhex 再 base64
        aeskey_hex = image_item.get("aeskey")
        aes_key_b64 = (
            base64.b64encode(bytes.fromhex(str(aeskey_hex))).decode("ascii")
            if aeskey_hex
            else media.get("aes_key")
        )
        data = await ilink.download_and_decrypt_media(
            self._poll_session,
            cdn_base_url=self._cdn_base_url,
            encrypted_query_param=media.get("encrypt_query_param"),
            aes_key_b64=aes_key_b64,
            full_url=media.get("full_url"),
            timeout_seconds=30.0,
        )
        path = self._save_inbound_media(data, "image", "photo.jpg")
        return MessageSegment(type=SegmentType.IMAGE, file_path=path, mime_type="image/jpeg", file_name="photo.jpg")

    async def _download_video(self, item: Dict[str, Any]) -> Optional[MessageSegment]:
        media = ilink.media_reference(item, "video_item")
        data = await ilink.download_and_decrypt_media(
            self._poll_session,
            cdn_base_url=self._cdn_base_url,
            encrypted_query_param=media.get("encrypt_query_param"),
            aes_key_b64=media.get("aes_key"),
            full_url=media.get("full_url"),
            timeout_seconds=120.0,
        )
        path = self._save_inbound_media(data, "video", "video.mp4")
        return MessageSegment(type=SegmentType.VIDEO, file_path=path, mime_type="video/mp4", file_name="video.mp4")

    async def _download_file(self, item: Dict[str, Any]) -> Optional[MessageSegment]:
        file_item = item.get("file_item") or {}
        media = file_item.get("media") or {}
        filename = str(file_item.get("file_name") or "document.bin")
        data = await ilink.download_and_decrypt_media(
            self._poll_session,
            cdn_base_url=self._cdn_base_url,
            encrypted_query_param=media.get("encrypt_query_param"),
            aes_key_b64=media.get("aes_key"),
            full_url=media.get("full_url"),
            timeout_seconds=60.0,
        )
        path = self._save_inbound_media(data, "file", filename)
        return MessageSegment(
            type=SegmentType.FILE,
            file_path=path,
            mime_type=ilink._mime_from_filename(filename),
            file_name=filename,
        )

    async def _download_voice(self, item: Dict[str, Any]) -> Optional[MessageSegment]:
        voice_item = item.get("voice_item") or {}
        # 有转写文本时 extract_text 已走文本路径，无需下载语音
        if voice_item.get("text"):
            return None
        media = voice_item.get("media") or {}
        data = await ilink.download_and_decrypt_media(
            self._poll_session,
            cdn_base_url=self._cdn_base_url,
            encrypted_query_param=media.get("encrypt_query_param"),
            aes_key_b64=media.get("aes_key"),
            full_url=media.get("full_url"),
            timeout_seconds=60.0,
        )
        path = self._save_inbound_media(data, "voice", "voice.silk")
        return MessageSegment(type=SegmentType.VOICE, file_path=path, mime_type="audio/silk", file_name="voice.silk")

    # ------------------------------------------------------------------
    # Typing「正在输入」
    # ------------------------------------------------------------------

    async def _maybe_fetch_typing_ticket(self, user_id: str, context_token: Optional[str]) -> None:
        if not self._poll_session or not self._token:
            return
        if self._typing_cache.get(user_id):
            return
        try:
            response = await ilink.get_config(
                self._poll_session,
                base_url=self._base_url,
                token=self._token,
                user_id=user_id,
                context_token=context_token,
            )
            typing_ticket = str(response.get("typing_ticket") or "")
            if typing_ticket:
                self._typing_cache.set(user_id, typing_ticket)
        except Exception as exc:
            log(f"微信: getConfig 失败 user={user_id[:8]}: {exc}", "DEBUG", tag="通道")

    async def _ensure_typing_ticket(self, chat_id: str) -> Optional[str]:
        """返回有效 typing ticket，过期时通过 getConfig 刷新。

        iLink ticket 有效期 600s，过期后 sendtyping/stop_typing 会静默失效，
        导致微信客户端一直卡在「正在输入」— 因此 stop 前必须保证 ticket 有效。
        """
        ticket = self._typing_cache.get(chat_id)
        if ticket:
            return ticket
        if not self._send_session or not self._token:
            return None
        context_token = self._token_store.get(self._account_id, chat_id)
        try:
            response = await ilink.get_config(
                self._send_session,
                base_url=self._base_url,
                token=self._token,
                user_id=chat_id,
                context_token=context_token,
            )
            typing_ticket = str(response.get("typing_ticket") or "")
            if typing_ticket:
                self._typing_cache.set(chat_id, typing_ticket)
                return typing_ticket
        except Exception as exc:
            log(f"微信: typing ticket 刷新失败 user={chat_id[:8]}: {exc}", "DEBUG", tag="通道")
        return None

    async def send_typing(self, chat_id: str) -> None:
        if not self._send_session or not self._token:
            return
        typing_ticket = await self._ensure_typing_ticket(chat_id)
        if not typing_ticket:
            return
        try:
            await ilink.send_typing(
                self._send_session,
                base_url=self._base_url,
                token=self._token,
                to_user_id=chat_id,
                typing_ticket=typing_ticket,
                status=ilink.TYPING_START,
            )
        except Exception as exc:
            log(f"微信: typing start 失败 user={chat_id[:8]}: {exc}", "DEBUG", tag="通道")

    async def stop_typing(self, chat_id: str) -> None:
        if not self._send_session or not self._token:
            return
        typing_ticket = await self._ensure_typing_ticket(chat_id)
        if not typing_ticket:
            return
        try:
            await ilink.send_typing(
                self._send_session,
                base_url=self._base_url,
                token=self._token,
                to_user_id=chat_id,
                typing_ticket=typing_ticket,
                status=ilink.TYPING_STOP,
            )
        except Exception as exc:
            log(f"微信: typing stop 失败 user={chat_id[:8]}: {exc}", "DEBUG", tag="通道")

    # ------------------------------------------------------------------
    # 统一发送入口
    # ------------------------------------------------------------------

    async def forward_message(self, request: SendRequest) -> SendResponse:
        if not self._send_session or not self._token:
            return SendResponse(success=False, error="微信频道未连接")
        chat_id = request.channel.channel_id
        message_ids: List[str] = []
        try:
            for seg in request.segments:
                if seg.type == SegmentType.TEXT:
                    if not (seg.content or "").strip():
                        continue
                    await self.stop_typing(chat_id)
                    last_id = await self._send_text(chat_id, seg.content)
                    message_ids.append(last_id)
                elif seg.type in {
                    SegmentType.IMAGE,
                    SegmentType.VIDEO,
                    SegmentType.FILE,
                    SegmentType.VOICE,
                    SegmentType.AUDIO,
                }:
                    path = seg.file_path or seg.content
                    if not path:
                        continue
                    await self.stop_typing(chat_id)
                    last_id = await self._send_media(
                        chat_id,
                        path,
                        caption=seg.caption or "",
                        force_file_attachment=(seg.type in {SegmentType.VOICE, SegmentType.AUDIO}),
                    )
                    message_ids.append(last_id)
            return SendResponse(
                success=True,
                message_id=message_ids[-1] if message_ids else None,
                message_ids=message_ids,
            )
        except Exception as exc:
            log(f"微信: 发送失败 to={chat_id[:8]}: {exc}", "ERROR", tag="通道")
            return SendResponse(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # 文本发送（分块 + 重试 + 限频熔断）
    # ------------------------------------------------------------------

    async def _send_text(self, chat_id: str, content: str) -> str:
        context_token = self._token_store.get(self._account_id, chat_id)
        if not context_token:
            log(
                f"微信: 发往 {chat_id[:8]} 的消息没有 context_token（从未收到该用户的入站消息），"
                "iLink 可能返回成功但不实际投递。请先让对方在微信里给 bot 发一条消息建立会话。",
                "WARNING",
                tag="通道",
            )
        chunks = [
            c
            for c in ilink.split_text_for_weixin_delivery(
                ilink.format_message(content),
                self.MAX_MESSAGE_LENGTH,
                bool(self._cfg("split_multiline_messages", False)),
            )
            if c and c.strip()
        ]
        last_message_id = ""
        for idx, chunk in enumerate(chunks):
            client_id = f"anel-weixin-{uuid.uuid4().hex}"
            await self._send_text_chunk(
                chat_id=chat_id,
                chunk=chunk,
                context_token=context_token,
                client_id=client_id,
            )
            last_message_id = client_id
            if idx < len(chunks) - 1:
                delay = float(self._cfg("send_chunk_delay_seconds", 1.5))
                if delay > 0:
                    await asyncio.sleep(delay)
        return last_message_id

    def _rate_limit_cooldown_remaining(self) -> float:
        return max(0.0, self._rate_limit_circuit_until - time.monotonic())

    def _rate_limit_error(self) -> RuntimeError:
        return RuntimeError(
            f"iLink sendmessage 限频，熔断冷却剩余 {self._rate_limit_cooldown_remaining():.1f}s"
        )

    def _record_rate_limit_event(self) -> bool:
        """记录一次真实限频，返回熔断器是否已断开。"""
        now = time.monotonic()
        window = float(self._cfg("rate_limit_circuit_window_seconds", 30.0))
        threshold = max(1, int(self._cfg("rate_limit_circuit_threshold", 1)))
        self._rate_limit_events = [ts for ts in self._rate_limit_events if ts >= now - window]
        self._rate_limit_events.append(now)
        if len(self._rate_limit_events) >= threshold:
            open_seconds = float(self._cfg("rate_limit_circuit_open_seconds", 30.0))
            if open_seconds > 0:
                self._rate_limit_circuit_until = max(
                    self._rate_limit_circuit_until, now + open_seconds,
                )
            return self._rate_limit_cooldown_remaining() > 0
        return False

    def _reset_rate_limit_circuit(self) -> None:
        self._rate_limit_events.clear()
        self._rate_limit_circuit_until = 0.0

    async def _send_text_chunk(
        self,
        *,
        chat_id: str,
        chunk: str,
        context_token: Optional[str],
        client_id: str,
    ) -> None:
        async with self._send_text_gate:
            await self._send_text_chunk_locked(
                chat_id=chat_id,
                chunk=chunk,
                context_token=context_token,
                client_id=client_id,
            )

    async def _send_text_chunk_locked(
        self,
        *,
        chat_id: str,
        chunk: str,
        context_token: Optional[str],
        client_id: str,
    ) -> None:
        """单块发送（持锁），-14 自动去掉 context_token 降级重发一次。

        iLink 接受无 context_token 的降级发送，可保证长时间无入站消息后
        的主动推送（如定时任务）不失败。
        """
        retries = int(self._cfg("send_chunk_retries", 4))
        retry_delay = float(self._cfg("send_chunk_retry_delay_seconds", 1.0))
        last_error: Optional[Exception] = None
        retried_without_token = False
        for attempt in range(retries + 1):
            if self._rate_limit_cooldown_remaining() > 0:
                raise self._rate_limit_error()
            try:
                resp = await ilink.send_message(
                    self._send_session,
                    base_url=self._base_url,
                    token=self._token,
                    to=chat_id,
                    text=chunk,
                    context_token=context_token,
                    client_id=client_id,
                )
                if resp and isinstance(resp, dict):
                    ret = resp.get("ret")
                    errcode = resp.get("errcode")
                    if (ret is not None and ret not in {0}) or (errcode is not None and errcode not in {0}):
                        is_session_expired = (
                            ret == ilink.SESSION_EXPIRED_ERRCODE
                            or errcode == ilink.SESSION_EXPIRED_ERRCODE
                            or ilink._is_stale_session_ret(ret, errcode, resp.get("errmsg"))
                        )
                        if is_session_expired and not retried_without_token and context_token:
                            retried_without_token = True
                            context_token = None
                            self._token_store.pop(self._account_id, chat_id)
                            log(
                                f"微信: 会话过期 to={chat_id[:8]}，去掉 context_token 重试",
                                "WARNING",
                                tag="通道",
                            )
                            continue
                        is_rate_limited = (
                            ret == ilink.RATE_LIMIT_ERRCODE or errcode == ilink.RATE_LIMIT_ERRCODE
                        )
                        if is_rate_limited:
                            errmsg = resp.get("errmsg") or resp.get("msg") or "rate limited"
                            last_error = RuntimeError(
                                f"iLink sendmessage 限频: ret={ret} errcode={errcode} errmsg={errmsg}"
                            )
                            if self._record_rate_limit_event():
                                last_error = self._rate_limit_error()
                                break
                            if attempt >= retries:
                                break
                            wait = retry_delay * 3  # 限频 3 倍退避
                            log(
                                f"微信: 限频 to={chat_id[:8]}，{wait:.1f}s 后重试",
                                "WARNING",
                                tag="通道",
                            )
                            await asyncio.sleep(wait)
                            continue
                        errmsg = resp.get("errmsg") or resp.get("msg") or "unknown error"
                        raise RuntimeError(
                            f"iLink sendmessage 错误: ret={ret} errcode={errcode} errmsg={errmsg}"
                        )
                self._reset_rate_limit_circuit()
                log(
                    f"微信: 发送块成功 to={chat_id[:8]} resp={json.dumps(resp, ensure_ascii=False)[:200]}",
                    "DEBUG",
                    tag="通道",
                )
                return
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    break
                wait = retry_delay * (attempt + 1)
                log(
                    f"微信: 发送块失败 to={chat_id[:8]} 第{attempt + 1}/{retries + 1}次，"
                    f"{wait:.2f}s 后重试: {exc}",
                    "WARNING",
                    tag="通道",
                )
                if wait > 0:
                    await asyncio.sleep(wait)
        assert last_error is not None
        raise last_error

    # ------------------------------------------------------------------
    # 媒体发送（AES 加密上传管线）
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_local_file_path(path: str) -> str:
        """解析媒体路径：绝对路径 / 项目相对路径 / workspace 相对路径（同 QQ 频道）。"""
        raw = (path or "").strip()
        if not raw:
            return raw
        if raw.startswith(("http://", "https://", "file://")):
            return raw
        expanded = os.path.expandvars(os.path.expanduser(raw))
        if os.path.isabs(expanded):
            return os.path.normpath(expanded)

        candidates = [os.path.normpath(expanded)]
        try:
            from core.config import ConfigManager
            workspace_root = str(ConfigManager.get("workspace_root", "workspace") or "workspace")
        except Exception:
            workspace_root = "workspace"
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

    async def _download_remote_media(self, url: str) -> str:
        """下载远程媒体到本地临时文件（仅允许 http/https）。"""
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"非法媒体 URL: {url}")
        assert self._send_session is not None

        async def _do_fetch() -> bytes:
            async with self._send_session.get(url) as response:
                response.raise_for_status()
                return await response.read()

        data = await asyncio.wait_for(_do_fetch(), timeout=30)
        suffix = Path(url.split("?", 1)[0]).suffix or ".bin"
        path = self._upload_dir("file") / f"{uuid.uuid4().hex[:8]}_remote{suffix}"
        path.write_bytes(data)
        return str(path)

    async def _send_media(
        self,
        chat_id: str,
        path: str,
        caption: str = "",
        force_file_attachment: bool = False,
    ) -> str:
        cleanup = False
        if path.startswith(("http://", "https://")):
            path = await self._download_remote_media(path)
            cleanup = True
        else:
            path = self._resolve_local_file_path(path.replace("file://", ""))
        try:
            return await self._send_file(chat_id, path, caption, force_file_attachment=force_file_attachment)
        finally:
            if cleanup and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    async def _send_file(
        self,
        chat_id: str,
        path: str,
        caption: str,
        force_file_attachment: bool = False,
    ) -> str:
        """完整上传流程：getuploadurl → AES 加密 → CDN POST → sendmessage。"""
        assert self._send_session is not None and self._token is not None
        plaintext = Path(path).read_bytes()
        media_type, item_builder = self._outbound_media_builder(path, force_file_attachment=force_file_attachment)
        filekey = secrets.token_hex(16)
        aes_key = secrets.token_bytes(16)
        rawsize = len(plaintext)
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()
        upload_response = await ilink.get_upload_url(
            self._send_session,
            base_url=self._base_url,
            token=self._token,
            to_user_id=chat_id,
            media_type=media_type,
            filekey=filekey,
            rawsize=rawsize,
            rawfilemd5=rawfilemd5,
            filesize=ilink._aes_padded_size(rawsize),
            aeskey_hex=aes_key.hex(),
        )
        upload_param = str(upload_response.get("upload_param") or "")
        upload_full_url = str(upload_response.get("upload_full_url") or "")
        ciphertext = ilink._aes128_ecb_encrypt(plaintext, aes_key)

        # 优先 upload_full_url（直连 CDN），否则用 upload_param 构造。
        # 两条路径都用 POST — 旧版对 upload_full_url 用 PUT 会在微信 CDN 404。
        if upload_full_url:
            upload_url = upload_full_url
        elif upload_param:
            upload_url = ilink._cdn_upload_url(self._cdn_base_url, upload_param, filekey)
        else:
            raise RuntimeError(f"getUploadUrl 未返回 upload_param 或 upload_full_url: {upload_response}")

        encrypted_query_param = await ilink.upload_ciphertext(
            self._send_session,
            ciphertext=ciphertext,
            upload_url=upload_url,
        )
        context_token = self._token_store.get(self._account_id, chat_id)
        # iLink API 期望 aes_key 为 base64(hex字符串)，而非 base64(原始字节)。
        # 发错会导致接收方图片显示灰图（解密密钥不匹配）。
        aes_key_for_api = base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii")
        item_kwargs: Dict[str, Any] = {
            "encrypt_query_param": encrypted_query_param,
            "aes_key_for_api": aes_key_for_api,
            "ciphertext_size": len(ciphertext),
            "plaintext_size": rawsize,
            "filename": Path(path).name,
            "rawfilemd5": rawfilemd5,
        }
        if media_type == ilink.MEDIA_VOICE and path.endswith(".silk"):
            item_kwargs["encode_type"] = 6
            item_kwargs["sample_rate"] = 24000
            item_kwargs["bits_per_sample"] = 16
        media_item = item_builder(**item_kwargs)

        last_message_id = ""
        if caption:
            last_message_id = f"anel-weixin-{uuid.uuid4().hex}"
            await ilink.send_message(
                self._send_session,
                base_url=self._base_url,
                token=self._token,
                to=chat_id,
                text=ilink.format_message(caption),
                context_token=context_token,
                client_id=last_message_id,
            )

        last_message_id = f"anel-weixin-{uuid.uuid4().hex}"
        await ilink.send_media_message(
            self._send_session,
            base_url=self._base_url,
            token=self._token,
            to=chat_id,
            media_item=media_item,
            context_token=context_token,
            client_id=last_message_id,
        )
        return last_message_id

    @staticmethod
    def _outbound_media_builder(path: str, force_file_attachment: bool = False):
        """按 MIME/扩展名构造出站媒体 item（图片/视频/语音/文件）。"""
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if mime.startswith("image/"):
            return ilink.MEDIA_IMAGE, lambda **kw: {
                "type": ilink.ITEM_IMAGE,
                "image_item": {
                    "media": {
                        "encrypt_query_param": kw["encrypt_query_param"],
                        "aes_key": kw["aes_key_for_api"],
                        "encrypt_type": 1,
                    },
                    "mid_size": kw["ciphertext_size"],
                },
            }
        if mime.startswith("video/"):
            return ilink.MEDIA_VIDEO, lambda **kw: {
                "type": ilink.ITEM_VIDEO,
                "video_item": {
                    "media": {
                        "encrypt_query_param": kw["encrypt_query_param"],
                        "aes_key": kw["aes_key_for_api"],
                        "encrypt_type": 1,
                    },
                    "video_size": kw["ciphertext_size"],
                    "play_length": kw.get("play_length", 0),
                    "video_md5": kw.get("rawfilemd5", ""),
                },
            }
        if path.endswith(".silk") and not force_file_attachment:
            return ilink.MEDIA_VOICE, lambda **kw: {
                "type": ilink.ITEM_VOICE,
                "voice_item": {
                    "media": {
                        "encrypt_query_param": kw["encrypt_query_param"],
                        "aes_key": kw["aes_key_for_api"],
                        "encrypt_type": 1,
                    },
                    "encode_type": kw.get("encode_type"),
                    "bits_per_sample": kw.get("bits_per_sample"),
                    "sample_rate": kw.get("sample_rate"),
                    "playtime": kw.get("playtime", 0),
                },
            }
        # 语音及其他类型统一走文件附件（上游实现中语音气泡未验证可靠）
        return ilink.MEDIA_FILE, lambda **kw: {
            "type": ilink.ITEM_FILE,
            "file_item": {
                "media": {
                    "encrypt_query_param": kw["encrypt_query_param"],
                    "aes_key": kw["aes_key_for_api"],
                    "encrypt_type": 1,
                },
                "file_name": kw["filename"],
                "len": str(kw["plaintext_size"]),
            },
        }

    # ------------------------------------------------------------------
    # 路由辅助
    # ------------------------------------------------------------------

    def is_known_group(self, target_id: str) -> bool:
        """判断 target_id 是否为已知群聊（运行期缓存 + 群白名单配置）。

        用于重启后、尚未收到群消息时的主动发送路由判断。
        """
        if target_id in self._known_groups:
            return True
        if target_id.endswith("@chatroom"):
            return True
        return target_id in self._coerce_list(self._cfg("group_allow_from", ""))

    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------

    async def get_self_info(self) -> ChannelUser:
        return ChannelUser(
            platform=self.channel_id,
            user_id=self._account_id or "weixin_bot",
            user_name=self._account_id or "微信",
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
        return ChannelInfo(
            channel_id=channel_id,
            channel_name=channel_id,
            channel_type=(
                ChannelType.GROUP if self.is_known_group(channel_id) else ChannelType.PRIVATE
            ),
        )

    async def health_check(self) -> HealthStatus:
        if not self._running or not self._poll_task or self._poll_task.done():
            return HealthStatus(
                healthy=False,
                detail="长轮询未运行",
                last_error="poll_not_running",
            )
        # 长轮询周期约 35s，超过 180s 无成功响应视为异常
        if self._last_poll_ok and time.time() - self._last_poll_ok > 180:
            return HealthStatus(
                healthy=False,
                detail=f"超过 180s 无成功轮询（上次 {time.time() - self._last_poll_ok:.0f}s 前）",
                last_error="poll_stale",
            )
        return HealthStatus(
            healthy=True,
            detail=f"微信 OK (account={self._account_id[:8]})",
            last_success_at=self._last_poll_ok or None,
        )

    def get_status_info(self) -> Dict[str, Any]:
        info = super().get_status_info()
        info["account_id"] = self._account_id[:8] if self._account_id else ""
        info["base_url"] = self._base_url
        info["poll_running"] = bool(self._poll_task and not self._poll_task.done())
        info["detail"] = (
            f"account={self._account_id[:8]}, long-polling"
            if info["poll_running"]
            else "未连接（请先运行 scripts/weixin_setup.py 扫码登录）"
        )
        return info

    # ------------------------------------------------------------------
    # HTTP 路由钩子（扫码登录）
    # ------------------------------------------------------------------

    def get_router(self) -> Optional[Any]:
        return build_router()

    async def render_approval_prompt(self, ctx) -> SendRequest:
        """渲染批准提示（微信纯文本 approve/deny，同 QQ 频道）。"""
        from agent.channel.base import ApprovalPromptRenderContext  # noqa: F401
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


CHANNEL_CLASS = WeixinChannel


# ======================================================================
# WebUI 扫码登录路由（挂载于 /api/channels/weixin，无需频道已启用）
# ======================================================================

def build_router() -> Any:
    """微信频道 HTTP 路由：扫码登录（WebUI 频道页调用）。"""
    from fastapi import APIRouter, HTTPException

    from .qr_login import get_qr_manager

    router = APIRouter()

    @router.post("/qr/start")
    async def qr_start() -> Dict[str, Any]:
        """拉取登录二维码，返回 {session_id, qr_png(data URL), qr_url}。"""
        try:
            return await get_qr_manager().start()
        except Exception as exc:
            raise HTTPException(500, str(exc))

    @router.get("/qr/{session_id}/status")
    async def qr_status(session_id: str) -> Dict[str, Any]:
        """推进一次扫码状态检查；确认后自动写入配置并启动频道。"""
        result = await get_qr_manager().poll(session_id)
        credential = result.pop("credential", None)
        if result.get("status") == "confirmed" and credential:
            await _apply_login_credential(credential)
        return result

    @router.delete("/qr/{session_id}")
    async def qr_discard(session_id: str) -> Dict[str, str]:
        await get_qr_manager().discard(session_id)
        return {"status": "ok"}

    return router


async def _apply_login_credential(credential: Dict[str, str]) -> None:
    """扫码成功后：写 channel_config.json → 启用 → （重）启动频道。"""
    import json

    cfg_path = Path(__file__).parent / "channel_config.json"
    cfg: Dict[str, Any] = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg["enabled"] = True
    cfg["account_id"] = credential["account_id"]
    cfg["token"] = credential["token"]
    cfg["base_url"] = credential.get("base_url") or cfg.get("base_url") or ilink.ILINK_BASE_URL
    cfg.setdefault("cdn_base_url", ilink.WEIXIN_CDN_BASE_URL)
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"微信: 扫码凭据已写入配置 account={credential['account_id'][:8]}", tag="通道")

    try:
        from agent.channel import get_channel_manager

        mgr = get_channel_manager()
        channel = mgr.get("weixin")
        if channel is not None:
            channel.reload_config()
            if channel.status == ChannelStatus.RUNNING:
                await mgr.stop_channel("weixin")
            await mgr.start_channel("weixin")
            log("微信: 频道已按新凭据重启", tag="通道")
            return
        # 频道未注册（此前 enabled=false）：实例化 → 注册 → 启动
        instance = WeixinChannel()
        mgr.register(instance)
        await mgr.start_channel("weixin")
        log("微信: 频道已注册并启动", tag="通道")
    except Exception as exc:
        log(f"微信: 扫码后自动启动失败（可手动在频道页启动）: {exc}", "WARNING", tag="通道")
