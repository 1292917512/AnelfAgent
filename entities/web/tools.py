"""网络工具实体 — 搜索、抓取、HTTP 请求。

搜索策略：优先使用百度高性能版（含 AI 总结），额度用尽自动降级到标准版。

提供完整的 Web 访问工具集：
- web_search:         搜索 + AI 总结，优先高性能版，自动降级
- web_fetch:          抓取指定 URL 的可读正文
- web_request:        通用 HTTP 请求（GET/POST，自定义 Header）
- extract_page_links: 提取页面所有链接
- web_download:       下载远程文件到本地 workspace（按需落盘）
"""

from __future__ import annotations

import json
from typing import Any, Optional, Tuple

from entities._sdk import tool, entity

entity("web", "网络工具 - 搜索引擎查询、网页正文抓取、HTTP 请求")

# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_WEB_CONFIGS = {
    "安全": {
        "web_ssrf_protection": {
            "description": "网络工具 SSRF 防护：拒绝访问回环/内网/链路本地等受限地址",
            "default": True,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_WEB_CONFIGS)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _proxy_kwargs(use_proxy: bool) -> dict[str, str]:
    """构建 httpx 代理参数。始终禁止读取环境变量代理，避免被 LLM 代理污染。"""
    if not use_proxy:
        return {"trust_env": False}
    from entities.web.baidu_search import get_proxy
    proxy = get_proxy()
    result: dict = {"trust_env": False}
    if proxy:
        result["proxy"] = proxy
    return result


# ==================================================================
# SSRF 防护
# ==================================================================

# 防护开启时响应体流式读取的字节上限
_SSRF_MAX_BODY_BYTES = 4 * 1024 * 1024


def _ssrf_protection_enabled() -> bool:
    """SSRF 防护开关（web_ssrf_protection，默认开）。"""
    try:
        from core.config import ConfigManager
        return bool(ConfigManager.get("web_ssrf_protection", True))
    except Exception:
        return True


def _check_ssrf_url(url: str) -> Optional[str]:
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


def _request_ssrf_checked(client: Any, method: str, url: str,
                          content: Optional[str] = None,
                          max_redirects: int = 5) -> Tuple[int, str, str, str]:
    """SSRF 防护模式请求：逐跳校验重定向目标，流式读取响应体并按字节上限截断。

    Returns:
        (status_code, final_url, content_type, body_text)
    """
    from urllib.parse import urljoin
    for _ in range(max_redirects):
        err = _check_ssrf_url(url)
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


# ==================================================================
# 搜索
# ==================================================================


@tool(name="web_search", group="web", tags=["web"], concurrency_safe=True)
def web_search(query: str, max_results: int = 8, search_recency: str = "") -> str:
    """搜索关键词并智能总结，返回 AI 总结和参考来源列表。

    自动搜索全网信息并生成结构化总结，同时提供原始参考链接。
    若需要某个页面的完整内容，请配合 web_fetch 使用。
    时间敏感的问题（比分、新闻、股价等）：query 中应显式包含当前日期/
    年份等时间词（如"2026年7月 世界杯决赛比分"），并配合 search_recency
    限定时间范围，避免搜出赛前预测等过时内容。

    Args:
        query:           搜索关键词，支持自然语言；时间敏感问题请显式写入日期/年份
        max_results:     最多返回条数，默认 8，最大 20
        search_recency:  时间过滤，可选 week/month/semiyear/year，默认不限；
                         注意指定后返回纯结果列表（无 AI 总结）
    """
    from entities.web.baidu_search import search_prefer_deep
    max_results = min(max(1, max_results), 20)
    try:
        result = search_prefer_deep(query, max_results, search_recency or None)
        refs = [
            {"title": r["title"], "url": r["url"], "snippet": r["snippet"]}
            for r in result["references"]
        ]
        output: dict = {
            "query": query,
            "sources": len(refs),
            "references": refs,
        }
        if result["summary"]:
            output["summary"] = result["summary"]
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"搜索失败: {e}"}, ensure_ascii=False)


