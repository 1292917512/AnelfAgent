"""酒馆 HTTP 回执与工具错误映射。"""

import json

import httpx
import pytest

from entities.sillytavern.st_client import STClient, STError
from entities.sillytavern.tools import _guard


@pytest.mark.parametrize("status", [400, 500, 503])
def test_empty_http_error_is_not_a_success(status: int) -> None:
    client = STClient()
    client._base_url = "http://sillytavern.test"
    client._token = "test-token"
    with httpx.Client(base_url=client._base_url, transport=httpx.MockTransport(
        lambda request: httpx.Response(status, content=b""),
    )) as transport:
        client._client = transport
        with pytest.raises(STError, match=str(status)):
            client.post(client._base_url, "/api/characters/delete", {"avatar_url": "test"})


def test_empty_success_is_preserved() -> None:
    client = STClient()
    client._base_url = "http://sillytavern.test"
    client._token = "test-token"
    with httpx.Client(base_url=client._base_url, transport=httpx.MockTransport(
        lambda request: httpx.Response(204),
    )) as transport:
        client._client = transport
        assert client.post(client._base_url, "/api/settings/save") is None


def test_write_timeout_reports_unknown_outcome() -> None:
    def write() -> None:
        try:
            raise httpx.ReadTimeout("response lost")
        except httpx.ReadTimeout as exc:
            raise STError("request failed") from exc

    result = json.loads(_guard(write))
    assert result["cause"] == "timeout"
    assert result["outcome"] == "unknown" and result["retryable"] is False
