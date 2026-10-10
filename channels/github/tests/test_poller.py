"""GitHub 轮询器测试(假客户端,不触网不触盘)。

覆盖:首次播种不重放 / 增量派发保序 / ETag 304 / release 盯梢 /
凭据失效暂停+单次告警 / 限流全局暂停 / 连续失败熔断 / 配额守卫。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from channels.github.client import ApiResponse, AuthError, NotFoundError, RateLimitError
from channels.github.config import GitHubConfig, RepoSubscription
from channels.github.poller import GitHubPoller


class _FakeClient:
    """按路径返回罐装响应的假客户端。"""

    def __init__(self) -> None:
        self.responses: Dict[str, Any] = {}
        self.etags_seen: List[str] = []
        self.calls: List[str] = []
        self.rate_remaining = -1
        self.rate_reset_at = 0.0

    async def get(self, path: str, *, params: Any = None, etag: str = "",
                  headers: Any = None) -> ApiResponse:
        self.calls.append(path)
        self.etags_seen.append(etag)
        resp = self.responses.get(path)
        if isinstance(resp, Exception):
            raise resp
        if resp is None:
            raise NotFoundError(f"no canned: {path}", status=404)
        if etag and resp.etag and etag == resp.etag:
            return ApiResponse(status=304, data=None, not_modified=True, etag=etag)
        return resp


class _FakePipeline:
    def __init__(self) -> None:
        self.received: List[Any] = []

    async def submit_raw(self, raw: Any) -> Optional[Any]:
        self.received.append(raw)
        return raw


class _FakeChannel:
    def __init__(self, config: GitHubConfig) -> None:
        self.config = config
        self.alerts: List[str] = []

    async def alert_repo(self, repo: str, message: str) -> None:
        self.alerts.append(f"{repo}: {message}")


def _sub(**kw: Any) -> RepoSubscription:
    return RepoSubscription(owner="o", repo="r", **kw)


def _events_payload(ids: List[int], etag: str = "") -> ApiResponse:
    # etag 缺省随内容变化(真实 GitHub 行为:内容变 etag 变);304 测试显式传同值
    etag = etag or f'"ev-{ids[-1] if ids else 0}"'
    return ApiResponse(status=200, etag=etag, data=[
        {"id": str(i), "type": "PushEvent", "repo": {"name": "o/r"},
         "actor": {"login": "a"}, "created_at": "2026-10-10T00:00:00Z",
         "payload": {"ref": "refs/heads/main", "size": 1,
                     "commits": [{"message": f"c{i}"}]}}
        for i in ids  # API 返回最新在前
    ][::-1])


def _make_poller(config: Optional[GitHubConfig] = None
                 ) -> tuple[GitHubPoller, _FakeClient, _FakePipeline, _FakeChannel]:
    cfg = config or GitHubConfig(repos=[_sub()])
    channel = _FakeChannel(cfg)
    client = _FakeClient()
    pipeline = _FakePipeline()
    return GitHubPoller(channel, client, pipeline), client, pipeline, channel


class TestSeedingAndIncrement:
    async def test_first_poll_seeds_without_dispatch(self) -> None:
        poller, client, pipeline, _ = _make_poller()
        client.responses["/repos/o/r/events"] = _events_payload([1, 2, 3])
        await poller._poll_repo(_sub())
        assert pipeline.received == []  # 历史不重放
        assert poller._cursors.is_seeded("o/r")

    async def test_second_poll_dispatches_new_oldest_first(self) -> None:
        poller, client, pipeline, _ = _make_poller()
        client.responses["/repos/o/r/events"] = _events_payload([1, 2])
        await poller._poll_repo(_sub())  # 播种
        client.responses["/repos/o/r/events"] = _events_payload([2, 3, 4])
        await poller._poll_repo(_sub())
        got = [r.event_id for r in pipeline.received]
        assert got == ["3", "4"]  # 只增量,最旧先发

    async def test_dispatch_failure_not_marked_seen(self) -> None:
        poller, client, pipeline, _ = _make_poller()
        client.responses["/repos/o/r/events"] = _events_payload([1])
        await poller._poll_repo(_sub())  # 播种

        class _FlakyPipeline(_FakePipeline):
            async def submit_raw(self, raw: Any) -> None:
                raise RuntimeError("boom")

        poller._pipeline = _FlakyPipeline()
        client.responses["/repos/o/r/events"] = _events_payload([1, 2])
        await poller._poll_repo(_sub())
        assert not poller._cursors.is_seen("events", "2")  # 失败条目不标已见

    async def test_etag_304_short_circuits(self) -> None:
        poller, client, pipeline, _ = _make_poller()
        client.responses["/repos/o/r/events"] = _events_payload([1], etag='"ev1"')
        await poller._poll_repo(_sub())
        await poller._poll_repo(_sub())  # 第二次带 etag,假客户端回 304
        events_etags = [e for p, e in zip(client.calls, client.etags_seen, strict=True)
                        if p == "/repos/o/r/events"]
        assert events_etags[1] == '"ev1"'  # 第二轮带上了首轮存下的 ETag
        assert pipeline.received == []


class TestReleaseWatch:
    async def test_release_seeded_then_dispatched_on_change(self) -> None:
        poller, client, pipeline, _ = _make_poller()
        client.responses["/repos/o/r/events"] = _events_payload([1])
        rel_v1 = ApiResponse(status=200, etag='"r1"', data={
            "id": 100, "tag_name": "v1.0", "published_at": "2026-10-01T00:00:00Z",
            "html_url": "https://x", "author": {"login": "a"}})
        client.responses["/repos/o/r/releases/latest"] = rel_v1
        await poller._poll_repo(_sub())  # 事件播种 + release 播种
        assert [r for r in pipeline.received if r.name == "release"] == []

        rel_v2 = ApiResponse(status=200, etag='"r2"', data={
            "id": 101, "tag_name": "v2.0", "published_at": "2026-10-10T00:00:00Z",
            "html_url": "https://x", "author": {"login": "a"}, "body": "日志"})
        client.responses["/repos/o/r/releases/latest"] = rel_v2
        await poller._poll_repo(_sub())
        releases = [r for r in pipeline.received if r.name == "release"]
        assert len(releases) == 1
        assert releases[0].payload["release"]["tag_name"] == "v2.0"

    async def test_repo_without_releases_404_ignored(self) -> None:
        poller, client, pipeline, _ = _make_poller()
        client.responses["/repos/o/r/events"] = _events_payload([1])
        # releases/latest 无罐装 → NotFoundError,不应影响事件流
        await poller._poll_repo(_sub())
        state = poller._repo_state["o/r"]
        assert not state.get("fail_count")  # 404 不算失败


class TestFailureModes:
    async def test_auth_error_pauses_repo_and_alerts_once(self) -> None:
        poller, client, pipeline, channel = _make_poller()
        client.responses["/repos/o/r/events"] = AuthError("bad credentials", status=401)
        await poller._poll_guarded(_sub())
        state = poller._repo_state["o/r"]
        assert state["paused_until"] > time.time() + 86400  # 长期暂停
        assert len(channel.alerts) == 1
        await poller._poll_guarded(_sub())  # 仍暂停中(直接跳过)
        assert len(channel.alerts) == 1  # 不重复告警

    async def test_rate_limit_pauses_globally(self) -> None:
        poller, client, _, _ = _make_poller()
        client.responses["/repos/o/r/events"] = RateLimitError("限流", retry_after=300)
        await poller._poll_guarded(_sub())
        assert poller._global_paused_until >= time.time() + 250

    async def test_circuit_breaker_after_five_failures(self) -> None:
        poller, client, _, _ = _make_poller()
        client.responses["/repos/o/r/events"] = GitHubApiErrorX()
        sub = _sub()
        for _ in range(5):
            await poller._poll_guarded(sub)
        state = poller._repo_state["o/r"]
        assert state["paused_until"] > time.time() + 1500  # 熔断 30 分钟
        assert state["fail_count"] == 0  # 熔断后归零

    async def test_quota_guard_skips_tick(self) -> None:
        cfg = GitHubConfig(repos=[_sub()], rate_limit_reserve=500)
        poller, client, pipeline, _ = _make_poller(cfg)
        client.rate_remaining = 100
        client.rate_reset_at = time.time() + 600
        await poller._tick()
        assert client.calls == []  # 配额不足,一轮都没发
        assert poller._global_paused_until > time.time()


class GitHubApiErrorX(Exception):
    """测试用通用异常(模拟非分类错误)。"""
