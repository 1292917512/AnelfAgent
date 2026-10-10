"""GitHub 频道配置。

CONFIG_MODEL 是配置的唯一声明源:schema 经 agent/channel/config.py 派生注册到
ConfigRegistry(组 adapter/github,键 github_<field>),值统一存 ConfigManager
(频道目录 channel_config.json 存储后端,``ANELF_GITHUB_<FIELD>`` 环境变量优先)。

密钥纪律:token / App 私钥不落本文件——登记 ``config/provider_keys.json``
(gitignored),本文件只存引用名(token_ref / app_private_key_ref)。
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, field_validator

from agent.channel.base import ChannelConfig

# 默认订阅的事件族(支持 "issues" 族 / "issues.opened" 精确 action / "*" 全部)
DEFAULT_EVENTS: List[str] = [
    "push",
    "issues",
    "issue_comment",
    "pull_request",
    "pull_request_review",
    "release",
    "star",
    "fork",
    "watch",
    "discussion",
]


class RepoSubscription(BaseModel):
    """单个仓库的监听订阅。"""

    owner: str = Field(description="仓库所有者(用户或组织)")
    repo: str = Field(description="仓库名")
    events: List[str] = Field(
        default_factory=list,
        description="订阅事件清单(空=全局 default_events;支持 'issues' 族 / 'issues.opened' 精确 / '*')",
    )
    branches: List[str] = Field(
        default_factory=list,
        description="push 事件分支过滤(空=全部;支持 'main' / 'release/*' glob)",
    )
    poll_interval_sec: int = Field(default=0, description="轮询间隔秒(0=用全局默认,下限 60)")
    priority_boost: bool = Field(default=False, description="重点仓:NORMAL 事件升级为 IMMEDIATE")
    local_path: str = Field(default="", description="本地工作区路径映射(AI 排查时直读代码)")
    digest: bool = Field(default=True, description="是否参与每日 digest 汇总")

    @field_validator("owner", "repo")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("owner/repo 不能为空")
        return v

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


class GitHubConfig(ChannelConfig):
    """GitHub 频道配置。"""

    enabled: bool = Field(default=False, description="是否启用 GitHub 频道")

    # ---- 接收模式 ----
    mode: str = Field(default="poll", description="接收模式: poll(轮询) | webhook(公网回调) | both")

    # ---- 轮询 ----
    default_poll_interval_sec: int = Field(default=300, description="默认每仓轮询间隔秒(下限 60)")
    default_events: List[str] = Field(
        default_factory=lambda: list(DEFAULT_EVENTS),
        description="默认订阅事件清单(仓库未单独配置时生效)",
    )

    # ---- webhook ----
    webhook_secret: str = Field(
        default="", description="Webhook 验签密钥(webhook 模式必填,fail-closed)",
        json_schema_extra={"value_type": "password"},
    )
    webhook_queue_size: int = Field(default=500, description="webhook 事件队列容量(满则丢最旧并计数)")

    # ---- 鉴权(token 本体存 provider_keys.json,这里只存引用名) ----
    auth_mode: str = Field(default="pat", description="鉴权形态: pat | app | none(匿名只读公开仓)")
    token_ref: str = Field(default="github", description="provider_keys.json 中 PAT 的登记名")
    app_id: str = Field(default="", description="GitHub App ID(auth_mode=app 时)")
    app_private_key_ref: str = Field(
        default="github_app", description="provider_keys.json 中 App 私钥(PEM)的登记名",
    )
    api_base_url: str = Field(
        default="https://api.github.com", description="REST API 根地址(GHES 企业版可改)",
    )

    # ---- 推送策略 ----
    aggregate_window_sec: int = Field(default=300, description="NORMAL 事件防抖聚合窗秒数")
    aggregate_max_events: int = Field(default=20, description="单条聚合消息的事件数上限(超出拆条)")
    body_digest_chars: int = Field(default=500, description="注入消息中外部正文的截断长度")
    quiet_hours: str = Field(
        default="", description="安静时段(如 '23:00-08:00'):期间 IMMEDIATE 降 NORMAL、NORMAL 入 digest",
    )
    watch_mentions_of: str = Field(
        default="", description="@提及升级 IMMEDIATE 的 GitHub 登录名(空=不检测提及)",
    )
    reply_relay: str = Field(
        default="webui:web_user",
        description="AI 在 GitHub 会话中回复的转述目标('adapter:target',如 'webui:web_user'/'qq:12345';空=不转述)",
    )

    # ---- 订阅清单 ----
    repos: List[RepoSubscription] = Field(default_factory=list, description="监听的仓库订阅清单")

    # ---- 写操作 ----
    allow_write: bool = Field(
        default=False, description="是否允许 AI 写操作(评论/开 issue/打标签;reaction 不受此限)",
    )

    # ---- 预算 ----
    rate_limit_reserve: int = Field(default=500, description="API 剩余配额低于此值时暂停轮询并告警")

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        v = (v or "poll").strip().lower()
        if v not in ("poll", "webhook", "both"):
            raise ValueError(f"非法接收模式: {v}(可选 poll/webhook/both)")
        return v

    @field_validator("auth_mode")
    @classmethod
    def _valid_auth_mode(cls, v: str) -> str:
        v = (v or "pat").strip().lower()
        if v not in ("pat", "app", "none"):
            raise ValueError(f"非法鉴权形态: {v}(可选 pat/app/none)")
        return v

    @property
    def poll_enabled(self) -> bool:
        return self.mode in ("poll", "both")

    @property
    def webhook_enabled(self) -> bool:
        return self.mode in ("webhook", "both")

    def subscription_for(self, repo_full_name: str) -> RepoSubscription | None:
        """按 'owner/repo' 查订阅(大小写不敏感,GitHub 仓名不区分大小写)。"""
        target = repo_full_name.strip().lower()
        for sub in self.repos:
            if sub.full_name.lower() == target:
                return sub
        return None


CONFIG_MODEL = GitHubConfig
