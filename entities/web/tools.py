"""网络工具实体 — 搜索、抓取、HTTP 请求。

搜索策略：优先使用百度高性能版（含 AI 总结），额度用尽自动降级到标准版。

提供完整的 Web 访问工具集：
- web_search:         搜索 + AI 总结，优先高性能版，自动降级
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

    try:
        with httpx.Client(
            timeout=float(timeout),
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            **_proxy_kwargs(use_proxy),
        ) as client:
            resp = client.get(url)
    except httpx.TimeoutException:
        return json.dumps({"error": f"请求超时 ({timeout}s): {url}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"请求失败: {e}"}, ensure_ascii=False)

    ct = resp.headers.get("content-type", "")
    final_url = str(resp.url)

    if "application/json" in ct or extract_mode == "raw":
        return _process_raw(resp.text, final_url, ct, start_index, max_chars)

    if "text/" not in ct:
        return json.dumps({"url": final_url, "content_type": ct, "error": f"不支持的内容类型: {ct}"}, ensure_ascii=False)

    return _process_html(resp.text, final_url, extract_mode, max_chars, start_index)


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

    try:
        with httpx.Client(
            timeout=float(timeout),
            follow_redirects=True,
            headers=req_headers,
            **_proxy_kwargs(use_proxy),
        ) as client:
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
