"""GitHub 事件规范化测试(不触网)。

覆盖:两种来源归一 / 渲染器 / 优先级表与 override / 订阅匹配三档 /
分支过滤 / 正文消毒 / 提及检测 / 消息格式化。
"""

from __future__ import annotations

import time

from channels.github.events import (
    EventPriority,
    branch_allowed,
    contains_mention,
    event_subscribed,
    format_event_message,
    from_poll_api,
    from_webhook,
    render_event,
    sanitize_body,
)

# ------------------------------------------------------------------
# payload 样例工厂
# ------------------------------------------------------------------


def _push_payload() -> dict:
    return {
        "ref": "refs/heads/main",
        "compare": "https://github.com/o/r/compare/aaa...bbb",
        "commits": [
            {"id": "aaa", "message": "fix: 登录偶发 500\n\n详细说明", "author": {"name": "alice"}},
            {"id": "bbb", "message": "docs: 更新 README", "author": {"name": "bob"}},
        ],
        "repository": {"full_name": "o/r", "html_url": "https://github.com/o/r"},
        "sender": {"login": "alice"},
    }


def _issue_payload(action: str = "opened") -> dict:
    return {
        "action": action,
        "issue": {
            "number": 45, "title": "登录偶发 500",
            "html_url": "https://github.com/o/r/issues/45",
            "body": "正文![截图](https://x.com/track.png)内容",
            "labels": [{"name": "bug"}],
        },
        "repository": {"full_name": "o/r"},
        "sender": {"login": "bob"},
    }


# ------------------------------------------------------------------
# 来源归一
# ------------------------------------------------------------------


class TestSourceNormalization:
    def test_from_webhook_extracts_repo_actor_action(self) -> None:
        raw = from_webhook("issues", _issue_payload(), "delivery-1")
        assert raw.name == "issues"
        assert raw.action == "opened"
        assert raw.repo_full_name == "o/r"
        assert raw.actor == "bob"
        assert raw.event_id == "delivery-1"
        assert raw.source == "webhook"

    def test_from_poll_api_maps_type_and_top_level_fields(self) -> None:
        raw = from_poll_api({
            "id": "12345", "type": "PushEvent",
            "repo": {"name": "o/r"}, "actor": {"login": "alice"},
            "created_at": "2026-10-10T06:32:00Z",
            "payload": {"ref": "refs/heads/main", "size": 1,
                        "commits": [{"message": "x", "author": {"name": "a"}}]},
        })
        assert raw.name == "push"
        assert raw.repo_full_name == "o/r"
        assert raw.actor == "alice"
        assert raw.event_id == "12345"
        assert raw.source == "poll"
        assert raw.occurred_at > 0

    def test_from_poll_api_watch_event_gets_started_action(self) -> None:
        raw = from_poll_api({"id": "1", "type": "WatchEvent",
                             "repo": {"name": "o/r"}, "payload": {}})
        assert raw.name == "watch"
        assert raw.action == "started"

    def test_from_poll_api_unknown_type_falls_back(self) -> None:
        raw = from_poll_api({"id": "1", "type": "SponsorshipEvent",
                             "repo": {"name": "o/r"}, "payload": {}})
        assert raw.name == "sponsorship"


# ------------------------------------------------------------------
# 渲染器
# ------------------------------------------------------------------