# ==================================================================
# 网页抓取
# ==================================================================


@tool(name="web_fetch", group="web", tags=["web"], concurrency_safe=True)
def web_fetch(
    url: str,
    extract_mode: str = "markdown",
    max_chars: int = 8000,
    timeout: int = 15,
    use_proxy: bool = False,
    start_index: int = 0,
) -> str:
    """获取指定 URL 的网页正文，自动提取可读内容。

    长页面会按 max_chars 截断；返回 truncated=true 时，
    可用 start_index 传回 next_start_index 继续分块读取后续内容。

    Args:
        url:          网页地址（必须以 http:// 或 https:// 开头）
        extract_mode: 输出格式：markdown（默认，保留结构）、text（纯文本）或 raw（原始内容，不提取正文）
        max_chars:    最大返回字符数，默认 8000
        timeout:      超时秒数，默认 15
        use_proxy:    是否使用代理，默认 False
        start_index:  从该字符索引开始返回，默认 0，用于长页面分块续读
    """
    import httpx
    max_chars = int(max_chars)
    start_index = max(0, int(start_index))
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": f"仅支持 http/https，收到: {url[:50]}"}, ensure_ascii=False)

    ssrf = _ssrf_protection_enabled()
    if ssrf:
        err = _check_ssrf_url(url)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)

    try:
        with httpx.Client(
            timeout=float(timeout),
            follow_redirects=not ssrf,
            headers={"User-Agent": _USER_AGENT},
            **_proxy_kwargs(use_proxy),
        ) as client:
            if ssrf:
                status, final_url, ct, body = _request_ssrf_checked(client, "GET", url)
            else:
                resp = client.get(url)
                final_url = str(resp.url)
                ct = resp.headers.get("content-type", "")
                body = resp.text
    except httpx.TimeoutException:
        return json.dumps({"error": f"请求超时 ({timeout}s): {url}"}, ensure_ascii=False)
    except PermissionError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"请求失败: {e}"}, ensure_ascii=False)

    if "application/json" in ct or extract_mode == "raw":
        return _process_raw(body, final_url, ct, start_index, max_chars)

    if "text/" not in ct:
        return json.dumps({"url": final_url, "content_type": ct, "error": f"不支持的内容类型: {ct}"}, ensure_ascii=False)

    return _process_html(body, final_url, extract_mode, max_chars, start_index)


@tool(name="extract_page_links", group="web", tags=["web"], concurrency_safe=True)
def extract_page_links(
    url: str,
    max_links: int = 50,
    timeout: int = 15,
    use_proxy: bool = False,
) -> str:
    """提取指定网页中的所有超链接（URL + 链接文本）。

    Args:
        url:       要分析的网页 URL
        max_links: 最多返回链接数，默认 50
        timeout:   超时秒数，默认 15
        use_proxy: 是否使用代理，默认 False
    """
    import httpx
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "仅支持 http/https"}, ensure_ascii=False)

    try:
        with httpx.Client(
            timeout=float(timeout),
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            **_proxy_kwargs(use_proxy),
        ) as client:
            resp = client.get(url)
        ct = resp.headers.get("content-type", "")
        if "text/html" not in ct:
            return json.dumps({"error": f"非 HTML 页面: {ct}"}, ensure_ascii=False)
        from entities.web.content_extractor import extract_links
        final_url = str(resp.url)
        links = extract_links(resp.text, base_url=final_url)
        total = len(links)
        return json.dumps({"url": final_url, "total_links": total, "returned": min(total, max_links), "links": links[:max_links]}, ensure_ascii=False)
    except httpx.TimeoutException:
        return json.dumps({"error": f"请求超时 ({timeout}s)"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"请求失败: {e}"}, ensure_ascii=False)


# ==================================================================
# 通用 HTTP 请求
# ==================================================================


