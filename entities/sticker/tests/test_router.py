"""表情库模块路由的路径、查询和文件访问边界。"""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import path as paths
from entities.sticker import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router.build_router(), prefix="/api/entity/sticker")
    return TestClient(app)


def test_module_list_query_and_image_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    listing = AsyncMock(return_value={"items": [], "total": 0})
    images = AsyncMock(return_value={"items": [], "total": 0})
    monkeypatch.setattr(router._sticker_svc, "list_stickers", listing)
    monkeypatch.setattr(router._sticker_svc, "list_images", images)
    assert client.get("/api/entity/sticker?query=cat&page=2&page_size=12").status_code == 200
    listing.assert_awaited_once_with("cat", 2, 12)
    assert client.get("/api/entity/sticker/images/list").status_code == 200
    images.assert_awaited_once_with(1, 24)


def test_indexed_file_still_requires_workspace_containment(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    public = workspace / "image.png"
    public.write_bytes(b"test-image")
    private = tmp_path / "private.png"
    private.write_bytes(b"private-image")
    monkeypatch.setattr(paths, "workspace_root", lambda: str(workspace))
    monkeypatch.setattr(router._sticker_svc, "get_image", AsyncMock(return_value={"indexed": True}))
    assert client.get("/api/entity/sticker/images/file", params={"path": str(public)}).content == b"test-image"
    assert client.get("/api/entity/sticker/images/file", params={"path": str(private)}).status_code == 403


def test_upload_rejects_oversize_before_import(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(router, "_MAX_UPLOAD_BYTES", 4)
    importer = AsyncMock()
    monkeypatch.setattr(router._sticker_svc, "import_sticker", importer)
    assert client.post("/api/entity/sticker", files={"file": ("test.png", b"12345", "image/png")}).status_code == 400
    importer.assert_not_awaited()
