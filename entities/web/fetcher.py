"""网页直连抓取设施：SSRF 防护、受限读取、正文提取。

builtin 读取提供者的实现底座，同时供 web_request / extract_page_links /
web_download 工具复用（这些工具只支持直连语义，不经提供者矩阵）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.config import ConfigManager
from core.log import log

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# SSRF 防护开启时响应体流式读取的字节上限
_SSRF_MAX_BODY_BYTES = 4 * 1024 * 1024


class RobotsDisallowed(Exception):
    """robots.txt 合规检查未通过。"""


def proxy_kwargs(use_proxy: bool) -> Dict[str, str]:
    """构建 httpx 代理参数。始终禁止读取环境变量代理，避免被 LLM 代理污染。"""
    if not use_proxy:
        return {"trust_env": False}
    from entities.web.web_config import get_proxy
    result: Dict[str, str] = {"trust_env": False}
    proxy = get_proxy()
    if proxy:
        result["proxy"] = proxy
    return result


def ssrf_protection_enabled() -> bool:
    """SSRF 防护开关（web_ssrf_protection，默认开）。"""
    try:
        return bool(ConfigManager.get("web_ssrf_protection", True))
    except Exception:
        return True


def check_ssrf_url(url: str) -> Optional[str]:
    """SSRF 检查：解析目标 host 的 IP，拒绝回环/内网/链路本地等受限地址。

    Returns:
        拦截原因，未拦截返回 None
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse
    host = urlparse(url).hostname
    if not host:
        return "URL 缺少主机名"
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        return f"DNS 解析失败: {host}: {e}"
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return f"SSRF 防护拦截: {host} 解析到受限地址 {ip_str}"
    return None


def _read_body_limited(resp: Any, max_bytes: int = _SSRF_MAX_BODY_BYTES) -> str:
    """流式读取响应体，按字节上限截断后解码（避免先全量加载到内存）。"""
    buf = bytearray()
    for chunk in resp.iter_bytes(65536):
        buf.extend(chunk)
        if len(buf) > max_bytes:
            break
    return bytes(buf[:max_bytes]).decode(resp.encoding or "utf-8", errors="replace")


def fetch(
    url: str,
    *,
    method: str = "GET",
    content: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 15.0,
    use_proxy: bool = False,
    max_redirects: int = 5,
) -> Tuple[int, str, str, str]:
    """直连请求（SSRF 开启时逐跳校验重定向、流式读取按字节上限截断）。

    Returns:
        (status_code, final_url, content_type, body_text)
    """
    import httpx
    ssrf = ssrf_protection_enabled()
    if ssrf:
        err = check_ssrf_url(url)
        if err:
            raise PermissionError(err)
    req_headers = {"User-Agent": _USER_AGENT}
    if headers:
        req_headers.update(headers)
    with httpx.Client(
        timeout=float(timeout),
        follow_redirects=not ssrf,
        headers=req_headers,
        **proxy_kwargs(use_proxy),
    ) as client:
        if not ssrf:
            resp = client.request(method, url, content=content)
            return resp.status_code, str(resp.url), resp.headers.get("content-type", ""), resp.text
        from urllib.parse import urljoin
        for _ in range(max_redirects):
            err = check_ssrf_url(url)
            if err:
                raise PermissionError(err)
            with client.stream(method, url, content=content, follow_redirects=False) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location", "")
                    if not location:
                        raise ValueError(f"重定向响应缺少 Location: {url}")
                    url = urljoin(str(resp.url), location)
                    if resp.status_code in (301, 302, 303):
                        method, content = "GET", None
                    continue
                body = _read_body_limited(resp)
                return resp.status_code, str(resp.url), resp.headers.get("content-type", ""), body
    raise ValueError(f"重定向次数过多 (上限 {max_redirects})")


