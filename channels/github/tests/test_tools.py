"""GitHub 工具面测试(假客户端,不触网)。

覆盖:订阅自助(新增/更新合并/取消/校验)/ 写操作 allow_write 门控 /
reaction 校验 / 读工具响应塑形 / 配额查询 / digest 读取。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from channels.github.adapter import GitHubChannel
from channels.github.client import ApiResponse, NotFoundError
from channels.github.config import GitHubConfig, RepoSubscription


class _FakeClient:
    def __init__(self) -> None:
        self.calls: List[tuple[str, str, Any]] = []
        self.canned: Dict[str, ApiResponse] = {}
        self.errors: Dict[str, Exception] = {}

    def _record(self, method: str, path: str, kwargs: Dict[str, Any]) -> ApiResponse:
        self.calls.append((method, path, kwargs))
        if path in self.errors:
            raise self.errors[path]
        return self.canned.get(path, ApiResponse(status=200, data={}))

    async def get(self, path: str, **kw: Any) -> ApiResponse:
        return self._record("GET", path, kw)

    async def post(self, path: str, **kw: Any) -> ApiResponse:
        return self._record("POST", path, kw)

    async def patch(self, path: str, **kw: Any) -> ApiResponse:
        return self._record("PATCH", path, kw)


def _channel(config: GitHubConfig) -> GitHubChannel:
    channel = GitHubChannel()
    channel._config = config
    channel._client = _FakeClient()
    return channel


def _result(raw: str) -> Dict[str, Any]:
    return json.loads(raw)


class TestSubscriptionTools:
    async def test_subscribe_appends_and_writes_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        written: Dict[str, Any] = {}
        monkeypatch.setattr("agent.channel.config.set_channel_config",
                            lambda cid, **kw: written.update(kw))
        channel = _channel(GitHubConfig(repos=[]))
        result = _result(await channel.subscribe_repo(
            "KroMiose/nekro-agent", events="push,issues", local_path="/tmp/nekro"))
        assert result["success"] and not result["updated"]
        repos = written["repos"]
        assert len(repos) == 1
        assert repos[0]["owner"] == "KroMiose" and repos[0]["repo"] == "nekro-agent"
        assert repos[0]["events"] == ["push", "issues"]
        assert repos[0]["local_path"] == "/tmp/nekro"

    async def test_subscribe_existing_merges(self, monkeypatch: pytest.MonkeyPatch) -> None:
        written: Dict[str, Any] = {}
        monkeypatch.setattr("agent.channel.config.set_channel_config",
                            lambda cid, **kw: written.update(kw))
        channel = _channel(GitHubConfig(repos=[RepoSubscription(
            owner="o", repo="r", local_path="/old", priority_boost=False)]))
        result = _result(await channel.subscribe_repo("o/r", priority_boost=True))
        assert result["success"] and result["updated"]
        merged = written["repos"][0]
        assert merged["local_path"] == "/old"      # 未显式给出的旧值保留
        assert merged["priority_boost"] is True    # 显式给出的覆盖

    async def test_subscribe_invalid_repo_rejected(self) -> None:
        channel = _channel(GitHubConfig())
        result = _result(await channel.subscribe_repo("no-slash"))
        assert not result["success"]
        assert "owner/repo" in result["error"]

    async def test_unsubscribe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        channel = _channel(GitHubConfig(repos=[RepoSubscription(owner="o", repo="r")]))

        def _fake_set(cid: str, **kw: Any) -> None:
            # 模拟真实热更:写配置后频道内存态经变更监听重新物化
            channel._config = GitHubConfig(**{**channel.config.model_dump(), **kw})

        monkeypatch.setattr("agent.channel.config.set_channel_config", _fake_set)
        assert _result(await channel.unsubscribe_repo("o/r"))["success"]
        assert channel.config.repos == []
        assert not _result(await channel.unsubscribe_repo("o/r"))["success"]  # 已不存在

    async def test_list_subscriptions(self) -> None:
        channel = _channel(GitHubConfig(repos=[RepoSubscription(owner="o", repo="r")]))
        result = _result(await channel.list_subscriptions())
        assert result["count"] == 1 and result["repos"][0]["repo"] == "r"


class TestWriteGate:
    async def test_write_blocked_when_allow_write_false(self) -> None:
        channel = _channel(GitHubConfig(allow_write=False))
        result = _result(await channel.create_comment("o/r", 1, "hello"))
        assert not result["success"]
        assert "allow_write" in result["error"]
        assert channel._client.calls == []  # 门控在出网前拦截

    async def test_write_allowed_when_enabled(self) -> None:
        channel = _channel(GitHubConfig(allow_write=True))
        channel._client.canned["/repos/o/r/issues/1/comments"] = ApiResponse(
            status=201, data={"id": 7, "html_url": "https://x/1#issuecomment-7"})
        result = _result(await channel.create_comment("o/r", 1, "修复了,感谢反馈"))
        assert result["success"] and result["comment_id"] == 7
        method, path, kw = channel._client.calls[0]
        assert method == "POST" and kw["json_body"] == {"body": "修复了,感谢反馈"}

    async def test_create_comment_empty_body_rejected(self) -> None:
        channel = _channel(GitHubConfig(allow_write=True))
        assert not _result(await channel.create_comment("o/r", 1, "  "))["success"]

    async def test_react_validation_and_paths(self) -> None:
        channel = _channel(GitHubConfig())  # react 不受 allow_write 限制
        bad = _result(await channel.react("o/r", 1, "illegal"))
        assert not bad["success"]
        channel._client.canned["/repos/o/r/issues/2/reactions"] = ApiResponse(
            status=201, data={"id": 1, "content": "+1"})
        ok = _result(await channel.react("o/r", 2, "+1"))
        assert ok["success"]
        # comment_id 路径分流
        channel._client.canned["/repos/o/r/issues/comments/55/reactions"] = ApiResponse(
            status=201, data={"id": 2, "content": "eyes"})
        ok2 = _result(await channel.react("o/r", 2, "eyes", comment_id=55))
        assert ok2["success"]
        assert channel._client.calls[-1][1] == "/repos/o/r/issues/comments/55/reactions"

    async def test_close_issue_with_comment(self) -> None:
        channel = _channel(GitHubConfig(allow_write=True))
        channel._client.canned["/repos/o/r/issues/9"] = ApiResponse(
            status=200, data={"number": 9, "state": "closed", "html_url": "https://x/9"})
        result = _result(await channel.close_issue("o/r", 9, comment="已在 v2 修复"))
        assert result["success"] and result["state"] == "closed"
        paths = [c[1] for c in channel._client.calls]
        assert "/repos/o/r/issues/9/comments" in paths  # 先评论
        assert paths[-1] == "/repos/o/r/issues/9"       # 后关闭


class TestReadTools:
    async def test_get_issue_shaped(self) -> None:
        channel = _channel(GitHubConfig())
        channel._client.canned["/repos/o/r/issues/45"] = ApiResponse(status=200, data={
            "number": 45, "title": "登录偶发 500", "state": "open",
            "user": {"login": "bob"}, "labels": [{"name": "bug"}],
            "body": "正文", "html_url": "https://x/45"})
        channel._client.canned["/repos/o/r/issues/45/comments"] = ApiResponse(status=200, data=[
            {"user": {"login": "alice"}, "body": "我也遇到", "created_at": "2026-10-01"}])
        result = _result(await channel.get_issue("o/r", 45))
        assert result["success"]
        assert result["number"] == 45 and result["labels"] == ["bug"]
        assert result["comments"][0]["user"] == "alice"

    async def test_get_issue_404(self) -> None:
        channel = _channel(GitHubConfig())
        channel._client.errors["/repos/o/r/issues/999"] = NotFoundError("nf", status=404)
        result = _result(await channel.get_issue("o/r", 999))
        assert not result["success"] and "不存在" in result["error"]

    async def test_list_issues_filters_prs(self) -> None:
        channel = _channel(GitHubConfig())
        channel._client.canned["/repos/o/r/issues"] = ApiResponse(status=200, data=[
            {"number": 1, "title": "真 issue", "user": {"login": "a"}, "labels": []},
            {"number": 2, "title": "这是 PR", "pull_request": {"url": "x"},
             "user": {"login": "b"}, "labels": []},
        ])
        result = _result(await channel.list_issues("o/r"))
        assert result["count"] == 1 and result["issues"][0]["number"] == 1

    async def test_get_pr_diff_truncates(self) -> None:
        channel = _channel(GitHubConfig())
        channel._client.canned["/repos/o/r/pulls/3"] = ApiResponse(
            status=200, data="d" * 20000)
        result = _result(await channel.get_pr_diff("o/r", 3, max_chars=1000))
        assert result["success"] and result["truncated"]
        assert len(result["diff"]) < 1200

    async def test_get_workflow_runs(self) -> None:
        channel = _channel(GitHubConfig())
        channel._client.canned["/repos/o/r/actions/runs"] = ApiResponse(status=200, data={
            "total_count": 1, "workflow_runs": [{
                "name": "CI", "status": "completed", "conclusion": "failure",
                "head_branch": "main", "event": "push", "actor": {"login": "a"},
                "created_at": "2026-10-10", "html_url": "https://x"}]})
        result = _result(await channel.get_workflow_runs("o/r", status="failure"))
        assert result["success"] and result["runs"][0]["conclusion"] == "failure"

    async def test_get_rate_limit(self) -> None:
        channel = _channel(GitHubConfig())
        channel._client.canned["/rate_limit"] = ApiResponse(status=200, data={
            "resources": {"core": {"limit": 5000, "remaining": 4990, "reset": 1},
                          "search": {"limit": 30, "remaining": 29, "reset": 1}}})
        result = _result(await channel.get_rate_limit())
        assert result["core"]["remaining"] == 4990

    async def test_get_daily_digest(self) -> None:
        import time as _time

        channel = _channel(GitHubConfig())
        today = _time.strftime("%Y-%m-%d")
        channel._stats.record("o/r", "star.created", {"repo": "o/r"}, day=today)
        channel._digest.add("o/r", "- 10:00 star.created 标星 by fan", day=today)
        result = _result(await channel.get_daily_digest())
        assert result["counts"]["o/r"]["star.created"] == 1
        assert result["buffer"][today]["o/r"]

    async def test_list_events_from_recent_ring(self) -> None:
        import time as _time

        channel = _channel(GitHubConfig())
        channel._stats.record("o/r", "push", {
            "ts": _time.time(), "repo": "o/r", "name": "push", "title": "push main · 1 个 commit",
        }, day="2026-10-10")
        channel._stats.record("x/y", "push", {
            "ts": _time.time(), "repo": "x/y", "name": "push", "title": "other",
        }, day="2026-10-10")
        result = _result(await channel.list_events(repo="o/r"))
        assert result["count"] == 1 and result["events"][0]["repo"] == "o/r"
        result_all = _result(await channel.list_events())
        assert result_all["count"] == 2
