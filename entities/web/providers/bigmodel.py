"""智谱 BigModel 提供者：GLM Coding Plan 检索 / 网页读取 / 仓库文档能力。

三个能力分别经对应 MCP 服务调用（Streamable HTTP + Bearer 认证）：
- search: web_search_prime（{"search_query"}）
- reader: web_reader     webReader（{"url", "timeout", "return_format"}）
- repo:   zread          search_doc / get_repo_structure / read_file（{"repo_name", ...}）

凭据解析链：entities/web/config.json 的 provider_keys.bigmodel
→ llm_clients.json 中 bigmodel.cn / z.ai 供应商凭据 → BIGMODEL_API_KEY 环境变量。

响应约定（实测）：文本块承载双重编码 JSON（JSON 字符串内嵌对象/数组），
解析统一走 _payload_from_result。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities.web.providers.base import (
    SOURCE_CONFIG,
    SOURCE_ENV,
    SOURCE_LLM,
    Provider,
    llm_provider_key,
    normalize_references,
    run_coro_sync,
)

_MCP_BASE = "https://open.bigmodel.cn/api/mcp"
_CONNECT_TIMEOUT = 10.0
_SEARCH_TIMEOUT = 30.0
_REPO_TIMEOUT = 60.0

# 检索结果数组在响应 JSON 中的常见键
_RESULT_LIST_KEYS = ("search_result", "results", "organic", "items", "data", "list")
# 仓库文档响应中文本内容的常见键
_TEXT_KEYS = ("content", "answer", "text", "result")


def _result_text(result: Any) -> str:
    """拼接 MCP 结果的文本块。"""
    parts = [
        str(getattr(block, "text", ""))
        for block in (getattr(result, "content", None) or [])
        if getattr(block, "type", "") == "text"
    ]
    return "\n".join(p for p in parts if p)


def _parse_json_text(text: str) -> Any:
    """解析文本块中的 JSON（服务端可能双重编码：JSON 字符串内嵌对象/数组）。"""
    for _ in range(2):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, str):
            return data
        text = data
    return None


def _payload_from_result(result: Any) -> Any:
    """从 MCP 调用结果提取负载：structuredContent 优先，文本 JSON 次之，纯文本原样返回。"""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, (dict, list)):
        return structured
    text = _result_text(result)
    parsed = _parse_json_text(text)
    return parsed if parsed is not None else text


async def _call_mcp(
    server_path: str, tool: str, args: Dict[str, Any], api_key: str, timeout: float,
) -> Any:
    """短会话调用智谱 MCP 工具（initialize → call），返回解析后的负载。"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(
        f"{_MCP_BASE}/{server_path}/mcp",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_CONNECT_TIMEOUT, sse_read_timeout=timeout,
    ) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
    if result.isError:
        raise RuntimeError(_result_text(result) or f"智谱 MCP {tool} 返回错误")
    return _payload_from_result(result)


def _looks_like_results(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and isinstance(value[0], dict)
        and any(k in value[0] for k in ("link", "url", "title"))
    )


def _find_result_list(data: Any) -> List[Dict[str, Any]]:
    """在响应 JSON 中查找检索结果数组（优先匹配常见键，否则递归找含链接字段的 dict 列表）。"""
    if isinstance(data, dict):
        for key in _RESULT_LIST_KEYS:
            if _looks_like_results(data.get(key)):
                return data[key]
        for value in data.values():
            found = _find_result_list(value)
            if found:
                return found
    elif _looks_like_results(data):
        return data
    return []


def _map_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """单条检索结果字段归一（兼容 title/link/content/publish_date 等命名差异）。"""
    return {
        "title": item.get("title") or item.get("name") or "",
        "url": item.get("link") or item.get("url") or "",
        "snippet": item.get("content") or item.get("snippet") or item.get("summary") or "",
        "date": item.get("publish_date") or item.get("date") or item.get("published") or "",
    }


def _payload_text(payload: Any) -> str:
    """提取负载中的文本内容（dict 取常见文本键，其余序列化）。"""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in _TEXT_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _unwrap(exc: BaseException) -> BaseException:
    """展开 anyio ExceptionGroup 包装（py3.10 无内置 ExceptionGroup，鸭子类型处理）。"""
    depth = 0
    while depth < 5:
        sub = getattr(exc, "exceptions", None)
        if not sub:
            break
        exc = sub[0]
        depth += 1
    return exc


