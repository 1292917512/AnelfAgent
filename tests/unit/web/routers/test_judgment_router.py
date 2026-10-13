"""Web 判断配置与 AI 入口共享目录和默认选择，错误不会破坏既有配置。"""
import json
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from agent.judgment.tools import configure_judgment, list_judgment_models
from web.routers.judgment import router


async def test_web_diagnostics_use_inference_only_when_explicit(monkeypatch):
    import services.judgment as service_module

    check = AsyncMock(return_value={"ok": True, "kind": "connectivity"})
    infer = AsyncMock(return_value={"ok": True, "kind": "inference"})
    monkeypatch.setattr(service_module, "check_connection", check)
    monkeypatch.setattr(service_module, "test_inference", infer)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/judgment/test", json={"model_id": "local"})
        assert result.json()["kind"] == "connectivity"
        check.assert_awaited_once_with("local")
        infer.assert_not_awaited()
        result = await client.post("/judgment/test", json={"model_id": "local", "inference": True})
        assert result.json()["kind"] == "inference"
        infer.assert_awaited_once_with("local")


async def test_web_and_ai_share_crud_masking_and_validation():
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.put("/judgment/providers/pc", json={
            "name": "PC", "base_url": "http://127.0.0.1:8090", "api_key": "a-private-testing-provider-key",
            "models": {"local": {"model": "StartLux-Decision-27B"}},
        })
        assert created.status_code == 200 and "a-private-testing-provider-key" not in created.text
        assert json.loads(await list_judgment_models())["providers"]["pc"]["name"] == "PC"
        saved = await client.put("/judgment/models/second", json={"provider_id": "pc", "model": "second-model"})
        assert saved.status_code == 200
        await configure_judgment("set_default", "second")
        status = (await client.get("/judgment/status")).json()
        assert status["model_id"] == "second" and status["provider_id"] == "pc"
        priority = await client.put("/judgment/priority", json={"model_ids": ["second", "local"]})
        assert priority.status_code == 200 and priority.json()["fallback_models"] == ["local"]
        assert json.loads(await list_judgment_models())["fallback_models"] == ["local"]
        assert (await client.put("/judgment/priority", json={"model_ids": ["local", "local"]})).status_code == 400
        assert (await client.put("/judgment/default", json={"model_id": "missing"})).status_code == 400
        assert (await client.put("/judgment/providers/pc", json={"base_url": "file:///bad"})).status_code == 400
        assert (await client.put("/judgment/providers/pc", json={"name": "Renamed"})).status_code == 200
        assert (await client.delete("/judgment/models/second")).json()["default_model"] == "local"
        assert (await client.put("/judgment/default", json={"model_id": "local"})).status_code == 200
        assert (await client.delete("/judgment/providers/pc")).json()["default_model"] == ""
        assert not (await client.get("/judgment/providers")).json()["providers"]
        failed_test = (await client.post("/judgment/test", json={"model_id": "missing"})).json()
        assert not failed_test["ok"] and failed_test["cause"] == "config"