@tool(name="web_request", group="web", tags=["web"], concurrency_safe=True)
def web_request(
    url: str,
    method: str = "GET",
    body: str = "",
    headers: str = "",
    timeout: int = 15,
    max_chars: int = 5000,
    use_proxy: bool = False,
) -> str:
    """发送 HTTP 请求，返回状态码和响应体。

    适合调用 API、表单提交等场景。

    Args:
        url:       完整 URL
        method:    HTTP 方法，GET（默认）、POST、PUT、DELETE、PATCH
        body:      请求体（POST/PUT 时使用，JSON 字符串）
        headers:   额外请求头（JSON 格式，如 {\"Authorization\": \"Bearer token\"}）
        timeout:   超时秒数，默认 15
        max_chars: 响应体最大字符数，默认 5000
        use_proxy: 是否使用代理，默认 False
    """
    import httpx
    method = method.upper()
    req_headers = {"User-Agent": _USER_AGENT}
    if headers.strip():
        try:
            req_headers.update(json.loads(headers))
        except Exception:
            return json.dumps({"error": f"headers JSON 解析失败: {headers}"}, ensure_ascii=False)

    req_body: Optional[str] = body.strip() or None

    ssrf = _ssrf_protection_enabled()
    if ssrf:
        err = _check_ssrf_url(url)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)

    try:
        with httpx.Client(
            timeout=float(timeout),
            follow_redirects=not ssrf,
            headers=req_headers,
            **_proxy_kwargs(use_proxy),
        ) as client:
            if ssrf:
                status_code, final_url, ct, text = _request_ssrf_checked(
                    client, method, url, content=req_body)
            else:
                if method == "GET":
                    resp = client.get(url)
                elif method == "POST":
                    resp = client.post(url, content=req_body)
                elif method == "PUT":
                    resp = client.put(url, content=req_body)
                elif method == "DELETE":
                    resp = client.delete(url)
                elif method == "PATCH":
                    resp = client.patch(url, content=req_body)
                else:
                    return json.dumps({"error": f"不支持的方法: {method}"}, ensure_ascii=False)
                status_code = resp.status_code
                ct = resp.headers.get("content-type", "")
                text = resp.text

        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n... (响应过长，已截断)"

        return json.dumps({
            "status_code": status_code,
            "content_type": ct,
            "body": text,
            "truncated": truncated,
        }, ensure_ascii=False)
    except httpx.TimeoutException:
        return json.dumps({"error": f"请求超时 ({timeout}s)"}, ensure_ascii=False)
    except PermissionError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==================================================================
# 文件下载
# ==================================================================


def _download_to_file_checked(
    client: Any,
    url: str,
    dest_path: str,
    max_bytes: int,
    ssrf: bool,
    max_redirects: int = 5,
) -> Tuple[int, str, int]:
    """流式下载 URL 到本地文件，逐跳校验重定向目标（SSRF 开启时），按字节上限截断。

    Returns:
        (status_code, final_url, written_bytes)
    """
    import os
    from urllib.parse import urljoin
    for _ in range(max_redirects):
        if ssrf:
            err = _check_ssrf_url(url)
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
                    pass
                raise ValueError(f"文件超过大小限制 ({max_bytes // 1024 // 1024}MB)")
            return resp.status_code, str(resp.url), written
    raise ValueError(f"重定向次数过多 (上限 {max_redirects})")


@tool(name="web_download", group="web",
      tags=["web", "media:file", "media:video", "media:audio", "media:voice"],
      concurrency_safe=True)
