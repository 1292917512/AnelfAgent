"""单聊/群聊富媒体上传。预签名 PUT 不携带机器人凭证。"""

import asyncio
import base64
import hashlib
import io
import mimetypes
import os
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit
from urllib.request import url2pathname

import httpx
from PIL import Image

from agent.channel.utils.media import resolve_local_file_path
from core.path import ConfigPaths, parse_upload_url

from .client import QQAPI, QQAPIError
from .events import Target

_LIMIT = 200 * 1024 * 1024
_DIRECT_LIMIT = 5 * 1024 * 1024
_INLINE_LIMIT = 20 * 1024 * 1024


def local_path(source: str) -> Path:
    upload = parse_upload_url(source)
    if upload:
        root = Path(ConfigPaths.UPLOAD_DIR).resolve()
        path = root.joinpath(*upload).resolve()
        if not path.is_relative_to(root):
            raise ValueError("无效的 Anelf 上传文件路径")
    elif source.startswith("file://"):
        uri = urlsplit(source)
        if uri.netloc not in ("", "localhost") or uri.path.startswith("//"):
            raise ValueError("file URL 仅支持本机文件")
        path = Path(url2pathname(uri.path))
    else:
        path = Path(resolve_local_file_path(source))
    if not path.is_file() or not 0 < path.stat().st_size <= _LIMIT:
        raise ValueError("需要非空的本地常规文件，最大 200 MiB")
    return path


def _inline_media(source: str, file_type: int) -> tuple[str, bytes]:
    mime = ""
    if source.startswith("data:"):
        header, sep, encoded = source.partition(",")
        if not sep or not header.endswith(";base64") or len(header) > 128:
            raise ValueError("媒体 data URI 需要 Base64 编码和有效的 MIME 类型")
        mime = header[5:-7].split(";", 1)[0].lower()
    else:
        encoded = source.removeprefix("base64://")
    if not encoded or len(encoded) > 4 * ((_INLINE_LIMIT + 2) // 3):
        raise ValueError("内联媒体为空或超过 20 MiB，请使用文件路径")
    raw = base64.b64decode(encoded, validate=True)
    if not raw or len(raw) > _INLINE_LIMIT:
        raise ValueError("无效的内联媒体大小")
    if file_type == 1:
        with Image.open(io.BytesIO(raw)) as img:
            mime = Image.MIME.get(img.format or "", "")
    ext = mimetypes.guess_extension(mime) or {1: ".png", 2: ".mp4", 3: ".silk", 4: ".bin"}[file_type]
    return "media" + ext, raw


def guild_image(source: str) -> tuple[str, bytes]:
    if source.startswith(("data:", "base64://")):
        return _inline_media(source, 1)
    path = local_path(source)
    with path.open("rb") as stream:
        if os.fstat(stream.fileno()).st_size > _INLINE_LIMIT:
            raise ValueError("频道本地图片最大 20 MiB")
        data = stream.read(_INLINE_LIMIT + 1)
    if not 0 < len(data) <= _INLINE_LIMIT:
        raise ValueError("无效的频道图片大小")
    return path.name, data


def _upload_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None:
        raise ValueError("QQ 预签名上传地址无效")
    return url


async def _put_part(api: QQAPI, url: str, chunk: bytes, delay: float, content_type: str = "") -> httpx.Response:
    """同一预签名地址可重复 PUT 相同字节，不重放最终消息。"""
    for attempt in range(3):
        try:
            response = await api.http.put(url, content=chunk, headers={"Content-Type": content_type} if content_type else {})
        except httpx.TransportError:
            if attempt == 2:
                raise RuntimeError("QQ 分片 PUT 连接失败") from None
        else:
            if response.is_success:
                return response
            if response.status_code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"QQ 分片 PUT HTTP {response.status_code}")
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")


def _hashes(stream: BinaryIO, size: int) -> dict[str, str]:
    md5, sha1, prefix = hashlib.md5(), hashlib.sha1(), hashlib.md5()
    prefix_remaining = 10_002_432
    remaining = size
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        if not chunk:
            raise OSError("上传时文件长度发生变化")
        md5.update(chunk)
        sha1.update(chunk)
        prefix.update(chunk[:prefix_remaining])
        prefix_remaining = max(0, prefix_remaining - len(chunk))
        remaining -= len(chunk)
    if stream.read(1):
        raise OSError("上传时文件长度发生变化")
    stream.seek(0)
    return {"md5": md5.hexdigest(), "sha1": sha1.hexdigest(), "md5_10m": prefix.hexdigest()}


