"""DeepSeek Search / Fetch API 客户端。

封装 DeepSeek 自研搜索引擎的两个只读接口（Bearer 认证，按成功请求计费）：
- search: POST /v1/tools/search —— 关键词 → 结构化引用（评分/摘要/权威层级）
- fetch:  POST /v1/tools/fetch  —— URL → Markdown 页面正文

Model Experience:
    ① 模型经 retrieval 工具组消费：web_search 引用列表（snippet 优先取面向
       模型的 summary 摘要）与 web_fetch 的 Markdown 正文，与其他提供者同通道
    ② token 影响：引用结构与既有检索提供者同形，无额外注入
    ③ 缓存影响：工具结果属动态区，不触碰 stable/summary/conversation 前缀层
"""

from __future__ import annotations

import re
from typing import Any, Dict

import httpx

from entities._sdk import ProxyMode, async_network_client

_BASE_URL = "https://api.deepseek.com"
_SEARCH_PATH = "/v1/tools/search"
_FETCH_PATH = "/v1/tools/fetch"
_SEARCH_TIMEOUT = 30.0
# 接口约束（官方文档）：query 1-128 个 Unicode 字符、count 1-50、URL ≤ 8192 字符
_MAX_QUERY_CHARS = 128
_MAX_COUNT = 50
_MAX_URL_CHARS = 8192


class DeepSeekError(Exception):
    """DeepSeek 接口错误（HTTP 状态码 + 服务端消息）。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


def _error_message(resp: httpx.Response) -> str:
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


# ASCII 可打印标点全集（CommonMark 反斜杠转义的合法目标字符）
_PUNCT_CLASS = r"""!"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~"""


def unescape_markdown_punctuation(content: str) -> str:
    r"""还原 DeepSeek Fetch 正文的全量标点转义（`\,` `\:` `\/` 等）。

    Fetch 生成器对源文本中每个反斜杠与 ASCII 标点前都补 `\`（实测连
    `>>>` 提示符都输出为 `\>\>\>`，约占 10% 字符量）。左到右单趟还原是该
    编码的正确逆变换：原始 `\"` 被编码为 `\\\"`，单趟依次还原 `\\`、`\"`
    得回 `\"`；字母数字类转义（`\d` `\n`）不在还原范围，保持原样。
    """
    return re.sub(r"\\([" + _PUNCT_CLASS + r"])", r"\1", content)


async def _request(
    path: str, payload: Dict[str, Any], api_key: str, timeout: float,
    proxy_mode: ProxyMode = "system",
) -> Dict[str, Any]:
    """POST JSON 并归一错误：非 200 解析响应体消息后抛 DeepSeekError。"""
    async with async_network_client(proxy_mode=proxy_mode, timeout=timeout) as client:
        resp = await client.post(
            f"{_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code != 200:
        raise DeepSeekError(resp.status_code, _error_message(resp))
    return resp.json()


async def deepseek_search(
    query: str, *, count: int = 10, api_key: str = "",
    timeout: float = _SEARCH_TIMEOUT, proxy_mode: ProxyMode = "system",
) -> Dict[str, Any]:
    """联网检索 POST /v1/tools/search，返回完整响应数据。

    Args:
        query: 搜索关键词（1-128 个 Unicode 字符，支持 site:/lang:/after: 等查询运算符）
        count: 返回结果数（1-50，超出自动收敛到边界）
        api_key: DeepSeek API Key
        timeout: 请求超时秒数
        proxy_mode: 本机到 api.deepseek.com 的网络路径
    """
    query = query.strip()
    if not query or len(query) > _MAX_QUERY_CHARS:
        raise ValueError(f"query 须为 1-{_MAX_QUERY_CHARS} 个字符（当前 {len(query)}）")
    return await _request(
        _SEARCH_PATH,
        {"query": query, "count": min(max(1, count), _MAX_COUNT)},
        api_key, timeout, proxy_mode,
    )


async def deepseek_fetch(
    url: str, *, api_key: str = "", timeout: float = _SEARCH_TIMEOUT,
    proxy_mode: ProxyMode = "system",
) -> Dict[str, Any]:
    """网页读取 POST /v1/tools/fetch，返回 {title, content(Markdown)}。

    目标页面由 DeepSeek 服务端抓取与转换；proxy_mode 只控制本机到
    api.deepseek.com 的请求路径。

    Args:
        url: 目标页面地址（http/https，≤ 8192 字符，不得含用户名/密码）
        api_key: DeepSeek API Key
        timeout: 请求超时秒数
        proxy_mode: 本机到 api.deepseek.com 的网络路径
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"仅支持 http/https URL（收到: {url[:50]}）")
    if len(url) > _MAX_URL_CHARS:
        raise ValueError(f"URL 超过 {_MAX_URL_CHARS} 字符上限（当前 {len(url)}）")
    data = await _request(_FETCH_PATH, {"url": url}, api_key, timeout, proxy_mode)
    if isinstance(data.get("content"), str):
        data["content"] = unescape_markdown_punctuation(data["content"])
    return data


def normalize_search_results(data: Dict[str, Any], query: str, max_results: int) -> Dict[str, Any]:
    """搜索结果归一为标准引用结构（snippet 优先取面向模型的 summary）。"""
    items = data.get("results") if isinstance(data, dict) else None
    refs = []
    for item in items[:max_results] if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        ref: Dict[str, Any] = {
            "title": str(item.get("title", "")),
            "url": str(item.get("url", "")),
            "snippet": str(item.get("summary") or item.get("snippet") or ""),
        }
        if item.get("date"):
            ref["date"] = str(item["date"])
        refs.append(ref)
    output: Dict[str, Any] = {"query": query, "sources": len(refs), "references": refs}
    if not refs:
        output["hint"] = "无结果，建议更换关键词重写 query 后重试"
    return output


def deepseek_error_response(exc: Exception, action: str, hint: str = "") -> str:
    """DeepSeek 错误归因为结构化工具错误 JSON（状态码语义按官方文档分类）。"""
    from entities._sdk import ErrorCause, error_from_exception, tool_error

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
    if isinstance(exc, httpx.TimeoutException):
        return tool_error(
            f"{action}超时: {exc}", cause=ErrorCause.TIMEOUT, retryable=True, hint=default_hint,
        )
    if isinstance(exc, ValueError):
        return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
    return error_from_exception(exc, action=action, hint=default_hint)
