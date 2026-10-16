"""SearXNG JSON 搜索适配；引擎聚合由自托管服务执行。

模型可见：沿用搜索引用格式，附带引擎降级警告，不生成额外 LLM 摘要。
Token 影响：引用数量与单条摘要有界，省去整页搜索 HTML。
缓存影响：仅工具结果，不修改稳定前缀。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from core.config import register_configs_safe

from .base import normalize_references
from .selfhosted import SelfHostedProvider, ServiceError, validate_service_url


class SearXNGProvider(SelfHostedProvider):
    """通过 /search?format=json 使用已配置的自托管搜索服务。"""

    name = "searxng"
    display_name = "SearXNG"
    description = "本地服务聚合搜索引擎，返回标题、链接与摘要，无需商业搜索 Key"
    requires_credential = False
    config_group = "retrieval/searxng"
    key_hint = "配置 retrieval/searxng 服务地址，并在 SearXNG settings.yml 的 search.formats 启用 json"

    def search(self, query: str, max_results: int) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("搜索词不能为空")
        params: dict[str, Any] = {"q": query, "format": "json", "pageno": 1}
        for name in ("language", "engines"):
            value = str(self.setting(name) or "").strip()
            if value:
                params[name] = value
        data, route = self.request_json("search", params=params, timeout=float(self.setting("timeout", 20)))
        rows = data.get("results")
        if not isinstance(rows, list):
            raise ServiceError("SearXNG 响应缺少 results 列表", code="INVALID_RESPONSE")
        warnings = data.get("unresponsive_engines")
        warnings = warnings if isinstance(warnings, list) else []
        if not rows and warnings:
            raise ServiceError(
                "搜索引擎未正常返回结果", code="SEARCH_ENGINES_UNAVAILABLE",
                hint="检查 SearXNG 出站网络及引擎配置；不要将引擎失败理解为没有相关内容",
                unresponsive_engines=warnings[:10], service_route=route,
            )
        refs: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "")
            if urlsplit(url).scheme not in {"http", "https"} or url in seen:
                continue
            seen.add(url)
            refs.append({"title": str(row.get("title") or "")[:300], "url": url,
                         "snippet": str(row.get("content") or "")[:1200],
                         "date": str(row.get("publishedDate") or "")[:80]})
            if len(refs) >= min(max(1, max_results), 20):
                break
        result = normalize_references(query, refs, max_results)
        result["service_route"] = route
        if warnings:
            result["unresponsive_engines"] = warnings[:10]
        return result


register_configs_safe({"retrieval/searxng": {
    "retrieval_searxng_base_url": {"default": "", "validator": validate_service_url,
        "description": "SearXNG 本地服务地址，例如 http://127.0.0.1:18080；留空不参与自动选择。服务连接默认直连，搜索引擎出站网络在 SearXNG 配置"},
    "retrieval_searxng_proxy_mode": {"default": "direct", "value_type": "enum",
        "options": ["direct", "system", "proxy"], "related_key": "proxy_enabled",
        "description": "到 SearXNG 服务的网络策略，默认直连；system 跟随全局规则，proxy 使用全局代理。不改变服务到搜索引擎的出站网络"},
    "retrieval_searxng_language": {"default": "", "description": "搜索语言，留空使用 SearXNG 默认值，例如 zh-CN"},
    "retrieval_searxng_engines": {"default": "", "description": "搜索引擎名，逗号分隔；留空使用 SearXNG 已启用的引擎", "advanced": True},
    "retrieval_searxng_timeout": {"default": 20, "min": 1, "max": 45, "unit": "s", "description": "等待搜索服务返回结果的超时"},
}})