async def _upload_parts(api: QQAPI, base: str, fields: dict[str, Any],
                        stream: BinaryIO, size: int) -> str:
    hashes = await asyncio.to_thread(_hashes, stream, size)
    plan = await api.request("POST", base + "/upload_prepare",
                             json={**fields, **hashes, "file_size": str(size)})
    upload_id = plan.get("upload_id")
    block_size = int(plan.get("block_size") or 0)
    parts = plan.get("parts")
    if not upload_id or block_size <= 0 or not isinstance(parts, list) or not parts:
        raise RuntimeError("QQ 分片上传计划不完整")
    delay = min(5.0, max(0.0, float((plan.get("upload_config") or {}).get("retry_delay", 1))))
    chunks: list[tuple[str, int]] = []
    remaining = size
    for index, part in enumerate(sorted(parts, key=lambda p: int(p["index"]))):
        url = _upload_url(part["presigned_url"])
        length = int(part.get("block_size", min(block_size, remaining)))
        if int(part["index"]) != index or not 0 < length <= remaining:
            raise RuntimeError("QQ 分片上传计划序号或长度无效")
        chunks.append((url, length))
        remaining -= length
    if remaining:
        raise RuntimeError("QQ 分片上传计划未覆盖整个文件")
    for index, (url, length) in enumerate(chunks):
        chunk = await asyncio.to_thread(stream.read, length)
        if len(chunk) != length:
            raise OSError("上传时文件长度发生变化")
        await _put_part(api, url, chunk, delay)
        finish = {"upload_id": upload_id, "part_index": index, "block_size": str(length),
                  "md5": hashlib.md5(chunk).hexdigest()}
        for attempt in range(3):
            try:
                await api.request("POST", base + "/upload_part_finish", json=finish)
                break
            except QQAPIError as exc:
                if exc.code != "40093001" or attempt == 2:
                    raise
                await asyncio.sleep(delay)
    return str(upload_id)


async def upload_media(api: QQAPI, target: Target, source: str, file_type: int) -> str:
    fields: dict[str, Any] = {"file_type": file_type}
    if source.startswith(("https://", "http://")):
        fields["url"] = source
    else:
        stream: BinaryIO
        if source.startswith(("data:", "base64://")):
            name, raw = await asyncio.to_thread(_inline_media, source, file_type)
            fields["file_name"] = name
            stream = io.BytesIO(raw)
            size = len(raw)
        else:
            path = local_path(source)
            fields["file_name"] = path.name
            stream = path.open("rb")
            size = os.fstat(stream.fileno()).st_size
        with stream:
            if not 0 < size <= _LIMIT:
                raise ValueError("QQ 上传文件大小应在 200 MiB 以内")
            if size < _DIRECT_LIMIT:
                data = await asyncio.to_thread(stream.read, _DIRECT_LIMIT)
                if len(data) != size:
                    raise OSError("上传时文件长度发生变化")
                fields["file_data"] = base64.b64encode(data).decode("ascii")
            else:
                fields["upload_id"] = await _upload_parts(api, target.base, fields, stream, size)
    result = await api.request("POST", target.base + "/files", json={**fields, "srv_send_msg": False})
    file_info = result.get("file_info")
    if not isinstance(file_info, str) or not file_info:
        raise RuntimeError("QQ 上传没有返回 file_info")
    return file_info


def _image_material(path: Path) -> tuple[bytes, str]:
    formats = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif", ".webp": "webp"}
    fmt = formats.get(path.suffix.lower())
    limit = 10 * 1024 * 1024
    if not fmt or path.stat().st_size > limit:
        raise ValueError("Markdown 素材需要 10 MiB 以内的 PNG、JPEG、GIF 或 WebP 图片")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("图片超过 10 MiB")
    with Image.open(io.BytesIO(data)) as image:
        if (image.format or "").lower() != fmt:
            raise ValueError("素材图片格式与文件扩展名不符")
        image.verify()
    return data, "image/" + fmt


async def upload_markdown_image(api: QQAPI, source: str) -> dict[str, Any]:
    path = local_path(source)
    data, mime = await asyncio.to_thread(_image_material, path)
    digest = hashlib.md5(data).hexdigest()
    plan = await api.request("POST", "/v2/media-images/prepare", json={
        "file_name": path.name, "file_size": len(data), "content_type": mime, "md5": digest,
        "sha1": hashlib.sha1(data).hexdigest(),
    })
    if not plan.get("upload_id"):
        raise RuntimeError("QQ 未返回素材上传任务 ID")
    result = await _put_part(api, _upload_url(plan["presigned_url"]), data, 1, mime)
    if result.headers.get("etag", "").strip('"').lower() != digest:
        raise RuntimeError("QQ 素材上传 ETag 与图片 MD5 不符")
    material = await api.request("POST", "/v2/media-images/complete", json={"upload_id": plan["upload_id"], "md5": digest})
    if not material.get("image_id") or not str(material.get("url") or "").startswith("qqbot-media://image/"):
        raise RuntimeError("QQ 未返回可用的 Markdown 素材引用")
    return {k: material[k] for k in ("image_id", "url", "ttl") if k in material}
