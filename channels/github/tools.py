"""GitHub 工具分组的装配入口。"""
from __future__ import annotations

from .tool_api import GitHubApiTools
from .tool_config import GitHubConfigTools
from .tool_reads import GitHubReadTools
from .tool_writes import GitHubWriteTools


class GitHubToolsMixin(GitHubApiTools, GitHubConfigTools, GitHubReadTools, GitHubWriteTools):
    """组合查询、写入、配置和完整 API 工具。"""
