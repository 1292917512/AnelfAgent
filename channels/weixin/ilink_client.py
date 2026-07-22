"""iLink Bot API 协议层 — 个人微信（Weixin）频道的底层通信实现。

移植自 hermes-agent ``gateway/platforms/weixin.py`` 的协议部分（纯函数），
不包含任何频道框架依赖，便于独立测试。

设计要点：
- 长轮询 ``getupdates`` 驱动入站消息投递；
- 每条出站回复必须回显对端最新的 ``context_token``；
- 媒体文件走 AES-128-ECB 加密的 CDN 协议；
- 鉴权为 Bearer token + 固定 iLink 头部，无 HMAC 签名。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import re
import secrets
import struct
import textwrap
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from core.log import log

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖门禁
    aiohttp = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖门禁
    default_backend = None  # type: ignore[assignment]
    Cipher = None  # type: ignore[assignment]
    algorithms = None  # type: ignore[assignment]
    modes = None  # type: ignore[assignment]
    CRYPTO_AVAILABLE = False


# ======================================================================
# 常量
# ======================================================================

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "2.2.0"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0  # 0x20200

EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_SEND_TYPING = "ilink/bot/sendtyping"
EP_GET_CONFIG = "ilink/bot/getconfig"
EP_GET_UPLOAD_URL = "ilink/bot/getuploadurl"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"

LONG_POLL_TIMEOUT_MS = 35_000
API_TIMEOUT_MS = 15_000
CONFIG_TIMEOUT_MS = 10_000
QR_TIMEOUT_MS = 35_000

MAX_CONSECUTIVE_FAILURES = 3
RETRY_DELAY_SECONDS = 2
BACKOFF_DELAY_SECONDS = 30
SESSION_EXPIRED_ERRCODE = -14
RATE_LIMIT_ERRCODE = -2  # iLink 频率限制 — 退避重试
MESSAGE_DEDUP_TTL_SECONDS = 300

MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_FILE = 3
MEDIA_VOICE = 4

ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5

MSG_TYPE_USER = 1
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2

TYPING_START = 1
TYPING_STOP = 2

MAX_MESSAGE_LENGTH = 2000
WEIXIN_COPY_LINE_WIDTH = 120

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_RULE_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
_FENCE_RE = re.compile(r"^```([^\n`]*)\s*$")


def check_weixin_requirements() -> bool:
    """运行依赖（aiohttp + cryptography）是否可用。"""
    return AIOHTTP_AVAILABLE and CRYPTO_AVAILABLE


def _is_stale_session_ret(
    ret: "Optional[int]", errcode: "Optional[int]", errmsg: "Optional[str]",
) -> bool:
    """iLink 返回 ret=-2 / errcode=-2 且 errmsg 为 'unknown error' 时，
    实际是会话过期信号（等同 errcode=-14），而非真正的限频。"""
    if ret != RATE_LIMIT_ERRCODE and errcode != RATE_LIMIT_ERRCODE:
        return False
    return (errmsg or "").lower() == "unknown error"


def _safe_id(value: Optional[str], keep: int = 8) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "?"
    if len(raw) <= keep:
        return raw
    return raw[:keep]


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _coerce_bool(value: Any, default: bool = True) -> bool:
    """容忍 ``"true"`` 等字符串的布尔转换。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


# ======================================================================
# AES-128-ECB 媒体加解密
# ======================================================================

def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _aes128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def _aes128_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    if not padded:
        return padded
    pad_len = padded[-1]
    if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
        return padded[:-pad_len]
    return padded


