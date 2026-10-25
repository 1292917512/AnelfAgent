"""单实体重载 API 的并发、缺失与失败反馈。"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routers import entities


@pytest.mark.parametrize("outcome, status", [
    ({"added": [], "removed": [], "reloaded": ["demo"], "failed": [], "total": 2}, 200),
    ({"skipped": "in_progress"}, 409),
    ({"failed": ["demo"]}, 500),
    (ValueError("实体模块不存在"), 404),
])
def test_reload_entity_reports_operation_result(monkeypatch, outcome, status):
    reload = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
    monkeypatch.setattr(entities._entity_svc, "reload_module", reload)
    app = FastAPI()
    app.include_router(entities.router)
    with TestClient(app) as client:
        response = client.post("/entities/demo/reload")
    assert response.status_code == status
    reload.assert_awaited_once_with("demo")
    if status == 200:
        assert response.json() == outcome
