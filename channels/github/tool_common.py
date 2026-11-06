"""GitHub 工具校验、错误归因和共享依赖。"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from core.tool_errors import ErrorCause, error_from_exception, tool_error

from .client import AuthError, GitHubApiError, GitHubClient, NotFoundError, OutcomeUnknownError, RateLimitError
from .config import GitHubConfig
from .state import DigestBuffer, EventStats

if TYPE_CHECKING:
    from .journal import EventJournal
    from .poller import GitHubPoller


def _err(message: str) -> str:
    """构造参数错误结果。"""
    return tool_error(message, cause=ErrorCause.PARAM, retryable=False, success=False)


def _err_of(exc: Exception, action: str) -> str:
    """将 API 异常归因为可操作的工具错误。"""
    import httpx
    from githubkit.exception import RateLimitExceeded, RequestError, RequestFailed

    if isinstance(exc, RateLimitExceeded):
        exc = RateLimitError("GitHub SDK 请求受到限流", retry_after=int(exc.retry_after.total_seconds()))
    elif isinstance(exc, RequestError) and isinstance(exc.exc, httpx.TransportError):
        exc = OutcomeUnknownError("SDK 请求响应未确认，请先查询目标状态")
    elif isinstance(exc, RequestFailed):
        status = exc.response.status_code
        if status >= 500:
            exc = OutcomeUnknownError(f"SDK 返回 {status}，请先查询操作是否已生效", status=status)
        elif status in (401, 403):
            exc = AuthError(f"GitHub 权限校验失败 ({status})", status=status)
        elif status == 404:
            exc = NotFoundError("SDK 资源不可见", status=status)
        else:
            exc = GitHubApiError(f"GitHub 请求失败 ({status})", status=status)
    if isinstance(exc, OutcomeUnknownError):
        return tool_error(str(exc), cause=ErrorCause.NETWORK, retryable=False, outcome="unknown", success=False,
                          hint="先查询目标状态，确认未生效后再决定是否重新提交")
    if isinstance(exc, RateLimitError):
        return tool_error(str(exc), cause=ErrorCause.STATE, retryable=True, retry_after=exc.retry_after, reset_at=exc.reset_at, success=False)
    if isinstance(exc, AuthError):
        return tool_error(str(exc), cause=ErrorCause.PERMISSION, retryable=False, hint="检查账户、安装和 GitHub 权限", success=False)
    if isinstance(exc, NotFoundError):
        return tool_error(f"资源不存在或不可见: {exc}", cause=ErrorCause.NOT_FOUND, retryable=False, success=False)
    if isinstance(exc, GitHubApiError):
        return tool_error(str(exc), cause=ErrorCause.NETWORK, retryable=False, success=False)
    import json
    return json.dumps({**json.loads(error_from_exception(exc, action=action)), "success": False}, ensure_ascii=False)


def _valid_repo(repo: str) -> str:
    """校验完整仓库名称，拒绝路径和查询注入。"""
    repo = repo.strip()
    parts = repo.split("/")
    if len(parts) != 2 or any(part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise ValueError("仓库参数须为 owner/repo")
    return repo


class GitHubToolHost:
    """频道工具依赖和统一写操作开关。"""

    _client: GitHubClient | None
    _stats: EventStats
    _digest: DigestBuffer
    _journal: EventJournal | None
    _poller: GitHubPoller | None
    config: GitHubConfig

    def _require_client(self) -> GitHubClient:
        """要求频道已启动。"""
        if self._client is None:
            raise RuntimeError("GitHub 频道未启动")
        return self._client

    def _write_gate(self) -> str:
        """检查 GitHub 写操作总开关。"""
        if not self.config.allow_write:
            return tool_error("GitHub 写操作未启用 (allow_write=false)", cause=ErrorCause.PERMISSION, retryable=False, success=False,
                              hint="由用户开启频道 allow_write 后再执行")
        return ""

    async def _remember_comment(self, repo: str, object_id: object) -> None:
        """记录本频道评论以过滤后续回调。"""
        if self._journal is not None:
            await self._journal.remember_outgoing(repo, "comment", object_id)