def _aes_padded_size(size: int) -> int:
    return ((size + 1 + 15) // 16) * 16


def _parse_aes_key(aes_key_b64: str) -> bytes:
    """解析 base64 编码的 AES key，兼容 base64(原始16字节) 与 base64(hex字符串) 两种格式。"""
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        text = decoded.decode("ascii", errors="ignore")
        if text and all(ch in "0123456789abcdefABCDEF" for ch in text):
            return bytes.fromhex(text)
    raise ValueError(f"unexpected aes_key format ({len(decoded)} decoded bytes)")


# ======================================================================
# 鉴权与 HTTP 封装
# ======================================================================

def _make_ssl_connector() -> Optional["aiohttp.TCPConnector"]:
    """返回使用 certifi CA bundle 的 TCPConnector（不可用时返回 None）。

    腾讯 iLink 服务器（``ilinkai.weixin.qq.com``）在某些系统 CA store
    （尤其是 macOS Apple Silicon 上 Homebrew 的 OpenSSL）下校验失败。
    使用 certifi 的 Mozilla CA bundle 保证校验通过；否则回退 aiohttp 默认。
    """
    try:
        import ssl
        import certifi
    except ImportError:
        return None
    if not AIOHTTP_AVAILABLE:
        return None
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    return aiohttp.TCPConnector(ssl=ssl_ctx)


def _random_wechat_uin() -> str:
    value = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _base_info() -> Dict[str, Any]:
    return {"channel_version": CHANNEL_VERSION}


def _headers(token: Optional[str], body: str) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _api_post(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    endpoint: str,
    payload: Dict[str, Any],
    token: Optional[str],
    timeout_ms: int,
) -> Dict[str, Any]:
    body = _json_dumps({**payload, "base_info": _base_info()})
    url = f"{base_url.rstrip('/')}/{endpoint}"

    # 使用 asyncio.wait_for() 而非 aiohttp ClientTimeout，避免在
    # asyncio.run_coroutine_threadsafe() 跨线程调用场景下出现
    # "Timeout context manager should be used inside a task" 错误。
    async def _do() -> Dict[str, Any]:
        async with session.post(url, data=body, headers=_headers(token, body)) as response:
            raw = await response.text()
            if not response.ok:
                raise RuntimeError(f"iLink POST {endpoint} HTTP {response.status}: {raw[:200]}")
            return json.loads(raw)

    return await asyncio.wait_for(_do(), timeout=timeout_ms / 1000)


async def _api_get(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    endpoint: str,
    timeout_ms: int,
) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint}"
    headers = {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }

    async def _do() -> Dict[str, Any]:
        async with session.get(url, headers=headers) as response:
            raw = await response.text()
            if not response.ok:
                raise RuntimeError(f"iLink GET {endpoint} HTTP {response.status}: {raw[:200]}")
            return json.loads(raw)

    return await asyncio.wait_for(_do(), timeout=timeout_ms / 1000)


# ======================================================================
# iLink API 封装
# ======================================================================

async def get_updates(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    token: str,
    sync_buf: str,
    timeout_ms: int,
) -> Dict[str, Any]:
    """长轮询拉取消息。超时视为空响应，保证轮询循环不中断。"""
    try:
        return await _api_post(
            session,
            base_url=base_url,
            endpoint=EP_GET_UPDATES,
            payload={"get_updates_buf": sync_buf},
            token=token,
            timeout_ms=timeout_ms,
        )
    except asyncio.TimeoutError:
        return {"ret": 0, "msgs": [], "get_updates_buf": sync_buf}


async def send_message(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    token: str,
    to: str,
    text: str,
    context_token: Optional[str],
    client_id: str,
) -> Dict[str, Any]:
    """发送文本消息，返回原始 API 响应（调用方需检查 errcode）。"""
    if not text or not text.strip():
        raise ValueError("send_message: text must not be empty")
    message: Dict[str, Any] = {
        "from_user_id": "",
        "to_user_id": to,
        "client_id": client_id,
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
    }
    if context_token:
        message["context_token"] = context_token
    return await _api_post(
        session,
        base_url=base_url,
        endpoint=EP_SEND_MESSAGE,
        payload={"msg": message},
        token=token,
        timeout_ms=API_TIMEOUT_MS,
    )


