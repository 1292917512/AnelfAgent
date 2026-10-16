"""聊天上传响应的媒体地址可经同一 HTTP 路由读取。"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from web.routers import chat


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
