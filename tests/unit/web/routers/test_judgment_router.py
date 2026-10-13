"""Web 判断配置与 AI 入口共享目录和默认选择，错误不会破坏既有配置。"""
import json

import httpx
from fastapi import FastAPI

from agent.judgment.tools import configure_judgment, list_judgment_models
from web.routers.judgment import router


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
        assert (await client.put("/judgment/default", json={"model_id": "missing"})).status_code == 400
        assert (await client.put("/judgment/providers/pc", json={"base_url": "file:///bad"})).status_code == 400
        assert (await client.put("/judgment/providers/pc", json={"name": "Renamed"})).status_code == 200
        assert (await client.delete("/judgment/models/second")).json()["default_model"] == ""
        assert (await client.put("/judgment/default", json={"model_id": "local"})).status_code == 200
        assert (await client.delete("/judgment/providers/pc")).json()["default_model"] == ""
        assert not (await client.get("/judgment/providers")).json()["providers"]
        failed_test = (await client.post("/judgment/test", json={"model_id": "missing"})).json()
        assert not failed_test["ok"] and failed_test["cause"] == "config"
