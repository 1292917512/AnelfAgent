"""聊天上传响应的媒体地址可经同一 HTTP 路由读取。"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from web.routers import chat


async def test_history_passes_incremental_cursor_and_rejects_conflicting_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    load = AsyncMock(return_value=[{"id": 12, "role": "assistant", "content": "Recovered"}])
    monkeypatch.setattr(chat._chat_svc, "load_history", load)
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/chat/history", params={"chat_id": "a", "limit": 40, "after_id": 11})
        assert response.status_code == 200
        assert response.json()[0]["id"] == 12
        load.assert_awaited_once_with(scope_id="webui:web_user#a", limit=40, before_id=None, after_id=11)
        invalid = await client.get("/api/chat/history", params={"after_id": 11, "before_id": 20})
        assert invalid.status_code == 422
        assert load.await_count == 1


@pytest.mark.parametrize("filename", ["截图 (1) #50%.png", "literal%2Fname.png"])
async def test_uploaded_image_url_serves_original_file(
    filename: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat, "_UPLOAD_DIR", tmp_path)
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    content = b"image content"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        uploaded = await client.post("/api/chat/upload", files={"file": (filename, content, "image/png")})
        assert uploaded.status_code == 200
        data = uploaded.json()
        assert Path(data["path"]).read_bytes() == content
        response = await client.get(data["url"])
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == content
