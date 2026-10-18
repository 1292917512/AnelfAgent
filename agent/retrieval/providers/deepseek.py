"""DeepSeek Search / Fetch 提供者：联网检索 + 网页读取（Markdown）能力。

两个能力调用 DeepSeek 自研搜索引擎 REST 接口（Bearer 认证）：
- search: POST /v1/tools/search（query/count；结果带 summary 摘要与发布时间）
- reader: POST /v1/tools/fetch （url → Markdown 正文，服务端抓取）

凭据解析链：统一配置 retrieval_deepseek_api_key
→ llm_clients.json 的 deepseek.com 供应商凭据 → DEEPSEEK_API_KEY 环境变量。
计费按成功请求（$0.75/千次），空结果与失败不计费；Search/Fetch 各自 QPS 5/秒。

Model Experience:
    模型可见：检索引用（snippet 优先取面向模型的 summary）与 Fetch 的 Markdown
    正文，经 retrieval 工具组与其他提供者同通道。Token 影响：引用结构同形，
    Fetch 正文标点转义已归一（约省 10% 字符量）。缓存影响：仅工具结果动态区，
    不触碰前缀层。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Tuple

from core.network import ProxyMode
from core.tool_errors import ErrorCause, error_from_exception, tool_error

from .base import (
    SOURCE_CONFIG,
    SOURCE_ENV,
    SOURCE_LLM,
    Provider,
    llm_provider_key,
    normalize_references,
    run_coro_sync,
)

_BASE_URL = "https://api.deepseek.com"
_SEARCH_PATH = "/v1/tools/search"
_FETCH_PATH = "/v1/tools/fetch"
_CONNECT_TIMEOUT = 10.0
_SEARCH_TIMEOUT = 30.0
# DeepSeek Fetch 由服务端读取整页再转 Markdown，预算高于工具层默认 15 秒
_FETCH_TIMEOUT_FLOOR = 30.0
# 接口约束（官方文档）：query 1-128 个 Unicode 字符、count 1-50、URL ≤ 8192 字符
_MAX_QUERY_CHARS = 128
_MAX_COUNT = 50
_MAX_URL_CHARS = 8192
# ASCII 可打印标点全集（CommonMark 反斜杠转义的合法目标字符）
_PUNCT_CLASS = r"""!"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~"""


