"""GitHub REST 薄客户端测试(httpx.MockTransport,不触网)。

覆盖:鉴权头注入 / ETag 条件请求与 304 / 限流头跟踪 / 错误分类
(401/403/404/429)/ 5xx 指数重试 / 响应解析。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
import pytest

from channels.github.client import (
    AuthError,
    GitHubApiError,
    GitHubClient,
    NotFoundError,
    RateLimitError,
)


def _client(handler: Any, **kw: Any) -> GitHubClient:
    return GitHubClient(transport=httpx.MockTransport(handler), max_retries=2, **kw)


class TestAuth:
    async def test_pat_bearer_header(self) -> None:
        seen: Dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization", "")
            return httpx.Response(200, json={})

        client = _client(handler, auth_mode="pat", token_provider=lambda: "tok-123")
        await client.get("/user")
        assert seen["auth"] == "Bearer tok-123"
        await client.close()

    async def test_none_mode_no_auth_header(self) -> None:
        seen: Dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization", "")
            return httpx.Response(200, json={})

        client = _client(handler, auth_mode="none")
        await client.get("/repos/o/r/events")
        assert seen["auth"] == ""
        await client.close()


class TestETag:
    async def test_etag_sent_and_304_not_modified(self) -> None:
        seen: List[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("if-none-match", ""))
            if request.headers.get("if-none-match") == '"abc"':
                return httpx.Response(304, headers={"etag": '"abc"'})
            return httpx.Response(200, json=[{"id": 1}], headers={"etag": '"abc"'})

        client = _client(handler)
        r1 = await client.get("/repos/o/r/events")
        assert r1.status == 200 and r1.etag == '"abc"' and r1.data == [{"id": 1}]
        r2 = await client.get("/repos/o/r/events", etag=r1.etag)
        assert r2.not_modified and r2.data is None
        assert seen[1] == '"abc"'
        await client.close()


class TestRateLimit:
    async def test_headers_tracked(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={}, headers={
                "x-ratelimit-remaining": "4999", "x-ratelimit-reset": "1800000000",
            })

        client = _client(handler)
        resp = await client.get("/x")
        assert resp.rate_remaining == 4999
        assert client.rate_remaining == 4999
        assert client.rate_reset_at == 1800000000.0
        await client.close()

    async def test_403_with_zero_remaining_raises_rate_limit(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "API rate limit exceeded"},
                                  headers={"x-ratelimit-remaining": "0",
                                           "x-ratelimit-reset": "1800000000"})

        client = _client(handler)
        with pytest.raises(RateLimitError):
            await client.get("/x")
        await client.close()

    async def test_403_plain_is_auth_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "Resource not accessible"})

        client = _client(handler)
        with pytest.raises(AuthError):
            await client.get("/x")
        await client.close()


class TestErrorClassification:
    async def test_401_auth_error(self) -> None:
        client = _client(lambda r: httpx.Response(401, json={"message": "bad credentials"}))
        with pytest.raises(AuthError):
            await client.get("/user")
        await client.close()

    async def test_404_not_found(self) -> None:
        client = _client(lambda r: httpx.Response(404, json={"message": "Not Found"}))
        with pytest.raises(NotFoundError):
            await client.get("/repos/o/r")
        await client.close()

    async def test_400_api_error(self) -> None:
        client = _client(lambda r: httpx.Response(422, json={"message": "Validation Failed"}))
        with pytest.raises(GitHubApiError):
            await client.post("/repos/o/r/issues", json_body={})
        await client.close()


class TestRetry:
    async def test_500_retried_then_success(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={"ok": True})

        client = _client(handler)  # max_retries=2:第一次 500 后重试一次(sleep 1s)
        resp = await client.get("/x")
        assert resp.data == {"ok": True}
        assert calls["n"] == 2
        await client.close()

    async def test_persistent_500_raises_after_retries(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(502, text="bad gateway")

        client = _client(handler)
        with pytest.raises(GitHubApiError):
            await client.get("/x")
        assert calls["n"] == 2  # 恰好重试到上限
        await client.close()


class TestPayloads:
    async def test_post_json_body(self) -> None:
        seen: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 99})

        client = _client(handler, auth_mode="pat", token_provider=lambda: "t")
        resp = await client.post("/repos/o/r/issues/1/comments", json_body={"body": "hi"})
        assert resp.status == 201 and resp.data["id"] == 99
        assert seen["body"] == {"body": "hi"}
        await client.close()

    async def test_text_response_returned_as_text(self) -> None:
        client = _client(lambda r: httpx.Response(200, text="diff --git a/x b/x"))
        resp = await client.get("/repos/o/r/pulls/1",
                                headers={"Accept": "application/vnd.github.diff"})
        assert isinstance(resp.data, str) and resp.data.startswith("diff --git")
        await client.close()