async def send_media_message(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    token: str,
    to: str,
    media_item: Dict[str, Any],
    context_token: Optional[str],
    client_id: str,
) -> Dict[str, Any]:
    """发送媒体消息（item_list 为单个媒体 item）。"""
    return await _api_post(
        session,
        base_url=base_url,
        endpoint=EP_SEND_MESSAGE,
        payload={
            "msg": {
                "from_user_id": "",
                "to_user_id": to,
                "client_id": client_id,
                "message_type": MSG_TYPE_BOT,
                "message_state": MSG_STATE_FINISH,
                "item_list": [media_item],
                **({"context_token": context_token} if context_token else {}),
            }
        },
        token=token,
        timeout_ms=API_TIMEOUT_MS,
    )


async def send_typing(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    token: str,
    to_user_id: str,
    typing_ticket: str,
    status: int,
) -> None:
    await _api_post(
        session,
        base_url=base_url,
        endpoint=EP_SEND_TYPING,
        payload={
            "ilink_user_id": to_user_id,
            "typing_ticket": typing_ticket,
            "status": status,
        },
        token=token,
        timeout_ms=CONFIG_TIMEOUT_MS,
    )


async def get_config(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    token: str,
    user_id: str,
    context_token: Optional[str],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"ilink_user_id": user_id}
    if context_token:
        payload["context_token"] = context_token
    return await _api_post(
        session,
        base_url=base_url,
        endpoint=EP_GET_CONFIG,
        payload=payload,
        token=token,
        timeout_ms=CONFIG_TIMEOUT_MS,
    )


async def get_upload_url(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    token: str,
    to_user_id: str,
    media_type: int,
    filekey: str,
    rawsize: int,
    rawfilemd5: str,
    filesize: int,
    aeskey_hex: str,
) -> Dict[str, Any]:
    return await _api_post(
        session,
        base_url=base_url,
        endpoint=EP_GET_UPLOAD_URL,
        payload={
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aeskey_hex,
        },
        token=token,
        timeout_ms=API_TIMEOUT_MS,
    )


# ======================================================================
# CDN 上传 / 下载
# ======================================================================

def _cdn_download_url(cdn_base_url: str, encrypted_query_param: str) -> str:
    return f"{cdn_base_url.rstrip('/')}/download?encrypted_query_param={quote(encrypted_query_param, safe='')}"


def _cdn_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    return (
        f"{cdn_base_url.rstrip('/')}/upload"
        f"?encrypted_query_param={quote(upload_param, safe='')}"
        f"&filekey={quote(filekey, safe='')}"
    )


async def upload_ciphertext(
    session: "aiohttp.ClientSession",
    *,
    ciphertext: bytes,
    upload_url: str,
) -> str:
    """上传加密媒体到 CDN，返回响应头 ``x-encrypted-param``。

    构造的 CDN URL（upload_param）与直连 upload_full_url 都用 POST —
    旧版对 upload_full_url 用 PUT 会在微信 CDN 返回 404。
    """

    async def _do_upload() -> str:
        async with session.post(
            upload_url,
            data=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
        ) as response:
            if response.status == 200:
                encrypted_param = response.headers.get("x-encrypted-param")
                if encrypted_param:
                    await response.read()
                    return encrypted_param
                raw = await response.text()
                raise RuntimeError(f"CDN upload missing x-encrypted-param header: {raw[:200]}")
            raw = await response.text()
            raise RuntimeError(f"CDN upload HTTP {response.status}: {raw[:200]}")

    return await asyncio.wait_for(_do_upload(), timeout=120)


async def _download_bytes(
    session: "aiohttp.ClientSession",
    *,
    url: str,
    timeout_seconds: float = 60.0,
) -> bytes:
    async def _do_download() -> bytes:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.read()

    return await asyncio.wait_for(_do_download(), timeout=timeout_seconds)


