"""频道定向重载 API 的缺失、并发与失败语义。"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routers import adapters


@pytest.mark.parametrize("outcome, status", [
    ({"added": [], "removed": [], "reloaded": ["demo"], "failed": [], "total": 2}, 200),
    ({"skipped": "in_progress"}, 409),
    ({"failed": ["demo"]}, 500),
    (KeyError("频道不存在"), 404),
])
def test_reload_channel_reports_result(monkeypatch, outcome, status):
    reload = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
    monkeypatch.setattr(adapters._adapter_svc, "reload_channels", reload)
    app = FastAPI()
    app.include_router(adapters.router)
    with TestClient(app) as client:
        response = client.post("/adapters/demo/reload")
    assert response.status_code == status
    reload.assert_awaited_once_with("demo")
    if status == 200:
        assert response.json() == outcome
