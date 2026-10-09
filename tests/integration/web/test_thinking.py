"""Thinking trace HTTP contract."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.tracer import NodeType, TraceNode, TraceSession
from web.routers.thinking import router


def test_expired_trace_returns_not_found(monkeypatch) -> None:
    from web.routers import thinking

    monkeypatch.setattr(thinking.thinking_tracer, "get_session", lambda _session_id: None)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/thinking/sessions/expired")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_session_summary_exposes_its_own_source() -> None:
    session = TraceSession(id="trace", start_time=1)
    session.nodes.append(TraceNode(
        id="start", type=NodeType.SESSION_START, label="Conversation",
        data={"scope": "user_webui:web_user#design"},
    ))
    assert session.to_summary()["scope"] == "user_webui:web_user#design"
    assert session.to_summary()["label"] == "Conversation"
