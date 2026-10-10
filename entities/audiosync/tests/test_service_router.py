from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from entities.audiosync import client as service
from entities.audiosync.router import build_router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(build_router(), prefix="/api/entity/audiosync")
    return TestClient(app)


def test_service_status_and_refresh(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "endpoint_config", lambda: "http://funasr.local")
    monkeypatch.setattr(service, "probe_available", AsyncMock(return_value=True))
    response = client.get("/api/entity/audiosync/service/status?refresh=true")
    assert response.status_code == 200
    assert response.json() == {"configured": True, "reachable": True, "endpoint": "http://funasr.local"}


def test_unavailable_gpu_returns_meaningful_status(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "gpu_status", AsyncMock(side_effect=service.FunAsrNotConfigured("Missing endpoint")))
    response = client.get("/api/entity/audiosync/service/gpu")
    assert response.status_code == 503
    assert response.json()["detail"] == "Missing endpoint"
