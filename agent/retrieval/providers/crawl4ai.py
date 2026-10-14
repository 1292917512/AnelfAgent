"""Crawl4AI 本地服务的网页读取与动态抓取适配。

模型可见：渲染后的正文、页面元信息及有界链接列表；目标错误与服务错误分开归因。
Token 影响：正文在工具层分块，链接最多 30 条，不返回 HTML 与 Markdown 的重复副本。
缓存影响：仅工具结果，不改变稳定前缀；服务端默认绕过页面缓存，避免读取过期内容。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from core.config import register_configs_safe
from core.network import ProxyMode
from core.sanitizer import sanitize_text

from .selfhosted import SelfHostedProvider, ServiceError, validate_service_url


class Crawl4AIProvider(SelfHostedProvider):
    """调用官方 /crawl 服务，提供浏览器渲染而不承担浏览器进程管理。"""

    name = "crawl4ai"
    display_name = "Crawl4AI"
    description = "本地浏览器渲染、正文提取、等待元素与滚动加载，不调用额外大模型"
    config_group = "retrieval/crawl4ai"
    key_hint = "配置 retrieval/crawl4ai 服务地址及与 CRAWL4AI_API_TOKEN 相同的访问令牌"

    def read(
        self, url: str, *, timeout: int = 15, extract_mode: str = "markdown",
        proxy_mode: ProxyMode = "system", respect_robots: bool = False,
    ) -> dict[str, Any]:
        return self.crawl(url, timeout=timeout, extract_mode=extract_mode,
                          proxy_mode=proxy_mode, respect_robots=respect_robots)

    def crawl(
        self, url: str, *, timeout: int = 30, extract_mode: str = "markdown",
        css_selector: str = "", wait_for: str = "", scan_full_page: bool = False,
        proxy_mode: ProxyMode = "system", respect_robots: bool = False,
    ) -> dict[str, Any]:
        from agent.retrieval.fetcher import check_ssrf_url, ssrf_protection_enabled

        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("抓取地址须为不含凭据的 HTTP(S) URL")
        if extract_mode not in {"markdown", "text", "raw"}:
            raise ValueError("extract_mode 须为 markdown、text 或 raw")
        if not 1 <= timeout <= 45:
            raise ValueError("timeout 须在 1–45 秒之间")
        if ssrf_protection_enabled():
            blocked = check_ssrf_url(url)
            if blocked:
                raise PermissionError(blocked)
        params: dict[str, Any] = {
            "cache_mode": {"type": "CacheMode", "params": "bypass"}, "page_timeout": timeout * 1000,
            "wait_until": "domcontentloaded", "check_robots_txt": respect_robots,
            "scan_full_page": scan_full_page, "max_scroll_steps": 10,
        }
        if css_selector.strip():
            params["css_selector"] = css_selector.strip()
        if wait_for.strip():
            params["wait_for"] = "css:" + wait_for.strip().removeprefix("css:")
            params["wait_for_timeout"] = timeout * 1000
        data, route = self.request_json("crawl", method="POST", timeout=timeout + 5,
            proxy_mode=proxy_mode, payload={"urls": [url],
                "crawler_config": {"type": "CrawlerRunConfig", "params": params}})
        rows = data.get("results")
        if data.get("success") is not True or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ServiceError("Crawl4AI 未返回单页抓取结果", code="INVALID_RESPONSE", service_route=route)
        page = rows[0]
        status = page.get("status_code")
        if page.get("success") is not True or (isinstance(status, int) and status >= 400):
            raise ServiceError(
                f"目标页面抓取失败{f'（HTTP {status}）' if status else ''}", code="PAGE_FETCH_FAILED",
                hint="核对页面地址和站点限制；浏览器渲染不能保证通过登录或风控校验",
                target_status_code=status, service_route=route,
                detail=sanitize_text(str(page.get("error_message") or ""))[:500],
            )
        markdown = page.get("markdown")
        content = (markdown.get("raw_markdown", "") if isinstance(markdown, dict) else markdown) or ""
        if extract_mode == "raw":
            content = str(page.get("html") or "")
        elif extract_mode == "text":
            from agent.retrieval.extractor import _bs4_to_text
            content = _bs4_to_text(str(page.get("cleaned_html") or page.get("html") or ""))
        if not isinstance(content, str) or not content.strip():
            raise ServiceError("页面没有可读正文，请检查选择器或等待元素", code="EMPTY_CONTENT", service_route=route)
        meta = page.get("metadata")
        meta = meta if isinstance(meta, dict) else {}
        result: dict[str, Any] = {
            "url": str(page.get("redirected_url") or page.get("url") or url),
            "title": str(meta.get("title") or "")[:300], "content": content,
            "extract_mode": extract_mode, "raw_length": len(content),
            "status_code": status, "service_route": route,
        }
        links = page.get("links")
        if isinstance(links, dict):
            found: dict[str, str] = {}
            for group in ("internal", "external"):
                for link in links.get(group, []) if isinstance(links.get(group), list) else []:
                    if not isinstance(link, dict):
                        continue
                    href = str(link.get("href") or "")
                    if urlsplit(href).scheme in {"http", "https"} and len(found) < 30:
                        found[href] = str(link.get("text") or "")[:160]
            result["links"] = [{"url": href, "title": title} for href, title in found.items()]
        return result


register_configs_safe({"retrieval/crawl4ai": {
    "retrieval_crawl4ai_base_url": {"default": "", "validator": validate_service_url,
        "description": "Crawl4AI 本地服务地址，例如 http://127.0.0.1:11235；留空不参与自动选择。工具 proxy_mode 仅控制到此服务的连接，不控制浏览器出站"},
    "retrieval_crawl4ai_api_key": {"default": "", "value_type": "password",
        "description": "自托管服务访问令牌，对应部署配置 CRAWL4AI_API_TOKEN，不是云平台 Key"},
}})
