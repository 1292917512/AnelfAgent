"""GitHub 完整 REST、GraphQL 与对象上下文工具。

Model Experience: 工具返回有界 API 结果、分页位置及对象标识。
Token effect: 默认输出最多 20000 字符，较大结果需要分页查询。
Cache effect: 仅增加工具结果，不改变稳定前缀。
"""

from __future__ import annotations

import inspect
import json
from contextlib import nullcontext
from typing import Any
from urllib.parse import parse_qs, urlsplit

from githubkit import BaseAuthStrategy, GitHub
from graphql import OperationType, get_operation_ast, parse
from pydantic import TypeAdapter

from agent.channel.channel_types import _ok
from agent.channel.tool_bridge import channel_tool
from core.network import AsyncNetworkTransport
from core.tool_errors import ErrorCause, tool_error

from .auth import AuthContext
from .client import ApiResponse, GitHubClient
from .tool_common import GitHubToolHost, _err_of, _valid_repo


class _ResolvedAuth(BaseAuthStrategy):
    """将频道已选择的认证流交给 SDK。"""

    def __init__(self, auth: Any) -> None:
        self._auth = auth

    def get_auth_flow(self, github: Any) -> Any:
        return self._auth


def safe_result(value: Any, limit: int = 20000) -> Any:
    """隐藏令牌字段并限制 API 返回体积。"""
    def redact(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: "***" if str(key).lower() in {"token", "access_token", "refresh_token", "client_secret", "private_key"}
                    else redact(child) for key, child in item.items()}
        if isinstance(item, list):
            return [redact(child) for child in item]
        return item
    result = redact(value)
    serialized = json.dumps(result, ensure_ascii=False)
    return result if len(serialized) <= limit else {"truncated": True, "total_chars": len(serialized), "preview": serialized[:limit]}


def page_links(response: ApiResponse) -> dict[str, int]:
    """提取 Link 中的分页编号，不跟随外部地址。"""
    import re

    result: dict[str, int] = {}
    for target, relation in re.findall(r'<([^>]+)>;\s*rel="([^"]+)"', response.headers.get("link", "")):
        page = parse_qs(urlsplit(target).query).get("page", [""])[0]
        if page.isdigit():
            result[relation] = int(page)
    return result


