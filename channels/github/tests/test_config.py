"""GitHub 频道配置测试。

覆盖:默认值 / 非法值校验 / 订阅嵌套模型 / 大小写不敏感匹配 / 模式开关属性。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from channels.github.config import GitHubConfig, RepoSubscription


class TestDefaults:
    def test_defaults_safe(self) -> None:
        cfg = GitHubConfig()
        assert cfg.enabled is False               # 默认不启用
        assert cfg.mode == "poll"                 # 默认轮询(零公网依赖)
        assert cfg.auth_mode == "pat"
        assert cfg.allow_write is False           # 写操作默认关
        assert cfg.poll_enabled and not cfg.webhook_enabled
        assert cfg.default_events                 # 有默认订阅集
        assert cfg.default_poll_interval_sec >= 60

    def test_mode_validation(self) -> None:
        assert GitHubConfig(mode="both").webhook_enabled
        assert GitHubConfig(mode="both").poll_enabled
        assert GitHubConfig(mode="WEBHOOK").webhook_enabled  # 大小写归一
        with pytest.raises(ValidationError):
            GitHubConfig(mode="carrier-pigeon")

    def test_auth_mode_validation(self) -> None:
        assert GitHubConfig(auth_mode="app").auth_mode == "app"
        with pytest.raises(ValidationError):
            GitHubConfig(auth_mode="password")


class TestRepoSubscription:
    def test_full_name(self) -> None:
        sub = RepoSubscription(owner="KroMiose", repo="nekro-agent")
        assert sub.full_name == "KroMiose/nekro-agent"

    def test_empty_owner_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RepoSubscription(owner="", repo="r")
        with pytest.raises(ValidationError):
            RepoSubscription(owner="o", repo="  ")

    def test_nested_repos_from_dicts(self) -> None:
        cfg = GitHubConfig(repos=[{"owner": "o", "repo": "r", "priority_boost": True}])
        assert cfg.repos[0].full_name == "o/r"
        assert cfg.repos[0].priority_boost is True
        assert cfg.repos[0].digest is True

    def test_subscription_for_case_insensitive(self) -> None:
        cfg = GitHubConfig(repos=[RepoSubscription(owner="KroMiose", repo="Nekro-Agent")])
        assert cfg.subscription_for("kromiose/nekro-agent") is not None
        assert cfg.subscription_for("other/repo") is None
