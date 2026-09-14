"""本地模型资产路由测试（/api/local-models）：清单 / 下载启动 / 删除 / 运行时。"""

from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import model_assets
from agent.model_assets import ModelAsset, ModelAssetManager
from web.routers.model_assets import router as local_models_router

CONTENT = b"model-bytes"


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """替换单例：临时目录 + 本地测试资产。"""
    asset = ModelAsset(
        id="test_model", filename="test_model.onnx", name="Test Model",
        version="v1", url="https://example.com/test_model.onnx",
        sha256=hashlib.sha256(CONTENT).hexdigest(),
        license="MIT", description="测试资产",
        pip_requires="onnxruntime", import_check="onnxruntime")
    mgr = ModelAssetManager(assets=(asset,), directory=str(tmp_path))
    monkeypatch.setattr(model_assets, "_manager", mgr)
    return mgr


@pytest.fixture
def client(manager):
    app = FastAPI()
    app.include_router(local_models_router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


class TestLocalModelsApi:
    def test_list_shape(self, client: TestClient) -> None:
        resp = client.get("/api/local-models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dir"].endswith("models")
        assert data["models"][0]["id"] == "test_model"
        assert data["models"][0]["status"] == "missing"
        assert "installed" in data["runtime"]

    def test_download_unknown_asset_404(self, client: TestClient) -> None:
        assert client.post("/api/local-models/nope/download").status_code == 404

    def test_delete_unknown_asset_404(self, client: TestClient) -> None:
        assert client.delete("/api/local-models/nope").status_code == 404

    def test_delete_existing_file(self, client, manager, tmp_path) -> None:
        (tmp_path / "test_model.onnx").write_bytes(CONTENT)
        resp = client.delete("/api/local-models/test_model")
        assert resp.status_code == 200
        assert resp.json()["removed"] is True

    def test_runtime_status_shape(self, client: TestClient) -> None:
        resp = client.get("/api/local-models/runtime")
        assert resp.status_code == 200
        assert set(resp.json()) == {"installed", "version"}

    def test_runtime_install_rejects_bad_package(self, client: TestClient) -> None:
        resp = client.post("/api/local-models/runtime/install",
                           json={"package": "foo; rm -rf"})
        assert resp.status_code == 400