class GitHubApiTools(GitHubToolHost):
    """完整 API 与诊断查询，写入统一经过审批和配置门控。"""

    def _auth_context(self, client: GitHubClient, auth: dict[str, Any] | None) -> Any:
        """校验并绑定任务局部认证选项。"""
        if client.auth is None:
            if auth:
                raise ValueError("客户端不支持认证覆盖")
            return nullcontext()
        if set(auth or {}) - set(AuthContext.__dataclass_fields__):
            raise ValueError("未知认证上下文字段")
        return client.auth.using(TypeAdapter(AuthContext).validate_python(auth or {}))

    @channel_tool(group="github", description="读取任意 GitHub REST API；path 为 /repos/owner/repo/...，params 为对象；auth 可选 account/installation_id/user_token_ref/as_app/as_oauth_app/repositories/repository_ids/permissions")
    async def api_read(self, path: str, params: dict[str, Any] | None = None, auth: dict[str, Any] | None = None) -> str:
        """执行只读 REST 请求，返回分页和配额信息。"""
        try:
            client = self._require_client()
            with self._auth_context(client, auth):
                response = await client.get(path, params=params)
            return _ok({"data": safe_result(response.data), "pages": page_links(response), "status": response.status,
                        "rate_remaining": response.rate_remaining})
        except Exception as exc:
            return _err_of(exc, "读取 API")

    @channel_tool(group="github", sensitive=True, description="执行 GitHub REST 写操作；method 为 POST/PUT/PATCH/DELETE，body 为 JSON 对象；须开启 allow_write")
    async def api_write(self, method: str, path: str, body: dict[str, Any] | list[Any] | str | None = None,
                        params: dict[str, Any] | None = None, auth: dict[str, Any] | None = None) -> str:
        """执行一次写请求，不自动重试不确定的结果。"""
        if gate := self._write_gate():
            return gate
        try:
            if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
                raise ValueError("写方法须为 POST、PUT、PATCH 或 DELETE")
            client = self._require_client()
            with self._auth_context(client, auth):
                response = await client.request(method, path, json_body=body, params=params)
            parts = path.strip("/").split("/")
            if method.upper() == "POST" and len(parts) >= 4 and parts[0] == "repos" and "comments" in parts and isinstance(response.data, dict):
                await self._remember_comment("/".join(parts[1:3]), response.data.get("id"))
            return _ok({"data": safe_result(response.data), "status": response.status})
        except Exception as exc:
            return _err_of(exc, "写入 API")

    async def _graphql(self, document: str, variables: dict[str, Any] | None, operation_name: str,
                       auth: dict[str, Any] | None, *, write: bool) -> str:
        """解析所选 GraphQL 操作并执行一次请求。"""
        try:
            operation = get_operation_ast(parse(document), operation_name or None)
            if operation is None:
                raise ValueError("请提供唯一操作，或指定 operation_name")
            if operation.operation == OperationType.SUBSCRIPTION:
                raise ValueError("GitHub GraphQL 不支持订阅操作")
            if not write and operation.operation != OperationType.QUERY:
                raise ValueError("mutation 必须使用 graphql_write")
            client = self._require_client()
            with self._auth_context(client, auth):
                response = await client.post("/graphql", json_body={"query": document, "variables": variables or {},
                                                                    "operationName": operation_name or None})
            data = response.data or {}
            if data.get("errors"):
                return tool_error("GraphQL 返回错误，请检查 errors 和已完成的数据", cause=ErrorCause.STATE,
                                  retryable=False, success=False, data=safe_result(data.get("data")),
                                  errors=safe_result(data["errors"]), partial=data.get("data") is not None)
            return _ok({"data": safe_result(data.get("data"))})
        except Exception as exc:
            return _err_of(exc, "GraphQL 请求")

    @channel_tool(group="github", description="执行 GitHub GraphQL query；自动拒绝 mutation")
    async def graphql_read(self, document: str, variables: dict[str, Any] | None = None,
                           operation_name: str = "", auth: dict[str, Any] | None = None) -> str:
        """执行 GraphQL 查询。"""
        return await self._graphql(document, variables, operation_name, auth, write=False)

    @channel_tool(group="github", sensitive=True, description="执行 GitHub GraphQL mutation，例如讨论回复或审查线程操作；须开启 allow_write")
    async def graphql_write(self, document: str, variables: dict[str, Any] | None = None,
                            operation_name: str = "", auth: dict[str, Any] | None = None) -> str:
        """执行经审批的 GraphQL 写操作。"""
        if gate := self._write_gate():
            return gate
        return await self._graphql(document, variables, operation_name, auth, write=True)

    @channel_tool(group="github", description="查询 GitHubKit REST 命名空间和操作签名，供查找完整 API 能力")
    async def describe_api(self, namespace: str = "", operation: str = "") -> str:
        """从 SDK 查询端点目录和参数签名。"""
        try:
            sdk = GitHub()
            rest = sdk.rest()
            if not namespace:
                return _ok({"namespaces": [name for name in dir(rest) if not name.startswith("_")],
                            "auth_schema": TypeAdapter(AuthContext).json_schema(),
                            "transport": "api_read / api_write / graphql_read / graphql_write"})
            if not namespace.isidentifier() or namespace.startswith("_"):
                raise ValueError("非法命名空间")
            group = getattr(rest, namespace)
            if not operation:
                return _ok({"operations": [name.removeprefix("async_") for name in dir(group) if name.startswith("async_")]})
            if not operation.isidentifier() or operation.startswith("_"):
                raise ValueError("非法操作名")
            method = getattr(group, "async_" + operation.removeprefix("async_"))
            return _ok({"signature": str(inspect.signature(method)), "description": (inspect.getdoc(method) or "")[:12000]})
        except Exception as exc:
            return _err_of(exc, "查询 API 目录")

    @channel_tool(group="github", sensitive=True, description="调用完整 GitHubKit REST 操作，包括上传端点；先用 describe_api 查询参数。所有 SDK 调用须开启 allow_write 并审批，普通查询请用 api_read")
    async def call_rest(self, namespace: str, operation: str, arguments: dict[str, Any],
                        auth: dict[str, Any] | None = None, binary_body_base64: str = "") -> str:
        """在指定认证上下文内执行单次 SDK 操作。"""
        if gate := self._write_gate():
            return gate
        try:
            import base64

            import httpx

            if any(not name.isidentifier() or name.startswith("_") for name in (namespace, operation)):
                raise ValueError("命名空间和操作必须为公开 SDK 名称")
            client = self._require_client()
            path = "/app" if namespace == "apps" and (auth or {}).get("as_app") else "/user"
            if arguments.get("owner") and arguments.get("repo"):
                path = "/repos/" + _valid_repo(f"{arguments['owner']}/{arguments['repo']}")
            with self._auth_context(client, auth):
                flow = await client.auth.resolve(path) if client.auth else None
                base_url = self.config.api_base_url
                if namespace == "repos" and operation.removeprefix("async_") == "upload_release_asset" and base_url == "https://api.github.com":
                    base_url = "https://uploads.github.com"
                sdk = GitHub(_ResolvedAuth(flow or httpx.Auth()), base_url=base_url,
                             async_transport=AsyncNetworkTransport(), auto_retry=False, http_cache=False,
                             trust_env=False, timeout=15)
                method = getattr(getattr(sdk.rest, namespace), "async_" + operation.removeprefix("async_"))
                values = dict(arguments)
                values["stream"] = False
                if binary_body_base64:
                    values["data"] = base64.b64decode(binary_body_base64, validate=True)
                inspect.signature(method).bind(**values)
                try:
                    async with sdk:
                        response = await method(**values)
                except httpx.TransportError as exc:
                    from .client import OutcomeUnknownError
                    raise OutcomeUnknownError("SDK 请求响应未确认，请查询状态后再决定是否重试") from exc
            try:
                data = response.raw_response.json()
            except ValueError:
                data = {"content_base64": base64.b64encode(response.raw_response.content).decode()}
            if (response.raw_request.method == "POST" and "/comments" in response.raw_request.url.path
                    and path.startswith("/repos/") and isinstance(data, dict)):
                await self._remember_comment(path.removeprefix("/repos/"), data.get("id"))
            return _ok({"status": response.status_code, "data": safe_result(data)})
        except Exception as exc:
            return _err_of(exc, "调用 SDK")

    @channel_tool(group="github", description="读取 PR 文件、审查和代码评论上下文；支持分页")
    async def get_pr_context(self, repo: str, number: int, page: int = 1) -> str:
        """返回可定位到文件、行和评论的 PR 上下文。"""
        try:
            repo = _valid_repo(repo)
            client = self._require_client()
            result: dict[str, Any] = {}
            for name, suffix in (("files", "files"), ("reviews", "reviews"), ("comments", "comments")):
                response = await client.get(f"/repos/{repo}/pulls/{int(number)}/{suffix}", params={"per_page": 30, "page": max(1, page)})
                result[name] = {"data": safe_result(response.data, 12000), "pages": page_links(response)}
            return _ok(result)
        except Exception as exc:
            return _err_of(exc, "查询 PR 上下文")

    @channel_tool(group="github", description="读取 GitHub Actions 运行中的 job、步骤与失败位置")
    async def get_workflow_jobs(self, repo: str, run_id: int, page: int = 1) -> str:
        """返回 CI 任务与失败步骤的结构化信息。"""
        try:
            repo = _valid_repo(repo)
            response = await self._require_client().get(f"/repos/{repo}/actions/runs/{int(run_id)}/jobs",
                                                       params={"per_page": 30, "page": max(1, page)})
            return _ok({"data": safe_result(response.data), "pages": page_links(response)})
        except Exception as exc:
            return _err_of(exc, "查询 CI 任务")

    @channel_tool(group="github", sensitive=True, description="回复 issue、PR、代码审查评论或 commit；kind=issue/review/commit，target 为议题编号或 commit SHA")
    async def reply(self, repo: str, target: str, body: str, kind: str = "issue", comment_id: int = 0) -> str:
        """根据对象类型选择 GitHub 评论回复端点。"""
        if gate := self._write_gate():
            return gate
        try:
            repo = _valid_repo(repo)
            if not body.strip():
                raise ValueError("回复正文不能为空")
            if kind == "issue":
                path = f"/repos/{repo}/issues/{int(target)}/comments"
            elif kind == "review" and comment_id > 0:
                path = f"/repos/{repo}/pulls/{int(target)}/comments/{comment_id}/replies"
            elif kind == "commit" and target.isalnum():
                path = f"/repos/{repo}/commits/{target}/comments"
            else:
                raise ValueError("未知回复类型或缺少评论 ID")
            data = (await self._require_client().post(path, json_body={"body": body})).data or {}
            await self._remember_comment(repo, data.get("id"))
            return _ok({"id": data.get("id"), "url": data.get("html_url")})
        except Exception as exc:
            return _err_of(exc, "回复 GitHub")
