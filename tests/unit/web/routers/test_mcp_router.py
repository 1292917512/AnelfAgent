"""MCP 编辑与排序的 HTTP 契约。"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


async def test_edit_preserves_unexposed_fields_and_sort_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from entities.mcp import config
    from services.mcp import MCPService
    from web.routers import mcp

    monkeypatch.setattr(config, "_writable_config_path", lambda: tmp_path / "mcp.json")
    monkeypatch.setattr(MCPService, "_trigger_reload", lambda self: None)
    service = MCPService()
    monkeypatch.setattr(mcp, "_mcp_svc", service)
    service.create_server("alpha", {"url": "https://example.com", "oauth": {"client_id": "example"}, "plugin": "example",
                                   "stay_awake": True, "timeout": 10, "headers": {"Authorization": "test-secret"}})
    service.create_server("beta", {"command": "npx"})
    before = service.get_server_config("alpha")
    app = FastAPI()
    app.include_router(mcp.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/mcp/alpha", json={"timeout": None, "headers": {"Authorization": "********"}})
        assert response.status_code == 200
        assert "test-secret" not in response.text
        assert service.get_server_config("alpha") == {key: value for key, value in before.items() if key != "timeout"}
        assert (await client.post("/mcp/reorder", json={"names": ["beta", "alpha"]})).status_code == 200
        assert service.get_server_names() == ["beta", "alpha"]
        assert (await client.post("/mcp/reorder", json={"names": ["alpha"]})).status_code == 409
        assert service.get_server_names() == ["beta", "alpha"]