_WEIXIN_CDN_ALLOWLIST: "frozenset[str]" = frozenset(
    {
        "novac2c.cdn.weixin.qq.com",
        "ilinkai.weixin.qq.com",
        "wx.qlogo.cn",
        "thirdwx.qlogo.cn",
        "res.wx.qq.com",
        "mmbiz.qpic.cn",
        "mmbiz.qlogo.cn",
    }
)


def assert_weixin_cdn_url(url: str) -> None:
    """校验 URL 指向已知的微信 CDN 主机（SSRF 防护），否则抛 ValueError。"""
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
    except Exception as exc:
        raise ValueError(f"Unparseable media URL: {url!r}") from exc

    if scheme not in {"http", "https"}:
        raise ValueError(
            f"Media URL has disallowed scheme {scheme!r}; only http/https are permitted."
        )
    if host not in _WEIXIN_CDN_ALLOWLIST:
        raise ValueError(
            f"Media URL host {host!r} is not in the WeChat CDN allowlist. "
            "Refusing to fetch to prevent SSRF."
        )


def media_reference(item: Dict[str, Any], key: str) -> Dict[str, Any]:
    return (item.get(key) or {}).get("media") or {}


async def download_and_decrypt_media(
    session: "aiohttp.ClientSession",
    *,
    cdn_base_url: str,
    encrypted_query_param: Optional[str],
    aes_key_b64: Optional[str],
    full_url: Optional[str],
    timeout_seconds: float,
) -> bytes:
    """下载 CDN 媒体并按需 AES 解密。"""
    if encrypted_query_param:
        raw = await _download_bytes(
            session,
            url=_cdn_download_url(cdn_base_url, encrypted_query_param),
            timeout_seconds=timeout_seconds,
        )
    elif full_url:
        assert_weixin_cdn_url(full_url)
        raw = await _download_bytes(session, url=full_url, timeout_seconds=timeout_seconds)
    else:
        raise RuntimeError("media item had neither encrypt_query_param nor full_url")
    if aes_key_b64:
        raw = _aes128_ecb_decrypt(raw, _parse_aes_key(aes_key_b64))
    return raw