class TestRenderers:
    def test_push_webhook_shape(self) -> None:
        ev = render_event(from_webhook("push", _push_payload()))
        assert ev is not None
        assert ev.event_name == "push"
        assert "main" in ev.title and "2 个 commit" in ev.title
        assert ev.extra["branch"] == "main"
        assert ev.extra["size"] == 2
        assert "fix: 登录偶发 500" in ev.body_digest
        assert ev.url.endswith("aaa...bbb")
        assert ev.priority == EventPriority.NORMAL

    def test_push_poll_shape(self) -> None:
        ev = render_event(from_poll_api({
            "id": "9", "type": "PushEvent", "repo": {"name": "o/r"},
            "actor": {"login": "alice"}, "created_at": "2026-10-10T00:00:00Z",
            "payload": {"ref": "refs/heads/dev", "size": 3,
                        "commits": [{"message": "a"}, {"message": "b"}, {"message": "c"}]},
        }))
        assert ev is not None
        assert "dev" in ev.title and "3 个 commit" in ev.title

    def test_push_zero_size_dropped(self) -> None:
        payload = _push_payload()
        payload["commits"] = []
        payload["size"] = 0
        payload["ref"] = "refs/heads/gone"
        assert render_event(from_webhook("push", payload)) is None

    def test_issues_opened_normal_with_sanitized_body(self) -> None:
        ev = render_event(from_webhook("issues", _issue_payload("opened")))
        assert ev is not None
        assert ev.priority == EventPriority.NORMAL
        assert "issue #45" in ev.title and "登录偶发 500" in ev.title
        assert "[bug]" in ev.title
        assert "track.png" not in ev.body_digest  # 图片语法已剥除
        assert "[图片已略]" in ev.body_digest

    def test_issues_labeled_is_digest(self) -> None:
        ev = render_event(from_webhook("issues", _issue_payload("labeled")))
        assert ev is not None and ev.priority == EventPriority.DIGEST

    def test_issue_comment_pr_detected(self) -> None:
        payload = {
            "action": "created",
            "issue": {"number": 7, "title": "t", "pull_request": {"url": "..."}},
            "comment": {"body": "@me 看一下", "html_url": "https://x",
                        "user": {"login": "carol"}},
            "repository": {"full_name": "o/r"}, "sender": {"login": "carol"},
        }
        ev = render_event(from_webhook("issue_comment", payload))
        assert ev is not None
        assert "PR #7" in ev.title
        assert "@me 看一下" in ev.body_digest

    def test_pull_request_merged_vs_unmerged(self) -> None:
        base = {
            "action": "closed", "number": 12,
            "pull_request": {"number": 12, "title": "重构", "merged": True,
                             "html_url": "https://x", "additions": 210, "deletions": 45,
                             "base": {"ref": "main"}, "head": {"ref": "feat"}},
            "repository": {"full_name": "o/r"}, "sender": {"login": "alice"},
        }
        ev = render_event(from_webhook("pull_request", base))
        assert ev is not None and "merged" in ev.title and "+210/-45" in ev.title
        assert ev.priority == EventPriority.NORMAL

        base["pull_request"]["merged"] = False
        ev2 = render_event(from_webhook("pull_request", base))
        assert ev2 is not None and ev2.priority == EventPriority.DIGEST

    def test_pull_request_review_requested_immediate(self) -> None:
        payload = {
            "action": "review_requested", "number": 3,
            "pull_request": {"number": 3, "title": "新特性", "html_url": "https://x",
                             "requested_reviewer": {"login": "me"},
                             "base": {"ref": "main"}, "head": {"ref": "f"}, "body": "说明"},
            "repository": {"full_name": "o/r"}, "sender": {"login": "alice"},
        }
        ev = render_event(from_webhook("pull_request", payload))
        assert ev is not None
        assert ev.priority == EventPriority.IMMEDIATE
        assert "me" in ev.title

    def test_release_published_immediate(self) -> None:
        payload = {
            "action": "published",
            "release": {"tag_name": "v2.1.0", "name": "v2.1.0",
                        "html_url": "https://x", "body": "更新日志"},
            "repository": {"full_name": "o/r"}, "sender": {"login": "alice"},
        }
        ev = render_event(from_webhook("release", payload))
        assert ev is not None
        assert ev.priority == EventPriority.IMMEDIATE
        assert "v2.1.0" in ev.title

    def test_workflow_run_failure_immediate_success_dropped(self) -> None:
        def _run(conclusion: str) -> dict:
            return {
                "action": "completed",
                "workflow_run": {"name": "CI", "conclusion": conclusion,
                                 "head_branch": "main", "html_url": "https://x"},
                "repository": {"full_name": "o/r"}, "sender": {"login": "ci"},
            }
        ev = render_event(from_webhook("workflow_run", _run("failure")))
        assert ev is not None and ev.priority == EventPriority.IMMEDIATE
        assert "CI failure" in ev.title
        assert render_event(from_webhook("workflow_run", _run("success"))) is None
        assert render_event(from_webhook("workflow_run", {
            "action": "requested", "workflow_run": {},
            "repository": {"full_name": "o/r"}})) is None

    def test_repository_renamed_immediate(self) -> None:
        ev = render_event(from_webhook("repository", {
            "action": "renamed", "repository": {"full_name": "o/r2"},
            "changes": {"old_name": {"name": "r"}}, "sender": {"login": "alice"}}))
        assert ev is not None and ev.priority == EventPriority.IMMEDIATE
        assert "renamed" in ev.title

    def test_star_is_digest(self) -> None:
        ev = render_event(from_webhook("star", {
            "action": "created", "repository": {"full_name": "o/r"},
            "sender": {"login": "fan"}}))
        assert ev is not None and ev.priority == EventPriority.DIGEST

    def test_security_alert_immediate(self) -> None:
        ev = render_event(from_webhook("dependabot_alert", {
            "action": "created",
            "alert": {"number": 9, "summary": "requests 漏洞", "html_url": "https://x"},
            "repository": {"full_name": "o/r"}, "sender": {"login": "github"}}))
        assert ev is not None and ev.priority == EventPriority.IMMEDIATE
        assert "Dependabot" in ev.title

    def test_unknown_event_generic_renderer(self) -> None:
        ev = render_event(from_webhook("sponsorship", {
            "action": "created", "repository": {"full_name": "o/r",
                                                "html_url": "https://github.com/o/r"},
            "sender": {"login": "sponsor"}}))
        assert ev is not None
        assert ev.title == "sponsorship.created"
        assert ev.priority == EventPriority.NORMAL

    def test_ping_never_rendered(self) -> None:
        assert render_event(from_webhook("ping", {"zen": "x",
                                                  "repository": {"full_name": "o/r"}})) is None

    def test_renderer_exception_falls_back_to_generic(self) -> None:
        # push 渲染器遇到畸形 payload(commits 不是 list)不炸,降级 generic
        ev = render_event(from_webhook("push", {
            "ref": 1, "commits": "not-a-list", "size": "x",
            "repository": {"full_name": "o/r"}, "sender": {}}))
        assert ev is not None


