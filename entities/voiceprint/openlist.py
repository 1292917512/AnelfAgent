"""OpenList（AList 系）远程目录客户端：列目录 + 下载文件。

对接 OpenList API（供 NAS 未本地挂载时经 HTTP 访问音频目录）：
    POST {endpoint}/api/fs/list   {path, page, per_page}   → 目录清单
    POST {endpoint}/api/fs/get    {path}                   → 文件详情（raw_url 下载直链）
    备选下载：GET {endpoint}/d{path}（OpenList 直链约定）
鉴权：Authorization 头携带 voiceprint_openlist_token。
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import httpx

from core.config import get_config


class OpenListError(RuntimeError):
    """OpenList 调用失败（未配置/网络/响应异常）。"""


def _endpoint() -> str:
    endpoint = str(get_config("voiceprint_openlist_endpoint", "") or "").strip().rstrip("/")
    if not endpoint:
        raise OpenListError("未配置 OpenList 地址（voiceprint_openlist_endpoint）")
    return endpoint


def _headers() -> Dict[str, str]:
    token = str(get_config("voiceprint_openlist_token", "") or "").strip()
    return {"Authorization": token} if token else {}


def is_configured() -> bool:
    """OpenList 访问是否已配置。"""
    return bool(str(get_config("voiceprint_openlist_endpoint", "") or "").strip())


def configured_root() -> str:
    """配置的 OpenList 监听根路径。"""
    return str(get_config("voiceprint_openlist_path", "/") or "/").strip() or "/"


async def check_status() -> Dict[str, Any]:
    """OpenList 连通性体检：配置状态 + 可达性 + 令牌有效性 + 延迟。"""
    if not is_configured():
        return {"configured": False, "reachable": False, "latency_ms": 0, "error": ""}
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await _post(client, "/api/fs/list", {
                "path": configured_root(), "page": 1, "per_page": 1, "refresh": False,
            })
        return {
            "configured": True,
            "reachable": True,
            "latency_ms": int((time.monotonic() - start) * 1000),
            "error": "",
        }
    except OpenListError as exc:
        return {
            "configured": True,
            "reachable": False,
            "latency_ms": int((time.monotonic() - start) * 1000),
            "error": str(exc),
        }


async def _post(client: httpx.AsyncClient, api: str, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        resp = await client.post(f"{_endpoint()}{api}", json=body, headers=_headers())
    except httpx.HTTPError as exc:
        raise OpenListError(f"OpenList 不可达: {exc}") from exc
    if resp.status_code != 200:
        raise OpenListError(f"OpenList {api} 返回 {resp.status_code}: {resp.text[:200]}")
    payload = resp.json()
    if payload.get("code") != 200:
        raise OpenListError(f"OpenList {api} 错误: {payload.get('message', 'unknown')}")
    return payload.get("data") or {}


def _entry_mtime_ns(item: Dict[str, Any]) -> int:
    """解析 OpenList 条目的 modified（ISO8601）为纳秒。"""
    modified = item.get("modified")
    if not modified:
        return 0
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(
            str(modified).replace("Z", "+00:00")).timestamp() * 1e9)
    except (ValueError, TypeError):
        return 0


async def list_dir(path: str) -> Dict[str, List[Dict[str, Any]]]:
    """列出单层目录内容（分页拉全）。

    Returns:
        {
            "dirs": [{"path": 完整路径, "mtime_ns": int}],
            "files": [{"path": 完整路径, "name": str, "size": int, "mtime_ns": int}],
        }
    """
    dirs: List[Dict[str, Any]] = []
    files: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        page = 1
        while True:
            data = await _post(client, "/api/fs/list", {
                "path": path, "page": page, "per_page": 200, "refresh": False,
            })
            content: List[Dict[str, Any]] = data.get("content") or []
            for item in content:
                name = str(item.get("name", ""))
                child = f"{path.rstrip('/')}/{name}"
                if item.get("is_dir"):
                    dirs.append({"path": child, "mtime_ns": _entry_mtime_ns(item)})
                else:
                    files.append({
                        "path": child, "name": name,
                        "size": int(item.get("size") or 0),
                        "mtime_ns": _entry_mtime_ns(item),
                    })
            if len(content) < 200:
                break
            page += 1
    return {"dirs": dirs, "files": files}


async def download(remote_path: str, dest_dir: Optional[str] = None) -> str:
    """下载远程文件到本地临时目录，返回本地路径（调用方负责删除）。"""
    import tempfile

    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        raw_url = ""
        try:
            data = await _post(client, "/api/fs/get", {"path": remote_path})
            raw_url = str(data.get("raw_url", ""))
        except OpenListError:
            raw_url = ""
        if not raw_url:
            raw_url = f"{_endpoint()}/d{remote_path}"

        try:
            resp = await client.get(raw_url, headers=_headers())
        except httpx.HTTPError as exc:
            raise OpenListError(f"OpenList 下载失败: {exc}") from exc
        if resp.status_code != 200:
            raise OpenListError(f"OpenList 下载返回 {resp.status_code}")

        suffix = os.path.splitext(remote_path)[1] or ".bin"
        fd, local_path = tempfile.mkstemp(
            prefix="voiceprint_dl_", suffix=suffix, dir=dest_dir)
        with os.fdopen(fd, "wb") as f:
            f.write(resp.content)
        return local_path
