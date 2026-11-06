"""GitHub 频道配置、凭据引用和订阅的唯一声明源。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.channel.base import ChannelConfig

from .catalog import EVENT_OPTIONS, MESSAGE_FIELDS, validate_events

DEFAULT_EVENTS = [
    "push", "issues", "issue_comment", "pull_request", "pull_request_review",
    "pull_request_review_comment", "release", "star", "fork", "watch",
    "discussion", "discussion_comment", "workflow_run.completed",
]
AuthMode = Literal["pat", "app", "oauth", "none"]


def validate_message_fields(values: list[str] | None) -> list[str] | None:
    """校验可推送字段并去重。"""
    if values is None:
        return None
    if set(values) - set(MESSAGE_FIELDS):
        raise ValueError("未知推送内容字段")
    return list(dict.fromkeys(values))


class GitHubAccount(BaseModel):
    """独立认证上下文；密钥只保留凭据中心引用。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", description="账户名称")
    auth_mode: AuthMode = Field(default="pat", description="认证方式")
    token_ref: str = Field(default="github", pattern=r"^[A-Za-z0-9_-]+$", description="用户令牌凭据名")
    app_id: str = Field(default="", description="GitHub App ID")
    app_private_key_ref: str = Field(default="github_app", pattern=r"^[A-Za-z0-9_-]+$", description="App 私钥凭据名")
    installation_id: int = Field(default=0, ge=0, description="安装 ID；0 按仓库发现")
    client_id: str = Field(default="", description="OAuth client ID")
    client_secret_ref: str = Field(default="", pattern=r"^[A-Za-z0-9_-]*$", description="OAuth client secret 凭据名")
    webhook_secret_ref: str = Field(default="", pattern=r"^[A-Za-z0-9_-]*$", description="独立 webhook 密钥凭据名")


class RepoSubscription(BaseModel):
    """仓库事件、内容和会话路由的订阅策略。"""

    model_config = ConfigDict(extra="forbid")
    owner: str = Field(description="仓库所有者", pattern=r"^[A-Za-z0-9_.-]+$")
    repo: str = Field(description="仓库名称", pattern=r"^[A-Za-z0-9_.-]+$")
    events: list[str] = Field(default_factory=list, description="订阅事件；空选沿用默认",
                             json_schema_extra={"options": EVENT_OPTIONS})
    message_fields: list[str] | None = Field(default=None, description="推送内容；未设置时沿用默认，空选只推送标题",
                                             json_schema_extra={"options": MESSAGE_FIELDS})
    branches: list[str] = Field(default_factory=list, description="分支过滤；空列表接收全部，支持 glob")
    poll_interval_sec: int = Field(default=0, ge=0, description="轮询间隔秒；0 沿用默认")
    priority_boost: bool = Field(default=False, description="常规事件立即推送")
    local_path: str = Field(default="", description="本地代码路径")
    digest: bool = Field(default=True, description="参与每日汇总")
    account: str = Field(default="", description="认证账户；空值使用默认账户")
    installation_id: int = Field(default=0, ge=0, description="安装 ID；0 自动发现")
    session_mode: Literal["repository", "subject"] = Field(default="repository", description="会话按仓库或议题分离")
    delivery: Literal["smart", "immediate", "normal", "digest"] = Field(default="smart", description="事件推送策略")

    @field_validator("owner", "repo")
    @classmethod
    def valid_name(cls, value: str) -> str:
        """拒绝路径片段。"""
        if value in (".", ".."):
            raise ValueError("仓库名称不能是路径片段")
        return value

    _events = field_validator("events")(validate_events)
    _fields = field_validator("message_fields")(validate_message_fields)

    @property
    def full_name(self) -> str:
        """仓库完整名称。"""
        return f"{self.owner}/{self.repo}"