class BigModelProvider(Provider):
    """智谱 GLM Coding Plan（检索 + 网页读取 + 仓库文档）。"""

    name = "bigmodel"
    display_name = "智谱 BigModel"
    description = "智谱 GLM Coding Plan：联网检索 / 网页读取 / GitHub 仓库文档（订阅配额）"
    key_hint = "在 Web 面板配置 BigModel Coding Plan API Key，或 LLM 供应商（bigmodel.cn）的 API Key"

    def credential(self) -> Tuple[str, str]:
        from entities.web.web_config import get_provider_key
        value = get_provider_key(self.name)
        if value:
            return value, SOURCE_CONFIG
        api_key, _provider_id = llm_provider_key("bigmodel.cn", "z.ai")
        if api_key:
            return api_key, SOURCE_LLM
        env_key = os.environ.get("BIGMODEL_API_KEY", "").strip()
        return (env_key, SOURCE_ENV) if env_key else ("", "")

    def set_api_key(self, api_key: str) -> None:
        from entities.web.web_config import set_provider_key
        set_provider_key(self.name, api_key)

    def _require_key(self) -> str:
        api_key, _source = self.credential()
        if not api_key:
            raise RuntimeError(f"智谱 BigModel 未配置凭据（{self.key_hint}）")
        return api_key

    # ── 检索能力 ────────────────────────────────────────────────────

    def search(self, query: str, max_results: int) -> Dict[str, Any]:
        payload = run_coro_sync(_call_mcp(
            "web_search_prime", "web_search_prime",
            {"search_query": query}, self._require_key(), _SEARCH_TIMEOUT,
        ))
        if isinstance(payload, (dict, list)):
            items = _find_result_list(payload)
            if items:
                return normalize_references(query, [_map_item(i) for i in items], max_results)
        if isinstance(payload, str) and payload.strip():
            return normalize_references(query, [{"title": "", "url": "", "snippet": payload.strip()[:2000]}], 1)
        return normalize_references(query, [], max_results)

    # ── 网页读取能力 ────────────────────────────────────────────────

    def read(
        self,
        url: str,
        *,
        timeout: int = 15,
        extract_mode: str = "markdown",
        use_proxy: bool = False,
        respect_robots: bool = False,
    ) -> Dict[str, Any]:
        if extract_mode == "raw":
            raise ValueError("智谱网页读取不支持 raw 模式（请改用 builtin 提供者）")
        payload = run_coro_sync(_call_mcp(
            "web_reader", "webReader",
            {
                "url": url,
                "timeout": min(max(5, timeout), 60),
                "return_format": "text" if extract_mode == "text" else "markdown",
            },
            self._require_key(), float(timeout) + 15,
        ))
        result: Dict[str, Any] = {"url": url, "extract_mode": extract_mode}
        if isinstance(payload, dict):
            result["url"] = str(payload.get("url") or url)
            if payload.get("title"):
                result["title"] = str(payload["title"])
            result["content"] = _payload_text(payload)
        else:
            result["content"] = str(payload)
        return result

    # ── 仓库文档能力 ────────────────────────────────────────────────

    def search_doc(self, repo: str, query: str) -> str:
        payload = run_coro_sync(_call_mcp(
            "zread", "search_doc",
            {"repo_name": repo, "query": query, "language": "zh"},
            self._require_key(), _REPO_TIMEOUT,
        ))
        return _payload_text(payload)

    def get_repo_structure(self, repo: str, dir_path: str = "") -> str:
        args: Dict[str, Any] = {"repo_name": repo}
        if dir_path.strip():
            args["dir_path"] = dir_path.strip()
        payload = run_coro_sync(_call_mcp(
            "zread", "get_repo_structure", args, self._require_key(), _REPO_TIMEOUT,
        ))
        return _payload_text(payload)

    def read_repo_file(self, repo: str, path: str) -> str:
        payload = run_coro_sync(_call_mcp(
            "zread", "read_file",
            {"repo_name": repo, "file_path": path},
            self._require_key(), _REPO_TIMEOUT,
        ))
        return _payload_text(payload)

    # ── 错误分类 ────────────────────────────────────────────────────

    def error_response(self, exc: Exception, action: str, hint: str = "") -> str:
        import httpx
        default_hint = hint or "检查 BigModel Coding Plan API Key 配置与网络连通性"
        unwrapped = _unwrap(exc)
        if isinstance(unwrapped, httpx.HTTPStatusError):
            code = unwrapped.response.status_code
            if code in (401, 403):
                return tool_error(
                    f"{action}鉴权被拒绝 (HTTP {code})",
                    cause=ErrorCause.CONFIG, retryable=False,
                    hint="检查 BigModel Coding Plan API Key 是否有效（个人/团队套餐 Key 不通用）",
                )
            if code == 429:
                return tool_error(
                    f"{action}触发限流 (HTTP 429)",
                    cause=ErrorCause.NETWORK, retryable=True, hint="稍后重试",
                )
            if code >= 500:
                return tool_error(
                    f"{action}服务端错误 (HTTP {code})",
                    cause=ErrorCause.NETWORK, retryable=True, hint=default_hint,
                )
        if isinstance(unwrapped, RuntimeError):
            message = str(unwrapped)
            if "1301" in message or "contentFilter" in message:
                return tool_error(
                    f"{action}被服务端内容过滤拦截",
                    cause=ErrorCause.PARAM, retryable=False,
                    hint="查询词触发内容安全检测，改写 query（去掉敏感词、缩短长度）后重试",
                )
            if "未配置凭据" in message:
                return tool_error(message, cause=ErrorCause.CONFIG, retryable=False, hint=default_hint)
        if isinstance(unwrapped, ValueError):
            return tool_error(str(unwrapped), cause=ErrorCause.PARAM, retryable=False)
        return error_from_exception(exc, action=action, hint=default_hint)
