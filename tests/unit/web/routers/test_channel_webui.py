"""频道 WebUI 通用代理的 OpenAPI 元数据与运行时路由回归。"""

from __future__ import annotations

import warnings

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routers import channel_webui


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(channel_webui.router, prefix="/api")
    return app


def test_proxy_routes_do_not_emit_duplicate_operation_ids() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = _app().openapi()
    assert not [warning for warning in caught if "Duplicate Operation ID" in str(warning.message)]
    assert not any(path.startswith("/api/channels/{channel_id}/webui") for path in schema["paths"])


@pytest.mark.parametrize("suffix", ["webui", "webui/", "webui/assets/app.js"])
def test_schema_exclusion_does_not_remove_proxy_routes(
    suffix: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(channel_webui, "_resolve_target", lambda _channel_id: None)
    response = TestClient(_app()).get(f"/api/channels/qq/{suffix}")
    assert response.status_code == 404
    assert response.json() == {"error": "该频道未配置 WebUI 地址"}
