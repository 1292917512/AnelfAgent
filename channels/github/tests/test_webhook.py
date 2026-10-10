"""GitHub webhook 入口测试(不触网)。

覆盖:验签(正确/错误/缺头/空前缀)/ fail-closed 启动 / delivery 去重 /
ping 不进管线 / 队列溢出丢最旧 / 单 worker 保序消费 / 立即 200。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import tempfile
from typing import Any, Dict, List, Optional

from channels.github.config import GitHubConfig
from channels.github.events import from_webhook
from channels.github.state import DeliveryDedup
from channels.github.webhook import WebhookIngress, handle_webhook, verify_signature

SECRET = "test-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class _FakeRequest:
    def __init__(self, body: bytes, headers: Dict[str, str]) -> None:
        self._body = body
        self.headers = headers

    async def body(self) -> bytes:
        return self._body


class _FakePipeline:
    def __init__(self) -> None:
        self.received: List[Any] = []

    async def submit_raw(self, raw: Any) -> None:
        self.received.append(raw)


class _FakeChannel:
    def __init__(self, config: GitHubConfig, ingress: Optional[WebhookIngress]) -> None:
        self.config = config
        self.ingress = ingress


def _webhook_config(**kw: Any) -> GitHubConfig:
    return GitHubConfig(mode="webhook", webhook_secret=SECRET, **kw)


def _request_for(event: str, payload: dict, *, secret: str = SECRET,
                 delivery: str = "d-1", sign: bool = True) -> _FakeRequest:
    body = json.dumps(payload).encode()
    headers = {
        "x-github-delivery": delivery,
        "x-github-event": event,
        "x-hub-signature-256": _sign(body, secret) if sign else "",
    }
    return _FakeRequest(body, headers)


class TestVerifySignature:
    def test_valid_signature(self) -> None:
        body = b'{"a":1}'
        assert verify_signature(SECRET, body, _sign(body))

    def test_wrong_secret_rejected(self) -> None:
        body = b'{"a":1}'
        assert not verify_signature("other", body, _sign(body))

    def test_missing_signature_rejected(self) -> None:
        assert not verify_signature(SECRET, b"{}", "")
        assert not verify_signature(SECRET, b"{}", "sha1=abc")

    def test_empty_secret_rejected(self) -> None:
        assert not verify_signature("", b"{}", _sign(b"{}"))


class TestDeliveryDedup:
    def test_first_seen_then_duplicate(self) -> None:
        dedup = DeliveryDedup(directory=tempfile.mkdtemp(prefix="github-dedup-test-"))
        assert dedup.seen_or_add("d-1") is False
        assert dedup.seen_or_add("d-1") is True
        assert dedup.seen_or_add("d-2") is False

    def test_empty_id_never_blocks(self) -> None:
        dedup = DeliveryDedup(directory=tempfile.mkdtemp(prefix="github-dedup-test-"))
        assert dedup.seen_or_add("") is False
        assert dedup.seen_or_add("") is False


class TestIngress:
    async def test_worker_consumes_in_order(self) -> None:
        pipeline = _FakePipeline()
        ingress = WebhookIngress(None, pipeline, DeliveryDedup(directory=tempfile.mkdtemp(prefix="github-dedup-test-")), queue_size=100)
        await ingress.start()
        try:
            for i in range(3):
                payload = {"action": "opened", "i": i, "repository": {"full_name": "o/r"},
                           "sender": {"login": "x"}}
                assert ingress.enqueue(f"d-{i}", "issues", payload) == "accepted"
            await asyncio.sleep(0.2)
            assert [r.payload["i"] for r in pipeline.received] == [0, 1, 2]  # 保序
        finally:
            await ingress.stop()

    async def test_duplicate_delivery_not_consumed(self) -> None:
        pipeline = _FakePipeline()
        ingress = WebhookIngress(None, pipeline, DeliveryDedup(directory=tempfile.mkdtemp(prefix="github-dedup-test-")), queue_size=100)
        await ingress.start()
        try:
            payload = {"repository": {"full_name": "o/r"}}
            assert ingress.enqueue("d-1", "push", payload) == "accepted"
            assert ingress.enqueue("d-1", "push", payload) == "duplicate"
            await asyncio.sleep(0.2)
            assert len(pipeline.received) == 1
        finally:
            await ingress.stop()

    async def test_queue_overflow_drops_oldest_and_counts(self) -> None:
        pipeline = _FakePipeline()
        ingress = WebhookIngress(None, pipeline, DeliveryDedup(directory=tempfile.mkdtemp(prefix="github-dedup-test-")), queue_size=10)
        # 不启动 worker:队列只进不出
        for i in range(12):
            status = ingress.enqueue(f"d-{i}", "push", {"i": i})
        assert ingress.dropped_count == 2
        assert status == "overflow_accepted"
        assert ingress.backlog == 10


class TestHandleWebhook:
    async def test_missing_headers_400(self) -> None:
        ingress = WebhookIngress(None, _FakePipeline(), DeliveryDedup(directory=tempfile.mkdtemp(prefix="github-dedup-test-")))
        await ingress.start()
        channel = _FakeChannel(_webhook_config(), ingress)
        body = b"{}"
        resp = await handle_webhook(channel, _FakeRequest(body, {"x-github-event": "push"}))
        assert resp.status_code == 400
        await ingress.stop()

    async def test_bad_signature_401(self) -> None:
        ingress = WebhookIngress(None, _FakePipeline(), DeliveryDedup(directory=tempfile.mkdtemp(prefix="github-dedup-test-")))
        await ingress.start()
        channel = _FakeChannel(_webhook_config(), ingress)
        resp = await handle_webhook(channel, _request_for("push", {"x": 1}, secret="wrong"))
        assert resp.status_code == 401
        await ingress.stop()

    async def test_ping_pong_without_pipeline(self) -> None:
        pipeline = _FakePipeline()
        ingress = WebhookIngress(None, pipeline, DeliveryDedup(directory=tempfile.mkdtemp(prefix="github-dedup-test-")))
        await ingress.start()
        channel = _FakeChannel(_webhook_config(), ingress)
        resp = await handle_webhook(channel, _request_for("ping", {"zen": "x"}))
        assert resp.status_code == 200
        assert json.loads(resp.body)["status"] == "pong"
        await asyncio.sleep(0.1)
        assert pipeline.received == []  # ping 不进管线
        await ingress.stop()

    async def test_valid_event_accepted_immediately(self) -> None:
        pipeline = _FakePipeline()
        ingress = WebhookIngress(None, pipeline, DeliveryDedup(directory=tempfile.mkdtemp(prefix="github-dedup-test-")))
        await ingress.start()
        channel = _FakeChannel(_webhook_config(), ingress)
        resp = await handle_webhook(channel, _request_for(
            "issues", {"action": "opened", "issue": {"number": 1, "title": "t"},
                       "repository": {"full_name": "o/r"}, "sender": {"login": "a"}}))
        assert resp.status_code == 200
        assert json.loads(resp.body)["status"] == "accepted"
        await asyncio.sleep(0.2)
        assert len(pipeline.received) == 1
        assert pipeline.received[0].name == "issues"
        await ingress.stop()

    async def test_duplicate_delivery_200_duplicate(self) -> None:
        ingress = WebhookIngress(None, _FakePipeline(), DeliveryDedup(directory=tempfile.mkdtemp(prefix="github-dedup-test-")))
        await ingress.start()
        channel = _FakeChannel(_webhook_config(), ingress)
        payload = {"action": "opened", "repository": {"full_name": "o/r"}}
        r1 = await handle_webhook(channel, _request_for("issues", payload, delivery="same"))
        r2 = await handle_webhook(channel, _request_for("issues", payload, delivery="same"))
        assert json.loads(r1.body)["status"] == "accepted"
        assert json.loads(r2.body)["status"] == "duplicate"
        await ingress.stop()

    async def test_webhook_mode_disabled_404(self) -> None:
        channel = _FakeChannel(GitHubConfig(mode="poll"), None)
        resp = await handle_webhook(channel, _request_for("push", {"x": 1}))
        assert resp.status_code == 404

    async def test_ingress_not_running_503(self) -> None:
        channel = _FakeChannel(_webhook_config(), None)
        resp = await handle_webhook(channel, _request_for("push", {"x": 1}))
        assert resp.status_code == 503

    async def test_invalid_json_400(self) -> None:
        ingress = WebhookIngress(None, _FakePipeline(), DeliveryDedup(directory=tempfile.mkdtemp(prefix="github-dedup-test-")))
        await ingress.start()
        channel = _FakeChannel(_webhook_config(), ingress)
        body = b"not-json"
        req = _FakeRequest(body, {
            "x-github-delivery": "d-x", "x-github-event": "push",
            "x-hub-signature-256": _sign(body),
        })
        resp = await handle_webhook(channel, req)
        assert resp.status_code == 400
        await ingress.stop()


class TestFailClosed:
    async def test_webhook_mode_without_secret_refuses_start(self) -> None:
        from channels.github.adapter import GitHubChannel

        channel = GitHubChannel()
        channel._config = GitHubConfig(mode="webhook", webhook_secret="")
        try:
            await channel.start()
        except RuntimeError as exc:
            assert "webhook_secret" in str(exc)
        else:
            raise AssertionError("缺 secret 的 webhook 模式必须拒绝启动")

    async def test_poll_mode_starts_without_secret(self) -> None:
        from channels.github.adapter import GitHubChannel

        channel = GitHubChannel()
        channel._config = GitHubConfig(mode="poll", repos=[], auth_mode="none")
        await channel.start()
        try:
            assert channel._poller is not None and channel._poller.running
        finally:
            await channel.stop()
        assert channel._poller is None


class TestFromWebhookSource:
    def test_raw_event_carries_delivery_id(self) -> None:
        raw = from_webhook("issues", {"action": "opened",
                                      "repository": {"full_name": "o/r"}},
                           delivery_id="d-42")
        assert raw.event_id == "d-42"
        assert raw.source == "webhook"
