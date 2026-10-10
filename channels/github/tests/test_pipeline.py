"""GitHub 事件管线测试(不触网,不触盘)。

覆盖:三档路由(立即/防抖聚合/静默 digest)/ push 同分支合并 /
IMMEDIATE 冲刷顺序 / 安静时段降级 / 重点仓升级 / @提及升级 /
订阅过滤 / 洪峰拆条 / 停止冲刷。
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from typing import Any, List

from channels.github.config import GitHubConfig, RepoSubscription
from channels.github.events import from_webhook
from channels.github.pipeline import EventPipeline, in_quiet_hours, parse_quiet_hours
from channels.github.state import DigestBuffer, EventStats


class _FakeChannel:
    """捕获派发的频道替身(管线只依赖 config 与 dispatch_events)。"""

    def __init__(self, config: GitHubConfig) -> None:
        self.config = config
        self.dispatched: List[tuple[str, str]] = []

    async def dispatch_events(self, repo: str, events: list, *, text: str) -> None:
        self.dispatched.append((repo, text))


def _make_pipeline(config: GitHubConfig, tmp_dir: str = ""
                   ) -> tuple[EventPipeline, _FakeChannel, DigestBuffer, EventStats]:
    # 缺省落临时目录:测试绝不写真实数据目录(workflow.sqlite3 事故教训)
    tmp_dir = tmp_dir or tempfile.mkdtemp(prefix="github-pipeline-test-")
    channel = _FakeChannel(config)
    digest = DigestBuffer(directory=tmp_dir)
    stats = EventStats(directory=tmp_dir)
    return EventPipeline(channel, digest=digest, stats=stats), channel, digest, stats


def _sub(**kw: Any) -> RepoSubscription:
    return RepoSubscription(owner="o", repo="r", **kw)


def _issue_event(number: int = 1) -> Any:
    return from_webhook("issues", {
        "action": "opened",
        "issue": {"number": number, "title": f"问题 {number}",
                  "html_url": f"https://x/{number}", "body": "正文", "labels": []},
        "repository": {"full_name": "o/r"}, "sender": {"login": "bob"},
    })


def _release_event() -> Any:
    return from_webhook("release", {
        "action": "published",
        "release": {"tag_name": "v1.0", "html_url": "https://x", "body": ""},
        "repository": {"full_name": "o/r"}, "sender": {"login": "alice"},
    })


def _push_event(branch: str = "main", msg: str = "c1") -> Any:
    return from_webhook("push", {
        "ref": f"refs/heads/{branch}", "compare": "https://x",
        "commits": [{"message": msg, "author": {"name": "a"}}],
        "repository": {"full_name": "o/r"}, "sender": {"login": "a"},
    })


def _star_event() -> Any:
    return from_webhook("star", {
        "action": "created", "repository": {"full_name": "o/r"}, "sender": {"login": "fan"},
    })


class TestQuietHours:
    def test_parse_and_wrap_overnight(self) -> None:
        parsed = parse_quiet_hours("23:00-08:00")
        assert parsed == (23 * 60, 8 * 60)
        night = time.mktime(time.strptime("2026-10-10 23:30", "%Y-%m-%d %H:%M"))
        noon = time.mktime(time.strptime("2026-10-10 12:00", "%Y-%m-%d %H:%M"))
        morning = time.mktime(time.strptime("2026-10-10 07:59", "%Y-%m-%d %H:%M"))
        assert parsed is not None
        assert in_quiet_hours(parsed, night)
        assert in_quiet_hours(parsed, morning)
        assert not in_quiet_hours(parsed, noon)

    def test_parse_invalid_and_empty(self) -> None:
        assert parse_quiet_hours("") is None
        assert parse_quiet_hours("23点-8点") is None
        assert parse_quiet_hours("23:00") is None

    def test_same_start_end_never_quiet(self) -> None:
        parsed = parse_quiet_hours("08:00-08:00")
        assert parsed is not None
        assert not in_quiet_hours(parsed)


class TestRouting:
    async def test_immediate_dispatches_at_once(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(GitHubConfig(repos=[_sub()]))
        ev = await pipeline.submit_raw(_release_event())
        assert ev is not None
        assert len(channel.dispatched) == 1
        assert "release.published" in channel.dispatched[0][1]

    async def test_normal_buffered_then_aggregated(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(
            GitHubConfig(repos=[_sub()], aggregate_window_sec=0))
        await pipeline.submit_raw(_issue_event(1))
        await pipeline.submit_raw(_issue_event(2))
        assert channel.dispatched == []  # 窗内不派发
        await asyncio.sleep(1.2)
        assert len(channel.dispatched) == 1
        text = channel.dispatched[0][1]
        assert "汇总" in text and "2 条" in text
        assert "问题 1" in text and "问题 2" in text
        await pipeline.stop()

    async def test_immediate_flushes_pending_normal_first(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(
            GitHubConfig(repos=[_sub()], aggregate_window_sec=999))
        await pipeline.submit_raw(_issue_event(1))   # 进防抖窗
        await pipeline.submit_raw(_release_event())  # IMMEDIATE → 冲刷+合并派发
        assert len(channel.dispatched) == 1
        text = channel.dispatched[0][1]
        assert "issues.opened" in text and "release.published" in text  # 时序保住
        await pipeline.stop()

    async def test_digest_only_buffered_not_dispatched(self) -> None:
        pipeline, channel, digest, _ = _make_pipeline(GitHubConfig(repos=[_sub()]))
        ev = await pipeline.submit_raw(_star_event())
        assert ev is not None
        assert channel.dispatched == []
        today = time.strftime("%Y-%m-%d")
        rows = digest.snapshot()[today]["o/r"]
        assert any("star.created" in r for r in rows)
        await pipeline.stop()

    async def test_quiet_hours_demote_immediate_to_normal(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(GitHubConfig(
            repos=[_sub()], aggregate_window_sec=0, quiet_hours="00:00-23:59"))
        ev = await pipeline.submit_raw(_release_event())
        assert ev is not None and ev.priority.value == "normal"
        assert channel.dispatched == []  # 降级后进防抖窗而非立即派发
        await pipeline.stop()

    async def test_priority_boost_repo(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(
            GitHubConfig(repos=[_sub(priority_boost=True)]))
        ev = await pipeline.submit_raw(_issue_event(1))
        assert ev is not None and ev.priority.value == "immediate"
        assert len(channel.dispatched) == 1
        await pipeline.stop()

    async def test_mention_boost(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(GitHubConfig(
            repos=[_sub()], watch_mentions_of="me"))
        raw = from_webhook("issue_comment", {
            "action": "created",
            "issue": {"number": 5, "title": "t"},
            "comment": {"body": "@me 帮忙看下", "html_url": "https://x",
                        "user": {"login": "c"}},
            "repository": {"full_name": "o/r"}, "sender": {"login": "c"},
        })
        ev = await pipeline.submit_raw(raw)
        assert ev is not None and ev.mention and ev.priority.value == "immediate"
        assert len(channel.dispatched) == 1
        await pipeline.stop()

    async def test_unsubscribed_repo_ignored(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(GitHubConfig(repos=[_sub()]))
        raw = from_webhook("push", {
            "ref": "refs/heads/main", "commits": [{"message": "x"}],
            "repository": {"full_name": "other/repo"}, "sender": {"login": "a"}})
        assert await pipeline.submit_raw(raw) is None
        assert pipeline.ignored_count == 1
        await pipeline.stop()

    async def test_unsubscribed_event_family_ignored(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(GitHubConfig(
            repos=[_sub(events=["push"])]))
        assert await pipeline.submit_raw(_issue_event(1)) is None
        assert channel.dispatched == []
        await pipeline.stop()

    async def test_push_branch_filter(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(GitHubConfig(
            repos=[_sub(branches=["main"])]))
        assert await pipeline.submit_raw(_push_event(branch="dev")) is None
        ev = await pipeline.submit_raw(_push_event(branch="main"))
        assert ev is not None
        await pipeline.stop()

    async def test_push_coalescing_same_branch(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(
            GitHubConfig(repos=[_sub()], aggregate_window_sec=0))
        await pipeline.submit_raw(_push_event(msg="c1"))
        await pipeline.submit_raw(_push_event(msg="c2"))
        await pipeline.submit_raw(_push_event(msg="c3"))
        await asyncio.sleep(1.2)
        assert len(channel.dispatched) == 1
        text = channel.dispatched[0][1]
        assert "聚合" in text and "3 个 commit" in text
        assert "c1" in text and "c3" in text
        await pipeline.stop()

    async def test_aggregate_max_events_triggers_flush(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(GitHubConfig(
            repos=[_sub()], aggregate_window_sec=999, aggregate_max_events=2))
        await pipeline.submit_raw(_issue_event(1))
        await pipeline.submit_raw(_issue_event(2))  # 达到上限立即冲刷
        assert len(channel.dispatched) == 1
        assert "2 条" in channel.dispatched[0][1]
        await pipeline.stop()

    async def test_stop_flushes_pending(self) -> None:
        pipeline, channel, _, _ = _make_pipeline(
            GitHubConfig(repos=[_sub()], aggregate_window_sec=999))
        await pipeline.submit_raw(_issue_event(1))
        await pipeline.stop()  # 停止冲刷残余
        assert len(channel.dispatched) == 1

    async def test_stats_and_recent_ring(self) -> None:
        pipeline, channel, _, stats = _make_pipeline(GitHubConfig(repos=[_sub()]))
        await pipeline.submit_raw(_release_event())
        await pipeline.submit_raw(_star_event())
        today = time.strftime("%Y-%m-%d")
        counts = stats.today_counts(day=today)
        assert counts["o/r"]["release.published"] == 1
        assert counts["o/r"]["star.created"] == 1
        recent = stats.recent_events(repo="o/r")
        assert len(recent) == 2 and recent[0]["name"] == "star"  # 新→旧
        await pipeline.stop()
