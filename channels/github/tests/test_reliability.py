"""持久投递、事件上下文、认证隔离和写入重试回归。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from channels.github.auth import AuthContext, GitHubAuth
from channels.github.catalog import EVENT_ACTIONS
from channels.github.client import GitHubClient, OutcomeUnknownError
from channels.github.config import GitHubAccount, GitHubConfig, RepoSubscription
from channels.github.events import from_poll_api, from_webhook, render_event
from channels.github.formatting import format_event_message
from channels.github.oauth import OAuthService
from channels.github.pipeline import EventPipeline
from channels.github.state import DigestBuffer, EventStats
from channels.github.tests.test_tools import _channel


class Channel:
    def __init__(self, config: GitHubConfig) -> None:
        self.config = config
        self.messages: list[str] = []
        self.fail = False

    async def dispatch_events(self, repo: str, events: list[Any], *, text: str) -> None:
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("temporary dispatch failure")
        self.messages.append(text)


def pipeline(directory: Path, **config: Any) -> tuple[EventPipeline, Channel]:
    channel = Channel(GitHubConfig(repos=[RepoSubscription(owner="o", repo="r")], **config))
    result = EventPipeline(channel, digest=DigestBuffer(directory=str(directory)), stats=EventStats(directory=str(directory)))
    return result, channel


def issue(delivery: str = "delivery", body: str = "body") -> Any:
    return from_webhook("issues", {"action": "opened", "repository": {"full_name": "o/r"},
                                  "issue": {"id": 123, "number": 4, "title": "Title", "body": body,
                                            "created_at": "2026-10-01T00:00:00Z"}}, delivery)


async def test_timer_survives_real_await_and_confirms_delivery(tmp_path: Path) -> None:
    pipe, channel = pipeline(tmp_path, aggregate_window_sec=1)
    await pipe.submit_raw(issue())
    await asyncio.sleep(1.1)
    assert len(channel.messages) == 1
    assert (await pipe.journal.snapshot())["counts"] == {"done": 1}
    await pipe.stop()


async def test_failure_and_restart_recover_without_accepting_duplicate(tmp_path: Path) -> None:
    pipe, channel = pipeline(tmp_path, aggregate_window_sec=300)
    channel.fail = True
    await pipe.submit_raw(issue())
    with pytest.raises(RuntimeError):
        await pipe.flush_all()
    snapshot = await pipe.journal.snapshot()
    assert snapshot["counts"] == {"pending": 1}
    assert snapshot["events"][0]["attempts"] == 1
    await pipe.stop()
    recovered, healthy = pipeline(tmp_path, aggregate_max_events=1)
    records = await recovered.journal._run(lambda connection: connection.execute("UPDATE inbox SET available_at=0"))
    del records
    for key, raw in await recovered.journal.pending():
        await recovered._process(key, raw)
    await recovered.submit_raw(issue("redelivery"))
    assert len(healthy.messages) == 1
    assert (await recovered.journal.snapshot())["counts"] == {"done": 1}
    await recovered.stop()


async def test_poll_and_webhook_share_business_identity(tmp_path: Path) -> None:
    pipe, channel = pipeline(tmp_path, aggregate_max_events=1)
    webhook = issue()
    poll = from_poll_api({"id": "poll-id", "type": "IssuesEvent", "repo": {"name": "o/r"},
                          "created_at": "2026-10-01T00:00:00Z", "payload": webhook.payload})
    await asyncio.gather(pipe.submit_raw(webhook), pipe.submit_raw(poll))
    assert len(channel.messages) == 1
    await pipe.stop()


async def test_full_body_mention_subject_fields_and_own_comment_filter(tmp_path: Path) -> None:
    pipe, channel = pipeline(tmp_path, body_digest_chars=10, watch_mentions_of="owner", message_fields=["context"])
    await pipe.submit_raw(issue(body="x" * 100 + " @owner"))
    assert len(channel.messages) == 1
    assert '"subject": "issue:4"' in channel.messages[0]
    assert "x" * 10 not in channel.messages[0]
    await pipe.journal.remember_outgoing("o/r", "comment", 42)
    raw = from_webhook("issue_comment", {"action": "created", "repository": {"full_name": "o/r"},
                                         "comment": {"id": 42, "body": "@owner"}, "issue": {"number": 4}}, "self")
    assert await pipe.submit_raw(raw) is None
    assert len(channel.messages) == 1
    await pipe.stop()


def test_modern_push_review_context_and_content_selection() -> None:
    push = from_poll_api({"id": "1", "type": "PushEvent", "repo": {"name": "o/r"},
                          "payload": {"ref": "refs/heads/main", "before": "oldsha", "head": "newsha"}})
    event = render_event(push)
    assert event is not None and "newsha" in event.title
    review = render_event(from_webhook("pull_request_review_comment", {
        "action": "created", "repository": {"full_name": "o/r"}, "pull_request": {"number": 5},
        "installation": {"id": 99}, "comment": {"id": 3, "body": "review body", "path": "a.py", "line": 7},
    }))
    assert review is not None
    assert review.body_digest == "review body" and review.extra["comment_line"] == 7
    assert review.extra["installation_id"] == 99 and review.extra["subject"] == "issue:5"
    assert "review body" not in format_event_message([review], fields=[])


def test_event_catalog_matches_sdk_and_schema_exposes_structured_choices() -> None:
    from githubkit.versions.latest.webhooks import webhook_action_types

    assert EVENT_ACTIONS == {key: list(value) if isinstance(value, dict) else [] for key, value in webhook_action_types.items()}
    schema = GitHubConfig.model_json_schema()
    assert schema["properties"]["repos"]["section"] == "subscriptions"
    assert "discussion_comment" in schema["properties"]["default_events"]["options"]
    assert "body" in schema["properties"]["message_fields"]["options"]


async def test_write_network_failure_is_not_retried_and_paths_cannot_escape() -> None:
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("response lost")
    client = GitHubClient(transport=httpx.MockTransport(handler))
    with pytest.raises(OutcomeUnknownError):
        await client.post("/repos/o/r/issues/1/comments", json_body={"body": "hello"})
    assert calls == 1
    for path in ("https://evil.test", "//evil.test", "/repos/../app", "/repos/%2e%2e/app", "/x?token=abc"):
        with pytest.raises(ValueError):
            await client.get(path)
    assert calls == 1
    await client.close()


async def test_auth_contexts_do_not_leak_between_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = GitHubConfig(accounts=[GitHubAccount(name="second", token_ref="two")])
    monkeypatch.setattr("channels.github.auth.get_provider_key", lambda ref: {"github": "one-token", "two": "two-token"}[ref])
    auth = GitHubAuth(lambda: cfg)
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        return httpx.Response(200, json={"authorization": request.headers["authorization"]})
    client = GitHubClient(auth=auth, transport=httpx.MockTransport(handler))
    async def query(name: str) -> str:
        with auth.using(AuthContext(account=name)):
            return (await client.get("/user")).data["authorization"]
    assert await asyncio.gather(query(""), query("second"), query("")) == ["token one-token", "token two-token", "token one-token"]
    await client.close()
    await auth.close()


async def test_subscription_patch_clears_arrays_and_false_without_resetting_other_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = _channel(GitHubConfig(repos=[RepoSubscription(owner="o", repo="r", events=["push"], branches=["main"],
                                                         priority_boost=True, poll_interval_sec=600, local_path="keep")]))
    saved: dict[str, Any] = {}
    monkeypatch.setattr("channels.github.tool_config.set_channel_config", lambda _, **changes: saved.update(changes))
    result = json.loads(await channel.subscribe_repo("o/r", events=[], branches=[], priority_boost=False, poll_interval_sec=0, message_fields=[]))
    assert result["success"]
    sub = saved["repos"][0]
    assert sub["events"] == sub["branches"] == sub["message_fields"] == []
    assert sub["priority_boost"] is False and sub["poll_interval_sec"] == 0 and sub["local_path"] == "keep"


async def test_graphql_read_gate_parses_operations_before_network() -> None:
    channel = _channel(GitHubConfig())
    result = json.loads(await channel.graphql_read('mutation Update { updateIssue(input: {id:"x",title:"y"}) { clientMutationId } }'))
    assert result["cause"] == "param" and channel._client.calls == []
    denied = json.loads(await channel.graphql_write("mutation { x }"))
    assert denied["cause"] == "permission" and channel._client.calls == []


async def test_sdk_catalog_and_call_use_real_sdk_with_single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = _channel(GitHubConfig(allow_write=True, auth_mode="none"))
    channel._client = GitHubClient()
    directory = json.loads(await channel.describe_api())
    assert "issues" in directory["namespaces"] and "repos" in directory["namespaces"]
    description = json.loads(await channel.describe_api("issues", "create_comment"))
    assert "issue_number" in description["signature"]
    calls: list[str] = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503, json={"message": "server failed"})
    monkeypatch.setattr("channels.github.tool_api.AsyncNetworkTransport", lambda: httpx.MockTransport(handler))
    result = json.loads(await channel.call_rest("issues", "create_comment", {"owner": "o", "repo": "r", "issue_number": 4, "body": "hello"}))
    assert result["outcome"] == "unknown" and result["retryable"] is False
    assert calls == ["/repos/o/r/issues/4/comments"]
    await channel._client.close()


async def test_enterprise_rest_and_graphql_preserve_correct_base_paths() -> None:
    paths: list[str] = []
    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={})
    client = GitHubClient(base_url="https://enterprise.test/api/v3", transport=httpx.MockTransport(handler))
    await client.get("/repos/o/r")
    await client.post("/graphql", json_body={"query": "{ viewer { login } }"})
    assert paths == ["/api/v3/repos/o/r", "/api/graphql"]
    await client.close()


async def test_oauth_state_is_single_use_and_secrets_only_enter_credential_store(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.parse import parse_qs, urlsplit

    account = GitHubAccount(name="user", auth_mode="oauth", client_id="client", client_secret_ref="secret", token_ref="user-token")
    cfg = GitHubConfig(accounts=[account])
    stored: dict[str, Any] = {}
    monkeypatch.setattr("channels.github.oauth.get_provider_key", lambda *args: "client-secret")
    monkeypatch.setattr("channels.github.oauth.set_provider_fields", lambda name, data: stored.update({name: data}))
    service = OAuthService(lambda: cfg, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "secret-token"})))
    url = service.begin("user", "http://localhost/callback")["authorize_url"]
    query = parse_qs(urlsplit(url).query)
    assert query["code_challenge_method"] == ["S256"]
    state = query["state"][0]
    assert await service.finish(state, "code") == "user"
    assert stored["user-token"]["api_key"] == "secret-token"
    with pytest.raises(ValueError):
        await service.finish(state, "code")
    assert "secret-token" not in cfg.model_dump_json()
    await service.close()


def test_oauth_token_storage_matches_registered_credential_fields() -> None:
    from channels.github.credentials import register_accounts
    from core.provider_keys import get_provider_key

    account = GitHubAccount(name="oauth-test", auth_mode="oauth", token_ref="github_oauth_test")
    register_accounts([account])
    OAuthService._save(account, {"access_token": "access-test", "refresh_token": "refresh-test", "expires_in": 3600})
    assert get_provider_key(account.token_ref) == "access-test"
    assert get_provider_key(account.token_ref, "refresh_token") == "refresh-test"


async def test_completed_event_cannot_be_claimed_from_stale_snapshot(tmp_path: Path) -> None:
    pipe, channel = pipeline(tmp_path, aggregate_max_events=1)
    raw = issue()
    key, inserted = await pipe.journal.accept(raw, 10)
    assert inserted
    await pipe._process(key, raw)
    await pipe._process(key, raw)
    assert len(channel.messages) == 1
    assert not await pipe.journal.claim(key)
    await pipe.stop()


async def test_sdk_upload_and_comment_feedback_use_actual_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from channels.github.journal import EventJournal

    channel = _channel(GitHubConfig(allow_write=True, auth_mode="none"))
    channel._client = GitHubClient()
    channel._journal = EventJournal(str(tmp_path))
    requests: list[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": 42})
    monkeypatch.setattr("channels.github.tool_api.AsyncNetworkTransport", lambda: httpx.MockTransport(handler))
    result = json.loads(await channel.call_rest("repos", "upload_release_asset", {
        "owner": "o", "repo": "r", "release_id": 5, "name": "test.txt", "headers": {"Content-Type": "text/plain"},
    }, binary_body_base64="aGVsbG8="))
    assert result["success"], result
    assert requests[0].url.host == "uploads.github.com" and requests[0].content == b"hello"
    result = json.loads(await channel.call_rest("issues", "create_comment", {"owner": "o", "repo": "r", "issue_number": 4, "body": "hello"}))
    assert result["success"], result
    raw = from_webhook("issue_comment", {"action": "created", "repository": {"full_name": "o/r"}, "comment": {"id": 42}})
    assert await channel._journal.is_outgoing(raw)
    await channel._client.close()


async def test_sdk_timeout_reports_unknown_outcome_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = _channel(GitHubConfig(allow_write=True, auth_mode="none"))
    channel._client = GitHubClient()
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("response lost", request=request)
    monkeypatch.setattr("channels.github.tool_api.AsyncNetworkTransport", lambda: httpx.MockTransport(handler))
    result = json.loads(await channel.call_rest("issues", "create_comment", {"owner": "o", "repo": "r", "issue_number": 4, "body": "hello"}))
    assert result["outcome"] == "unknown" and result["retryable"] is False and calls == 1
    await channel._client.close()


async def test_graphql_partial_errors_are_not_reported_as_success() -> None:
    channel = _channel(GitHubConfig(allow_write=True))
    channel._client = GitHubClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "data": {"updateIssue": None}, "errors": [{"message": "permission denied"}],
    })))
    result = json.loads(await channel.graphql_write("mutation { updateIssue(input: {id: \"x\", title: \"y\"}) { clientMutationId } }"))
    assert result["success"] is False and result["partial"] is True and result["errors"]
    await channel._client.close()