def _mime_from_filename(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


# ======================================================================
# 入站消息解析
# ======================================================================

def guess_chat_type(message: Dict[str, Any], account_id: str) -> "tuple[str, str]":
    """判断消息是私聊还是群聊，返回 (chat_type, effective_chat_id)。"""
    room_id = str(message.get("room_id") or message.get("chat_room_id") or "").strip()
    to_user_id = str(message.get("to_user_id") or "").strip()
    is_group = bool(room_id) or (
        to_user_id and account_id and to_user_id != account_id and message.get("msg_type") == 1
    )
    if is_group:
        return "group", room_id or to_user_id or str(message.get("from_user_id") or "")
    return "dm", str(message.get("from_user_id") or "")


def extract_text(item_list: List[Dict[str, Any]]) -> str:
    """从 item_list 提取文本内容，处理引用消息与语音转写。"""
    for item in item_list:
        if item.get("type") == ITEM_TEXT:
            text = str((item.get("text_item") or {}).get("text") or "")
            ref = item.get("ref_msg") or {}
            ref_item = ref.get("message_item") or {}
            ref_type = ref_item.get("type")
            if ref_type in {ITEM_IMAGE, ITEM_VIDEO, ITEM_FILE, ITEM_VOICE}:
                title = ref.get("title") or ""
                prefix = f"[引用媒体: {title}]\n" if title else "[引用媒体]\n"
                return f"{prefix}{text}".strip()
            if ref_item:
                parts: List[str] = []
                if ref.get("title"):
                    parts.append(str(ref["title"]))
                ref_text = extract_text([ref_item])
                if ref_text:
                    parts.append(ref_text)
                if parts:
                    return f"[引用: {' | '.join(parts)}]\n{text}".strip()
            return text
    for item in item_list:
        if item.get("type") == ITEM_VOICE:
            voice_text = str((item.get("voice_item") or {}).get("text") or "")
            if voice_text:
                return voice_text
    return ""


# ======================================================================
# 文本格式化与分块
# ======================================================================

def _normalize_markdown_blocks(content: str) -> str:
    """压缩连续空行（保留代码块内部原样）。"""
    lines = content.splitlines()
    result: List[str] = []
    in_code_block = False
    blank_run = 0

    for raw_line in lines:
        line = raw_line.rstrip()
        if _FENCE_RE.match(line.strip()):
            in_code_block = not in_code_block
            result.append(line)
            blank_run = 0
            continue

        if in_code_block:
            result.append(line)
            continue

        if not line.strip():
            blank_run += 1
            if blank_run <= 1:
                result.append("")
            continue

        blank_run = 0
        result.append(line)

    return "\n".join(result).strip()


def _wrap_copy_friendly_lines_for_weixin(content: str) -> str:
    """对微信客户端里难以复制的超长行做软换行（代码块/表格除外）。"""
    if not content:
        return content

    wrapped: List[str] = []
    in_code_block = False

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if _FENCE_RE.match(stripped):
            in_code_block = not in_code_block
            wrapped.append(line)
            continue

        if (
            in_code_block
            or len(line) <= WEIXIN_COPY_LINE_WIDTH
            or not stripped
            or stripped.startswith("|")
            or _TABLE_RULE_RE.match(stripped)
        ):
            wrapped.append(line)
            continue

        wrapped_lines = textwrap.wrap(
            line,
            width=WEIXIN_COPY_LINE_WIDTH,
            break_long_words=False,
            break_on_hyphens=False,
            replace_whitespace=False,
            drop_whitespace=True,
        )
        wrapped.extend(wrapped_lines or [line])

    return "\n".join(wrapped).strip()


def format_message(content: Optional[str]) -> str:
    """出站文本格式化：压缩空行 + 复制友好软换行。"""
    if content is None:
        return ""
    return _wrap_copy_friendly_lines_for_weixin(_normalize_markdown_blocks(content))


def _split_markdown_blocks(content: str) -> List[str]:
    if not content:
        return []

    blocks: List[str] = []
    lines = content.splitlines()
    current: List[str] = []
    in_code_block = False

    for raw_line in lines:
        line = raw_line.rstrip()
        if _FENCE_RE.match(line.strip()):
            if not in_code_block and current:
                blocks.append("\n".join(current).strip())
                current = []
            current.append(line)
            in_code_block = not in_code_block
            if not in_code_block:
                blocks.append("\n".join(current).strip())
                current = []
            continue

        if in_code_block:
            current.append(line)
            continue

        if not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)

    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _split_delivery_units_for_weixin(content: str) -> List[str]:
    """把格式化内容拆成聊天友好的投递单元。

    顶层换行拆为独立消息；缩进延续行归入上一条（保护嵌套列表）；
    fenced code block 保持完整。
    """
    units: List[str] = []

    for block in _split_markdown_blocks(content):
        if _FENCE_RE.match(block.splitlines()[0].strip()):
            units.append(block)
            continue

        current: List[str] = []
        for raw_line in block.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                if current:
                    units.append("\n".join(current).strip())
                    current = []
                continue

            is_continuation = bool(current) and raw_line.startswith((" ", "\t"))
            if is_continuation:
                current.append(line)
                continue

            if current:
                units.append("\n".join(current).strip())
            current = [line]

        if current:
            units.append("\n".join(current).strip())

    return [unit for unit in units if unit]