class GitHubConfig(ChannelConfig):
    """GitHub 接收、认证、消息和写操作配置。"""

    enabled: bool = Field(default=False, description="启用 GitHub 频道")
    mode: Literal["poll", "webhook", "both"] = Field(default="poll", description="事件接收方式")
    default_poll_interval_sec: int = Field(default=300, ge=60, description="默认轮询间隔秒")
    poll_max_pages: int = Field(default=3, ge=1, le=10, description="每次轮询最大页数")
    default_events: list[str] = Field(default_factory=lambda: list(DEFAULT_EVENTS), description="默认订阅事件",
                                      json_schema_extra={"options": EVENT_OPTIONS})
    message_fields: list[str] = Field(default_factory=lambda: list(MESSAGE_FIELDS), description="默认推送内容",
                                      json_schema_extra={"options": MESSAGE_FIELDS})
    webhook_secret: str = Field(default="", description="默认 Webhook 验签密钥",
                                json_schema_extra={"value_type": "password"})
    webhook_queue_size: int = Field(default=500, ge=10, le=100000, description="持久事件队列容量；满时拒收并保留已有事件")
    auth_mode: AuthMode = Field(default="pat", description="默认认证方式")
    token_ref: str = Field(default="github", pattern=r"^[A-Za-z0-9_-]+$", description="默认用户令牌凭据名")
    app_id: str = Field(default="", description="默认 GitHub App ID")
    app_private_key_ref: str = Field(default="github_app", pattern=r"^[A-Za-z0-9_-]+$", description="默认 App 私钥凭据名")
    installation_id: int = Field(default=0, ge=0, description="默认安装 ID；0 按仓库发现")
    accounts: list[GitHubAccount] = Field(default_factory=list, description="独立认证账户")
    api_base_url: str = Field(default="https://api.github.com", description="GitHub REST API 根地址")
    aggregate_window_sec: int = Field(default=300, ge=0, le=86400, description="常规消息聚合窗口秒")
    aggregate_max_events: int = Field(default=20, ge=1, le=100, description="单条聚合消息事件上限")
    body_digest_chars: int = Field(default=500, ge=0, le=20000, description="事件正文摘要字符数")
    quiet_hours: str = Field(default="", description="安静时段，例如 23:00-08:00")
    watch_mentions_of: str = Field(default="", description="提及此 GitHub 登录名时立即推送")
    reply_relay: str = Field(default="webui:web_user", description="回复转述目标，格式为 adapter:target")
    repos: list[RepoSubscription] = Field(default_factory=list, description="仓库订阅")
    account_events: list[str] = Field(default_factory=list, description="无仓库的账户/安装事件；空选不接收",
                                     json_schema_extra={"options": EVENT_OPTIONS})
    allow_write: bool = Field(default=False, description="允许 GitHub 写操作；仍须通过统一审批")
    rate_limit_reserve: int = Field(default=500, ge=0, description="轮询预留配额；最多占账户总配额的 10%")

    _events = field_validator("default_events", "account_events")(validate_events)
    _fields = field_validator("message_fields")(validate_message_fields)

    @field_validator("api_base_url")
    @classmethod
    def valid_api_url(cls, value: str) -> str:
        """校验 API 根地址。"""
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("API 根地址必须是无凭据、查询和片段的 HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("mode", "auth_mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: object) -> object:
        """接受大小写不同的模式名称。"""
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("quiet_hours")
    @classmethod
    def valid_quiet_hours(cls, value: str) -> str:
        """校验安静时段的时分范围。"""
        if value and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("安静时段须为 HH:MM-HH:MM")
        return value

    @model_validator(mode="after")
    def unique_subscriptions(self) -> GitHubConfig:
        """校验订阅和认证账户引用。"""
        names = [account.name for account in self.accounts]
        repos = [sub.full_name.lower() for sub in self.repos]
        if len(names) != len(set(names)) or "default" in names:
            raise ValueError("账户名称必须唯一，default 为保留名称")
        if len(repos) != len(set(repos)):
            raise ValueError("同一仓库只能有一条订阅")
        if any(sub.account and sub.account not in names for sub in self.repos):
            raise ValueError("订阅引用了不存在的认证账户")
        return self

    @property
    def poll_enabled(self) -> bool:
        """是否启用轮询。"""
        return self.mode in ("poll", "both")

    @property
    def webhook_enabled(self) -> bool:
        """是否启用回调。"""
        return self.mode in ("webhook", "both")

    def subscription_for(self, repo_full_name: str) -> RepoSubscription | None:
        """按仓库完整名称查找订阅。"""
        return next((sub for sub in self.repos if sub.full_name.lower() == repo_full_name.lower()), None)


_SECTIONS = {
    "subscriptions": {"repos", "default_events", "account_events"},
    "messages": {"message_fields", "aggregate_window_sec", "aggregate_max_events", "body_digest_chars", "quiet_hours", "watch_mentions_of", "reply_relay"},
    "authentication": {"auth_mode", "token_ref", "app_id", "app_private_key_ref", "installation_id", "accounts", "api_base_url", "allow_write"},
}
for _name, _field in GitHubConfig.model_fields.items():
    _section = next((section for section, names in _SECTIONS.items() if _name in names), "reception")
    _field.json_schema_extra = {**(_field.json_schema_extra or {}), "section": _section, "tag": _section}

GitHubConfig.model_rebuild(force=True)
CONFIG_MODEL = GitHubConfig