def download_to_file(
    url: str,
    dest_path: str,
    max_bytes: int,
    *,
    timeout: float = 30.0,
    use_proxy: bool = False,
    max_redirects: int = 5,
) -> Tuple[int, str, int]:
    """流式下载 URL 到本地文件（SSRF 开启时逐跳校验），按字节上限截断。

    Returns:
        (status_code, final_url, written_bytes)
    """
    import os
    from urllib.parse import urljoin

    import httpx
    ssrf = ssrf_protection_enabled()
    if ssrf:
        err = check_ssrf_url(url)
        if err:
            raise PermissionError(err)
    with httpx.Client(
        timeout=float(timeout),
        follow_redirects=False,
        headers={"User-Agent": _USER_AGENT},
        **proxy_kwargs(use_proxy),
    ) as client:
        for _ in range(max_redirects):
            if ssrf:
                err = check_ssrf_url(url)
                if err:
                    raise PermissionError(err)
            with client.stream("GET", url, follow_redirects=False) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location", "")
                    if not location:
                        raise ValueError(f"重定向响应缺少 Location: {url}")
                    url = urljoin(str(resp.url), location)
                    continue
                if resp.status_code >= 400:
                    raise ValueError(f"HTTP {resp.status_code}: {url}")
                content_length = resp.headers.get("content-length", "")
                if content_length.isdigit() and int(content_length) > max_bytes:
                    raise ValueError(f"文件超过大小限制 ({max_bytes // 1024 // 1024}MB)")
                written = 0
                overflow = False
                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_bytes(65536):
                        written += len(chunk)
                        if written > max_bytes:
                            overflow = True
                            break
                        f.write(chunk)
                if overflow:
                    try:
                        os.remove(dest_path)
                    except OSError:
                        log("download_to_file 超限临时文件清理失败", "DEBUG")
                    raise ValueError(f"文件超过大小限制 ({max_bytes // 1024 // 1024}MB)")
                return resp.status_code, str(resp.url), written
    raise ValueError(f"重定向次数过多 (上限 {max_redirects})")


def read_page(
    url: str,
    *,
    timeout: int = 15,
    extract_mode: str = "markdown",
    use_proxy: bool = False,
    respect_robots: bool = False,
) -> Dict[str, Any]:
    """直连抓取并提取可读正文（builtin 读取提供者实现），返回完整内容不分块。

    分块截断（start_index/max_chars）由工具层统一施加，与提供者无关。
    """
    if respect_robots:
        from entities.web.robots import is_allowed
        allowed, detail = is_allowed(url, use_proxy=use_proxy)
        if not allowed:
            raise RobotsDisallowed(f"robots.txt 合规检查未通过: {detail}")

    _status, final_url, content_type, body = fetch(
        url, timeout=float(timeout), use_proxy=use_proxy)

    if "application/json" in content_type or extract_mode == "raw":
        return {
            "url": final_url,
            "content_type": content_type,
            "extract_mode": "raw",
            "content": body,
            "raw_length": len(body),
        }

    if "text/" not in content_type:
        raise ValueError(f"不支持的内容类型: {content_type}")

    from entities.web.content_extractor import (
        _bs4_to_text,
        _simple_html_to_text,
        extract_page_metadata,
        extract_readable_content,
        html_to_markdown,
    )

    meta = extract_page_metadata(body, final_url)
    title: Optional[str] = meta.get("title")
    content_html = body
    readable = extract_readable_content(body, final_url)
    if readable:
        title = title or readable[0]
        content_html = readable[1]

    if extract_mode == "markdown":
        text = html_to_markdown(content_html)
    else:
        text = _bs4_to_text(content_html) or _simple_html_to_text(content_html)

    result: Dict[str, Any] = {
        "url": final_url,
        "extract_mode": extract_mode,
        "content": text,
        "raw_length": len(body),
    }
    if title:
        result["title"] = title
    for field in ("description", "author", "published"):
        if meta.get(field):
            result[field] = meta[field]
    return result