# ------------------------------------------------------------------
# 订阅匹配 / 分支过滤 / 消毒 / 提及
# ------------------------------------------------------------------


class TestSubscriptionAndFilters:
    def test_event_subscribed_granularities(self) -> None:
        assert event_subscribed(["issues"], [], "issues", "opened")
        assert event_subscribed(["issues.opened"], [], "issues", "opened")
        assert not event_subscribed(["issues.opened"], [], "issues", "closed")
        assert event_subscribed(["*"], [], "anything", "x")
        assert event_subscribed([], ["push"], "push", "")
        assert not event_subscribed([], [], "push", "")

    def test_branch_allowed_glob(self) -> None:
        assert branch_allowed([], "refs/heads/anything")
        assert branch_allowed(["main"], "refs/heads/main")
        assert not branch_allowed(["main"], "refs/heads/dev")
        assert branch_allowed(["release/*"], "refs/heads/release/1.2")
        assert not branch_allowed(["release/*"], "refs/heads/main")

    def test_sanitize_body_strips_media_and_truncates(self) -> None:
        body = "前文 ![img](https://t.co/x.png) 中图 <img src='https://t.co/y.png'> 后文"
        out = sanitize_body(body, 500)
        assert "t.co" not in out
        assert out.count("[图片已略]") == 2
        long_body = "x" * 600
        out2 = sanitize_body(long_body, 100)
        assert len(out2) < 160 and "截断" in out2
        assert sanitize_body("", 100) == ""

    def test_contains_mention(self) -> None:
        assert contains_mention("@Me 看一下", "me")
        assert contains_mention("cc @me!", "me")
        assert not contains_mention("email me@example.com", "me")  # @ 在 me 前
        assert not contains_mention("@melon", "me")
        assert not contains_mention("@me", "")


# ------------------------------------------------------------------
# 消息格式化
# ------------------------------------------------------------------


class TestFormatMessage:
    def test_single_event_with_untrusted_wrapper(self) -> None:
        ev = render_event(from_webhook("issues", _issue_payload("opened")))
        assert ev is not None
        ev.local_path = "/tmp/repo"
        text = format_event_message([ev])
        assert "[GitHub 事件] o/r · issues.opened" in text
        assert "触发者: bob" in text
        assert "本地代码: /tmp/repo" in text
        assert "不可信" in text and "外部内容结束" in text
        assert ev.url in text

    def test_aggregate_message_numbered(self) -> None:
        ev1 = render_event(from_webhook("issues", _issue_payload("opened")))
        ev2 = render_event(from_webhook("star", {
            "action": "created", "repository": {"full_name": "o/r"},
            "sender": {"login": "fan"}}))
        assert ev1 and ev2
        ev2.occurred_at = time.time()
        text = format_event_message([ev1, ev2])
        assert "[GitHub 事件汇总] o/r · 2 条" in text
        assert "1. issues.opened" in text
        assert "2. star.created" in text

    def test_empty_events_empty_text(self) -> None:
        assert format_event_message([]) == ""
