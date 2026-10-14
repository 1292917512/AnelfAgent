"""上下文诊断的 HTTP 边界、过滤和只读查询。"""

import httpx
from fastapi import FastAPI

from web.routers.context import router


async def test_recent_diagnostics_validate_bounds_and_do_not_arm_capture():
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        before = (await client.get("/context/snapshot")).json()["status"]
        response = await client.get("/context/recent", params={"scope": "user_webui:me#a", "steps": 80})
        assert response.status_code == 200
        assert response.json()["conversations"] == [{"scope": "user_webui:me#a", "loaded": False, "messages": []}]
        for params in ({"steps": 81}, {"limit": 0}, {"messages": 21}, {"scope": "x" * 501}):
            assert (await client.get("/context/recent", params=params)).status_code == 422
        assert (await client.get("/context/snapshot")).json()["status"] == before