def _looks_like_chatty_line_for_weixin(line: str) -> bool:
    """一行是否像独立的闲聊语句。"""
    stripped = line.strip()
    if not stripped:
        return False
    if len(stripped) > 48:
        return False
    if line.startswith((" ", "\t")):
        return False
    if stripped.startswith((">", "-", "*", "【", "#", "|")):
        return False
    if _TABLE_RULE_RE.match(stripped):
        return False
    if re.match(r"^\*\*[^*]+\*\*$", stripped):
        return False
    if re.match(r"^\d+\.\s", stripped):
        return False
    return True


def _looks_like_heading_line_for_weixin(line: str) -> bool:
    """短行是否表现为标题。"""
    stripped = line.strip()
    if not stripped:
        return False
    if _HEADER_RE.match(stripped):
        return True
    return len(stripped) <= 24 and stripped.endswith((":", "："))


def _should_split_short_chat_block_for_weixin(block: str) -> bool:
    """仅把闲聊风格的多行块拆成多个气泡。"""
    lines = [line for line in block.splitlines() if line.strip()]
    if not 2 <= len(lines) <= 6:
        return False
    if _looks_like_heading_line_for_weixin(lines[0]):
        return False
    return all(_looks_like_chatty_line_for_weixin(line) for line in lines)


def truncate_message(content: str, max_length: int) -> List[str]:
    """把超长单块截断为多段（保留代码栅栏，追加 (n/m) 指示）。

    替代 hermes ``BasePlatformAdapter.truncate_message`` 的自实现：
    优先在换行处断开；代码块被截断时在段尾闭合栅栏、下一段重新打开。
    """
    if len(content) <= max_length:
        return [content]

    parts: List[str] = []
    remaining = content
    fence_marker = ""
    while remaining:
        if len(remaining) <= max_length:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n", 0, max_length)
        if cut < max_length // 2:
            cut = max_length
        head, remaining = remaining[:cut], remaining[cut:].lstrip("\n")
        # 代码栅栏配对处理
        if head.count("```") % 2 == 1:
            head = f"{head}\n```"
            fence_open = re.search(r"```[^\n`]*", head)
            fence_marker = fence_open.group(0) if fence_open else "```"
            remaining = f"{fence_marker}\n{remaining}"
        parts.append(head)

    total = len(parts)
    if total > 1:
        parts = [f"{p}\n({i + 1}/{total})" for i, p in enumerate(parts)]
    return parts


def _pack_markdown_blocks_for_weixin(content: str, max_length: int) -> List[str]:
    """按 markdown 块打包到 max_length 以内，单块仍超长则截断。"""
    if len(content) <= max_length:
        return [content]

    packed: List[str] = []
    current = ""
    for block in _split_markdown_blocks(content):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= max_length:
            current = candidate
            continue
        if current:
            packed.append(current)
            current = ""
        if len(block) <= max_length:
            current = block
            continue
        packed.extend(truncate_message(block, max_length))
    if current:
        packed.append(current)
    return packed


def split_text_for_weixin_delivery(
    content: str, max_length: int, split_per_line: bool = False,
) -> List[str]:
    """把内容拆分为顺序发送的微信消息。

    *compact*（默认）：能放下就整条发，除非内容像短闲聊多行
    （拆成多个气泡更自然）；超长时按块打包。

    *per_line*（``split_per_line=True``）：顶层换行拆成独立消息，
    超长单元仍按块打包。由配置 ``split_multiline_messages`` 控制。
    """
    if not content:
        return []
    if split_per_line:
        if len(content) <= max_length and "\n" not in content:
            return [content]
        chunks: List[str] = []
        for unit in _split_delivery_units_for_weixin(content):
            if len(unit) <= max_length:
                chunks.append(unit)
                continue
            chunks.extend(_pack_markdown_blocks_for_weixin(unit, max_length))
        return [c for c in chunks if c] or [content]

    if len(content) <= max_length:
        return (
            [u for u in _split_delivery_units_for_weixin(content) if u]
            if _should_split_short_chat_block_for_weixin(content)
            else [content]
        )
    return _pack_markdown_blocks_for_weixin(content, max_length) or [content]
