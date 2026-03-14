"""网络工具实体 — 搜索、抓取、HTTP 请求。

整合了原 network 模块的 HTTP 请求能力，提供完整的 Web 访问工具集：
- web_search:         关键词搜索，返回摘要列表
- web_search_deep:    搜索 + 自动抓取正文，一步获取精准信息
- web_fetch:          抓取指定 URL 的可读正文
- web_request:        通用 HTTP 请求（GET/POST，自定义 Header）
- extract_page_links: 提取页面所有链接
"""

from __future__ import annotations

import json
from typing import Optional

from entities._sdk import tool, entity

entity("web", "网络工具 - 搜索引擎查询、网页正文抓取、HTTP 请求")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


# ==================================================================
# 搜索
# ==================================================================


@tool(name="web_search", group="web", tags=["web"])
def web_search(query: str, max_results: int = 5, region: str = "cn-zh") -> str:
    """通过 DuckDuckGo 搜索关键词，返回标题、URL 和摘要列表。

    适合快速了解话题概况或获取相关链接，若需要完整内容请配合 web_fetch 使用。

    Args:
        query:       搜索关键词，支持自然语言和高级语法（site:、filetype: 等）
        max_results: 最多返回条数，默认 5，最大 20
        region:      搜索区域语言代码，默认 cn-zh（中文），us-en（英文）
    """
    from duckduckgo_search import DDGS
    max_results = min(max(1, max_results), 20)
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results, region=region))
        items = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", "")[:300],
            }
            for r in raw
        ]
        return json.dumps({"query": query, "region": region, "total": len(items), "results": items}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"搜索失败: {e}"}, ensure_ascii=False)


@tool(name="web_search_deep", group="web", tags=["web"])
def web_search_deep(
    query: str,
    fetch_top: int = 3,
    max_chars_per_page: int = 4000,
    region: str = "cn-zh",
) -> str:
    """搜索关键词并自动抓取前 N 条结果的正文，一步获取精准信息。

    比单独调用 web_search 更深入：自动读取页面内容，适合需要准确答案的场景。

    Args:
        query:            搜索关键词
        fetch_top:        自动抓取前 N 条结果的正文，默认 3，最大 5
        max_chars_per_page: 每页正文最大字符数，默认 4000
        region:           区域语言代码，默认 cn-zh
    """
    from duckduckgo_search import DDGS
    fetch_top = min(max(1, fetch_top), 5)
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=fetch_top + 2, region=region))
    except Exception as e:
        return json.dumps({"error": f"搜索失败: {e}"}, ensure_ascii=False)

    if not raw:
        return json.dumps({"query": query, "results": [], "message": "无搜索结果"}, ensure_ascii=False)

    results = []
    fetched = 0
    for r in raw:
        url = r.get("href", "")
        title = r.get("title", "")
        snippet = r.get("body", "")[:200]

        content = None
        if fetched < fetch_top and url:
            try:
                import httpx
                with httpx.Client(timeout=10.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}) as client:
                    resp = client.get(url)
                ct = resp.headers.get("content-type", "")
                if "text/html" in ct:
                    content = _extract_text(resp.text, url, max_chars_per_page)
                fetched += 1
            except Exception:
                pass

        results.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "content": content,
        })

    return json.dumps({"query": query, "fetched_pages": fetched, "results": results}, ensure_ascii=False)


# ==================================================================
# 网页抓取
# ==================================================================


@tool(name="web_fetch", group="web", tags=["web"])
def web_fetch(
    url: str,
    extract_mode: str = "markdown",
    max_chars: int = 8000,
    timeout: int = 15,
) -> str:
    """获取指定 URL 的网页正文，自动提取可读内容。

    Args:
        url:          网页地址（必须以 http:// 或 https:// 开头）
        extract_mode: 输出格式：markdown（默认，保留结构）或 text（纯文本）
        max_chars:    最大返回字符数，默认 8000
        timeout:      超时秒数，默认 15
    """
    import httpx
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": f"仅支持 http/https，收到: {url[:50]}"}, ensure_ascii=False)

    try:
        with httpx.Client(timeout=float(timeout), follow_redirects=True, headers={"User-Agent": _USER_AGENT}) as client:
            resp = client.get(url)
    except httpx.TimeoutException:
        return json.dumps({"error": f"请求超时 ({timeout}s): {url}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"请求失败: {e}"}, ensure_ascii=False)

    ct = resp.headers.get("content-type", "")
    final_url = str(resp.url)

    if "application/json" in ct:
        from entities.web.content_extractor import truncate_text
        body, truncated = truncate_text(resp.text, max_chars)
        return json.dumps({"url": final_url, "content_type": "json", "content": body, "truncated": truncated}, ensure_ascii=False)

    if "text/" not in ct:
        return json.dumps({"url": final_url, "content_type": ct, "error": f"不支持的内容类型: {ct}"}, ensure_ascii=False)

    return _process_html(resp.text, final_url, extract_mode, max_chars)


@tool(name="extract_page_links", group="web", tags=["web"])
def extract_page_links(url: str, max_links: int = 50, timeout: int = 15) -> str:
    """提取指定网页中的所有超链接（URL + 链接文本）。

    Args:
        url:       要分析的网页 URL
        max_links: 最多返回链接数，默认 50
        timeout:   超时秒数，默认 15
    """
    import httpx
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "仅支持 http/https"}, ensure_ascii=False)

    try:
        with httpx.Client(timeout=float(timeout), follow_redirects=True, headers={"User-Agent": _USER_AGENT}) as client:
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


@tool(name="web_request", group="web", tags=["web"])
def web_request(
    url: str,
    method: str = "GET",
    body: str = "",
    headers: str = "",
    timeout: int = 15,
    max_chars: int = 5000,
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

    try:
        with httpx.Client(timeout=float(timeout), follow_redirects=True, headers=req_headers) as client:
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

        text = resp.text
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n... (响应过长，已截断)"

        return json.dumps({
            "status_code": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "body": text,
            "truncated": truncated,
        }, ensure_ascii=False)
    except httpx.TimeoutException:
        return json.dumps({"error": f"请求超时 ({timeout}s)"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==================================================================
# 内部工具
# ==================================================================


def _extract_text(html: str, url: str, max_chars: int) -> Optional[str]:
    """从 HTML 提取可读文本（BS4 预清洗 → Readability → 评分兜底）。"""
    from entities.web.content_extractor import (
        extract_readable_content,
        html_to_markdown,
        truncate_text,
        _bs4_to_text,
    )
    readable = extract_readable_content(html, url)
    if readable:
        _, content_html = readable
        text = html_to_markdown(content_html)
    else:
        text = _bs4_to_text(html)
    text, _ = truncate_text(text, max_chars)
    return text or None


def _process_html(html: str, url: str, extract_mode: str, max_chars: int) -> str:
    """处理 HTML：提取元数据 + 预清洗 + 正文提取 + 格式转换 + 截断。"""
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

    text, truncated = truncate_text(text, max_chars)

    result: dict = {
        "url": url,
        "extract_mode": extract_mode,
        "content": text,
        "truncated": truncated,
        "raw_length": len(html),
    }
    if title:
        result["title"] = title
    if meta.get("description"):
        result["description"] = meta["description"]
    if meta.get("author"):
        result["author"] = meta["author"]
    if meta.get("published"):
        result["published"] = meta["published"]
    return json.dumps(result, ensure_ascii=False)
