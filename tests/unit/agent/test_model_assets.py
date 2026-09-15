"""本地模型资产测试：注册表完整性 / 状态快照 / 下载校验 / 并发合并 / 删除。"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from agent.model_assets import (
    MODEL_ASSETS,
    ModelAsset,
    ModelAssetManager,
    models_dir,
)

CONTENT = b"fake-onnx-model-bytes" * 1000


def _asset(url: str, sha256: str = "") -> ModelAsset:
    return ModelAsset(
        id="test_model", filename="test_model.onnx", name="Test Model",
        version="v1", url=url, sha256=sha256 or hashlib.sha256(CONTENT).hexdigest(),
        license="MIT", description="测试资产",
        pip_requires="onnxruntime", import_check="onnxruntime")


@pytest.fixture
async def server():
    """本地资产下载源（命中计数用于并发合并断言；slow 路由可闸住下载）。"""
    hits = {"count": 0}
    gate = asyncio.Event()

    async def handler(_request: web.Request) -> web.Response:
        hits["count"] += 1
        return web.Response(body=CONTENT)

    async def slow_handler(_request: web.Request) -> web.Response:
        gate.set()
        await asyncio.sleep(0.05)
        return web.Response(body=CONTENT)

    app = web.Application()
    app.router.add_get("/model.onnx", handler)
    app.router.add_get("/slow.onnx", slow_handler)
    test_server = TestServer(app)
    await test_server.start_server()
    yield test_server, hits
    await test_server.close()


class TestRegistry:
    def test_builtin_assets_https_and_hash(self) -> None:
        assert len(MODEL_ASSETS) >= 2
        for asset in MODEL_ASSETS:
            assert asset.url.startswith("https://")
            assert len(asset.sha256) == 64
            int(asset.sha256, 16)  # 合法十六进制
            assert asset.pip_requires and asset.import_check

    def test_models_dir_under_workspace(self) -> None:
        assert models_dir().endswith("models")


class TestSnapshot:
    def test_missing_status(self, tmp_path) -> None:
        mgr = ModelAssetManager(assets=(_asset("https://example.com/m.onnx"),),
                                directory=str(tmp_path))
        entry = mgr.snapshot()[0]
        assert entry["status"] == "missing"
        assert isinstance(entry["runtime_ready"], bool)  # 依赖本机是否装 onnxruntime
        assert mgr.resolve("test_model") is None

    def test_ready_after_verified_file(self, tmp_path) -> None:
        asset = _asset("https://example.com/m.onnx")
        (tmp_path / asset.filename).write_bytes(CONTENT)
        mgr = ModelAssetManager(assets=(asset,), directory=str(tmp_path))
        assert mgr.snapshot()[0]["status"] == "ready"
        assert mgr.resolve("test_model").endswith("test_model.onnx")

    def test_hash_mismatch_not_ready(self, tmp_path) -> None:
        asset = _asset("https://example.com/m.onnx")
        (tmp_path / asset.filename).write_bytes(b"tampered")
        mgr = ModelAssetManager(assets=(asset,), directory=str(tmp_path))
        assert mgr.snapshot()[0]["status"] == "missing"
        assert mgr.resolve("test_model") is None

    def test_unknown_asset_raises(self, tmp_path) -> None:
        mgr = ModelAssetManager(assets=(_asset("https://example.com/m.onnx"),),
                                directory=str(tmp_path))
        with pytest.raises(KeyError):
            mgr.asset("nope")


class TestDownload:
    async def test_download_verifies_and_lands(self, tmp_path, server) -> None:
        srv, hits = server
        asset = _asset(str(srv.make_url("/model.onnx")))
        mgr = ModelAssetManager(assets=(asset,), directory=str(tmp_path))
        entry = await mgr.download("test_model")
        assert entry["status"] == "ready"
        assert (tmp_path / "test_model.onnx").read_bytes() == CONTENT
        assert not list(tmp_path.glob("*.part"))
        assert hits["count"] == 1

    async def test_hash_mismatch_cleans_up(self, tmp_path, server) -> None:
        srv, _hits = server
        asset = _asset(str(srv.make_url("/model.onnx")), sha256="0" * 64)
        mgr = ModelAssetManager(assets=(asset,), directory=str(tmp_path))
        entry = await mgr.download("test_model")
        assert entry["status"] == "error"
        assert "SHA-256" in entry["error"]
        assert not list(tmp_path.glob("*.part"))
        assert not (tmp_path / "test_model.onnx").exists()

    async def test_http_error_reported(self, tmp_path, server) -> None:
        srv, _hits = server
        asset = _asset(str(srv.make_url("/missing.onnx")))
        mgr = ModelAssetManager(assets=(asset,), directory=str(tmp_path))
        entry = await mgr.download("test_model")
        assert entry["status"] == "error"

    async def test_concurrent_download_single_flight(self, tmp_path, server) -> None:
        srv, hits = server
        asset = _asset(str(srv.make_url("/model.onnx")))
        mgr = ModelAssetManager(assets=(asset,), directory=str(tmp_path))
        results = await asyncio.gather(mgr.download("test_model"),
                                       mgr.download("test_model"))
        assert all(e["status"] == "ready" for e in results)
        assert hits["count"] == 1  # 同资产并发请求合并为一次下载

    async def test_start_download_returns_progress_state(self, tmp_path, server) -> None:
        srv, _hits = server
        asset = _asset(str(srv.make_url("/slow.onnx")))
        mgr = ModelAssetManager(assets=(asset,), directory=str(tmp_path))
        entry = mgr.start_download("test_model")
        assert entry["status"] == "downloading"
        with pytest.raises(RuntimeError):  # 下载中拒绝删除
            mgr.delete("test_model")
        await mgr.download("test_model")  # 等待完成
        assert mgr.snapshot()[0]["status"] == "ready"


class TestDelete:
    def test_delete_removes_file(self, tmp_path) -> None:
        asset = _asset("https://example.com/m.onnx")
        (tmp_path / asset.filename).write_bytes(CONTENT)
        mgr = ModelAssetManager(assets=(asset,), directory=str(tmp_path))
        assert mgr.delete("test_model")["removed"] is True
        assert not (tmp_path / asset.filename).exists()
        assert mgr.delete("test_model")["removed"] is False  # 幂等


class TestMirrorFallback:
    def test_sources_auto_appends_hf_mirror(self) -> None:
        asset = _asset("https://huggingface.co/org/model.onnx")
        urls = ModelAssetManager._sources(asset)
        assert urls[0].startswith("https://huggingface.co/")
        assert urls[1].startswith("https://hf-mirror.com/")

    def test_sources_non_hf_no_mirror(self) -> None:
        asset = _asset("https://example.com/model.onnx")
        assert len(ModelAssetManager._sources(asset)) == 1

    def test_sources_custom_mirror(self, monkeypatch) -> None:
        from core.config import ConfigManager

        ConfigManager.set("model_asset_mirror", "https://my-mirror.example/")
        try:
            asset = _asset("https://huggingface.co/org/model.onnx")
            urls = ModelAssetManager._sources(asset)
            assert urls[1] == "https://my-mirror.example/org/model.onnx"
        finally:
            ConfigManager.set("model_asset_mirror", "auto")


class TestGithubMirrorFallback:
    def test_sources_auto_appends_ghfast_for_github_raw(self) -> None:
        asset = _asset("https://raw.githubusercontent.com/user/repo/v6/m.onnx")
        urls = ModelAssetManager._sources(asset)
        assert urls[0].startswith("https://raw.githubusercontent.com/")
        assert urls[1] == "https://ghfast.top/https://raw.githubusercontent.com/user/repo/v6/m.onnx"

    def test_sources_off_disables_all_mirrors(self) -> None:
        from core.config import ConfigManager

        ConfigManager.set("model_asset_mirror", "off")
        try:
            assert len(ModelAssetManager._sources(_asset("https://huggingface.co/o/m.onnx"))) == 1
            assert len(ModelAssetManager._sources(
                _asset("https://raw.githubusercontent.com/u/r/m.onnx"))) == 1
        finally:
            ConfigManager.set("model_asset_mirror", "auto")


class TestProxySelection:
    def test_https_uses_https_env(self, monkeypatch) -> None:
        from agent.model_assets import _proxy_for

        monkeypatch.setenv("https_proxy", "http://p:7890")
        monkeypatch.setenv("http_proxy", "http://other:1")
        assert _proxy_for("https://example.com/m.onnx") == "http://p:7890"

    def test_http_uses_http_env(self, monkeypatch) -> None:
        from agent.model_assets import _proxy_for

        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.setenv("HTTP_PROXY", "http://p:7890")
        assert _proxy_for("http://example.com/m.onnx") == "http://p:7890"

    def test_loopback_exempt(self, monkeypatch) -> None:
        from agent.model_assets import _proxy_for

        monkeypatch.setenv("https_proxy", "http://p:7890")
        monkeypatch.setenv("http_proxy", "http://p:7890")
        assert _proxy_for("http://127.0.0.1:8080/m.onnx") is None
        assert _proxy_for("http://localhost:8080/m.onnx") is None

    def test_no_env_direct(self, monkeypatch) -> None:
        from agent.model_assets import _proxy_for

        for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
            monkeypatch.delenv(key, raising=False)
        assert _proxy_for("https://example.com/m.onnx") is None