def web_download(
    url: str,
    filename: str = "",
    max_mb: int = 50,
    timeout: int = 30,
    use_proxy: bool = False,
) -> str:
    """下载远程文件到本地 workspace/uploads/file/，返回本地路径。

    适用于频道消息中标记为「未下载」且标签带有 URL 的媒体/文件，
    或任何需要落地后再用 read_file 等工具分析的远程文件。
    若标签带的是 file_id 而非 URL（如 QQ 文件），请改用 qq_download_file。

    Args:
        url:       文件地址（必须以 http:// 或 https:// 开头）
        filename:  期望保存的文件名（可选，默认从 URL 推断）
        max_mb:    允许的最大文件大小（MB），默认 50
        timeout:   超时秒数，默认 30
        use_proxy: 是否使用代理，默认 False
    """
    import os
    import time
    import uuid
    from urllib.parse import unquote, urlparse

    import httpx

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": f"仅支持 http/https，收到: {url[:50]}"}, ensure_ascii=False)
    max_bytes = max(1, int(max_mb)) * 1024 * 1024

    ssrf = _ssrf_protection_enabled()
    if ssrf:
        err = _check_ssrf_url(url)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)

    try:
        from core.config import ConfigManager
        ws = ConfigManager.get("workspace_root", "workspace")
    except Exception:
        ws = "workspace"
    dl_dir = os.path.abspath(os.path.join(ws, "uploads", "file"))
    os.makedirs(dl_dir, exist_ok=True)

    name = os.path.basename(filename.strip()) if filename.strip() else ""
    if not name:
        name = os.path.basename(unquote(urlparse(url).path)) or "download.bin"
    local_path = os.path.join(
        dl_dir, f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}_{name}")
    if not os.path.realpath(local_path).startswith(os.path.realpath(dl_dir) + os.sep):
        return json.dumps({"error": f"非法文件名: {filename}"}, ensure_ascii=False)

    try:
        with httpx.Client(
            timeout=float(timeout),
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT},
            **_proxy_kwargs(use_proxy),
        ) as client:
            _, final_url, written = _download_to_file_checked(
                client, url, local_path, max_bytes, ssrf)
    except httpx.TimeoutException:
        return json.dumps({"error": f"下载超时 ({timeout}s): {url}"}, ensure_ascii=False)
    except PermissionError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"下载失败: {e}"}, ensure_ascii=False)

    return json.dumps({
        "path": local_path,
        "name": os.path.basename(local_path),
        "size": written,
        "source_url": final_url,
        "hint": "文件已下载，可用 read_file 读取该路径进行分析",
    }, ensure_ascii=False)


# ==================================================================
# 内部工具
# ==================================================================


def _process_raw(body: str, url: str, content_type: str, start_index: int, max_chars: int) -> str:
    """原始内容直通（JSON 响应 / raw 模式）：分块切片 + 截断。"""
    from entities.web.content_extractor import truncate_text

    body = body[start_index:]
    body, truncated = truncate_text(body, max_chars)
    result: dict = {"url": url, "content_type": content_type, "content": body, "truncated": truncated}
    if truncated:
        result["next_start_index"] = start_index + len(body)
    return json.dumps(result, ensure_ascii=False)


def _process_html(html: str, url: str, extract_mode: str, max_chars: int, start_index: int = 0) -> str:
    """处理 HTML：提取元数据 + 预清洗 + 正文提取 + 格式转换 + 分块截断。"""
    from entities.web.content_extractor import (
        extract_page_metadata,
        extract_readable_content,
        html_to_markdown,
        truncate_text,
        _bs4_to_text,
        _simple_html_to_text,
    )

    meta = extract_page_metadata(html, url)

    title: Optional[str] = meta.get("title")
    content_html = html

    readable = extract_readable_content(html, url)
    if readable:
        title = title or readable[0]
        content_html = readable[1]

    if extract_mode == "markdown":
        text = html_to_markdown(content_html)
    else:
        text = _bs4_to_text(content_html) or _simple_html_to_text(content_html)

    text = text[start_index:]
    text, truncated = truncate_text(text, max_chars)

    result: dict = {
        "url": url,
        "extract_mode": extract_mode,
        "content": text,
        "truncated": truncated,
        "raw_length": len(html),
    }
    if truncated:
        result["next_start_index"] = start_index + len(text)
    if title:
        result["title"] = title
    if meta.get("description"):
        result["description"] = meta["description"]
    if meta.get("author"):
        result["author"] = meta["author"]
    if meta.get("published"):
        result["published"] = meta["published"]
    return json.dumps(result, ensure_ascii=False)