class DeepSeekError(Exception):
    """DeepSeek 接口错误（HTTP 状态码 + 服务端消息）。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


def unescape_markdown_punctuation(content: str) -> str:
    r"""还原 Fetch 正文的全量标点转义（`\,` `\:` `\/` 等）。

    Fetch 生成器对源文本中每个反斜杠与 ASCII 标点前都补 `\`（实测连
    `>>>` 提示符都输出为 `\>\>\>`，约占 10% 字符量）。左到右单趟还原是该
    编码的正确逆变换：原始 `\"` 被编码为 `\\\"`，单趟依次还原 `\\`、`\"`
    得回 `\"`；字母数字类转义（`\d` `\n`）不在还原范围，保持原样。
    """
    return re.sub(r"\\([" + _PUNCT_CLASS + r"])", r"\1", content)


def _error_message(resp: Any) -> str:
    """提取错误响应体中的服务端消息（字段命名未知，按常见键探测）。"""
    try:
        body = resp.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "")
        for key in ("message", "msg", "detail"):
            if body.get(key):
                return str(body[key])
    return ""


async def _post_tools(
    path: str, payload: Dict[str, Any], api_key: str, timeout: float,
    proxy_mode: ProxyMode = "system",
) -> Dict[str, Any]:
    """POST JSON 并归一错误：非 200 解析响应体消息后抛 DeepSeekError。"""
    import httpx2

    from core.network.httpx2 import AsyncNetworkTransport2

    async with httpx2.AsyncClient(
        transport=AsyncNetworkTransport2(proxy_mode),
        trust_env=False,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=httpx2.Timeout(_CONNECT_TIMEOUT, read=timeout),
    ) as client:
        resp = await client.post(f"{_BASE_URL}{path}", json=payload)
    if resp.status_code != 200:
        raise DeepSeekError(resp.status_code, _error_message(resp))
    return resp.json()


def _map_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """单条检索结果字段归一（snippet 优先取面向模型的 summary 摘要）。"""
    return {
        "title": str(item.get("title") or ""),
        "url": str(item.get("url") or ""),
        "snippet": str(item.get("summary") or item.get("snippet") or ""),
        "date": str(item.get("date") or ""),
    }


class DeepSeekProvider(Provider):
    """DeepSeek Search（联网检索）+ Fetch（网页读取，Markdown 正文）。"""

    name = "deepseek"
    display_name = "DeepSeek"
    description = "DeepSeek Search/Fetch：联网检索 + 网页读取（Markdown），按成功请求计费（$0.75/千次）"
    key_hint = "在 Web 检索页签配置 DeepSeek API Key，或 LLM 供应商（deepseek.com）的 API Key"

    def credential(self) -> Tuple[str, str]:
        from agent.retrieval.config import get_provider_key
        value = get_provider_key(self.name)
        if value:
            return value, SOURCE_CONFIG
        api_key, _provider_id = llm_provider_key("deepseek.com")
        if api_key:
            return api_key, SOURCE_LLM
        env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        return (env_key, SOURCE_ENV) if env_key else ("", "")

    def set_api_key(self, api_key: str) -> None:
        from agent.retrieval.config import set_provider_key
        set_provider_key(self.name, api_key)

    def _require_key(self) -> str:
        api_key, _source = self.credential()
        if not api_key:
            raise RuntimeError(f"DeepSeek 未配置凭据（{self.key_hint}）")
        return api_key

    # ── 检索能力 ────────────────────────────────────────────────

    def search(self, query: str, max_results: int) -> Dict[str, Any]:
        query = query.strip()
        if not query or len(query) > _MAX_QUERY_CHARS:
            raise ValueError(f"query 须为 1-{_MAX_QUERY_CHARS} 个字符（当前 {len(query)}）")
        payload = run_coro_sync(_post_tools(
            _SEARCH_PATH,
            {"query": query, "count": min(max(1, max_results), _MAX_COUNT)},
            self._require_key(), _SEARCH_TIMEOUT,
        ))
        items = payload.get("results") if isinstance(payload, dict) else None
        rows: List[Dict[str, Any]] = (
            [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
        )
        return normalize_references(query, [_map_item(i) for i in rows], max_results)

    # ── 网页读取能力 ────────────────────────────────────────────

    def read(
        self,
        url: str,
        *,
        timeout: int = 15,
        extract_mode: str = "markdown",
        proxy_mode: ProxyMode | None = None,
        respect_robots: bool = False,
    ) -> Dict[str, Any]:
        """读取网页正文（服务端抓取并转 Markdown；robots 合规在 DeepSeek 侧执行）。"""
        if extract_mode != "markdown":
            raise ValueError("DeepSeek 网页读取仅支持 markdown 模式（text/raw 请改用 builtin 提供者）")
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"仅支持 http/https URL（收到: {url[:50]}）")
        if len(url) > _MAX_URL_CHARS:
            raise ValueError(f"URL 超过 {_MAX_URL_CHARS} 字符上限（当前 {len(url)}）")
        data = run_coro_sync(_post_tools(
            _FETCH_PATH, {"url": url}, self._require_key(),
            max(float(timeout), _FETCH_TIMEOUT_FLOOR),
            proxy_mode=proxy_mode if proxy_mode is not None else "system",
        ))
        content = data.get("content")
        return {
            "url": url,
            "extract_mode": "markdown",
            "title": str(data.get("title") or ""),
            "content": unescape_markdown_punctuation(content) if isinstance(content, str) else "",
        }

    # ── 错误分类 ────────────────────────────────────────────────

    def error_response(self, exc: Exception, action: str, hint: str = "") -> str:
        """错误归因为结构化工具错误 JSON（状态码语义按官方文档分类）。"""
        import httpx2

        default_hint = hint or "检查 DeepSeek API Key 配置与网络连通性"
        if isinstance(exc, DeepSeekError):
            code = exc.status_code
            if code == 400:
                return tool_error(
                    f"{action}请求无效: {exc}", cause=ErrorCause.PARAM, retryable=False,
                    hint="检查 query（1-128 字符）或 URL 是否符合接口约束",
                )
            if code == 401:
                return tool_error(
                    f"{action}鉴权失败: {exc}", cause=ErrorCause.CONFIG, retryable=False,
                    hint="检查 DeepSeek API Key 是否有效",
                )
            if code == 402:
                return tool_error(
                    f"{action}账户余额不足: {exc}", cause=ErrorCause.CONFIG, retryable=False,
                    hint="DeepSeek 账户充值后重试",
                )
            if code == 403:
                return tool_error(
                    f"{action}被安全策略拒绝: {exc}", cause=ErrorCause.PERMISSION, retryable=False,
                    hint="目标页面被 DeepSeek 访问策略拦截，可换用 builtin 提供者读取",
                )
            if code == 404:
                return tool_error(
                    f"{action}未取回内容: {exc}", cause=ErrorCause.NOT_FOUND, retryable=False,
                    hint="目标页面无法取回可用内容，确认 URL 或换用 builtin 提供者",
                )
            if code == 408:
                return tool_error(
                    f"{action}读取超时: {exc}", cause=ErrorCause.TIMEOUT, retryable=True,
                    hint="稍后重试",
                )
            if code == 413:
                return tool_error(
                    f"{action}请求体过大: {exc}", cause=ErrorCause.PARAM, retryable=False,
                    hint=f"URL 超长（上限 {_MAX_URL_CHARS} 字符）",
                )
            if code == 429:
                return tool_error(
                    f"{action}触发限流 (HTTP 429): {exc}", cause=ErrorCause.NETWORK, retryable=True,
                    hint="Search/Fetch 各自 QPS 上限 5 次/秒（账号级），稍后重试",
                )
            if code >= 500:
                return tool_error(
                    f"{action}服务端错误 (HTTP {code}): {exc}", cause=ErrorCause.NETWORK,
                    retryable=True, hint=default_hint,
                )
        if isinstance(exc, httpx2.TimeoutException):
            return tool_error(
                f"{action}超时: {exc}", cause=ErrorCause.TIMEOUT, retryable=True, hint=default_hint,
            )
        if isinstance(exc, ValueError):
            return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
        return error_from_exception(exc, action=action, hint=default_hint)
