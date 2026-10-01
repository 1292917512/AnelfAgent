"""POST /api/models/reload 集成测试：手改 llm_clients.json 后接口触发热重载。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def reload_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """指向临时配置的 TestClient，测试后还原路径与单例。"""
    from fastapi.testclient import TestClient

    import agent.llm.llm_manager as mgr_mod
    import services.model as model_svc_mod
    import web.routers.models as models_router
    from agent.llm.llm_manager import LLMManager
    from core.path import ConfigPaths

    llm_cfg = tmp_path / "llm_clients.json"
    webui_cfg = tmp_path / "webui.json"
    llm_cfg.write_text(json.dumps({
        "providers": [{
            "id": "p1", "base_url": "http://a", "api_key": "k",
            "models": [{"id": "m1", "model": "m1-x"}],
        }],
        "type_priorities": {"chat": ["m1"]},
        "default_chat": "m1",
    }), encoding="utf-8")
    webui_cfg.write_text(json.dumps({
        "auth": {"password": "", "api_keys": []},
        "server": {"host": "127.0.0.1", "port": 8092},
    }), encoding="utf-8")

    monkeypatch.setattr(ConfigPaths, "LLM_CLIENTS", str(llm_cfg))
    monkeypatch.setattr(ConfigPaths, "WEBUI_CONFIG", str(webui_cfg))
    monkeypatch.setattr(mgr_mod, "_manager", LLMManager(config_path=str(llm_cfg)), raising=False)

    from web.server import create_app
    app = create_app()
    models_router._svc = model_svc_mod.ModelService()
    yield TestClient(app), llm_cfg
    monkeypatch.setattr(mgr_mod, "_manager", None, raising=False)


class TestReloadEndpoint:
    def test_reload_after_manual_edit(self, reload_client) -> None:  # type: ignore[no-untyped-def]
        client, llm_cfg = reload_client
        # 手改配置文件：加供应商 + 模型 + 换默认
        llm_cfg.write_text(json.dumps({
            "providers": [
                {
                    "id": "p1", "base_url": "http://a", "api_key": "k",
                    "models": [{"id": "m1", "model": "m1-x"}],
                },
                {
                    "id": "p2", "base_url": "http://b", "api_key": "k2",
                    "models": [{"id": "m2", "model": "m2-x"}],
                },
            ],
            "type_priorities": {"chat": ["m2", "m1"]},
            "default_chat": "m2",
        }), encoding="utf-8")

        r = client.post("/api/models/reload")
        assert r.status_code == 200, r.text
        summary = r.json()
        assert summary["ok"] is True
        assert summary["providers"]["added"] == ["p2"]
        assert summary["models"]["added"] == ["m2"]
        assert summary["default_chat"] == "m2"

        # 热生效：新模型立即出现在 Web 列表
        r = client.get("/api/models/providers/p2/models")
        assert r.status_code == 200
        assert [m["id"] for m in r.json()] == ["m2"]

    def test_reload_no_change(self, reload_client) -> None:  # type: ignore[no-untyped-def]
        client, _llm_cfg = reload_client
        r = client.post("/api/models/reload")
        assert r.status_code == 200
        summary = r.json()
        assert summary["ok"] is True
        assert not summary["providers"]["added"] and not summary["models"]["changed"]

    def test_reload_corrupt_file_returns_400(self, reload_client) -> None:  # type: ignore[no-untyped-def]
        client, llm_cfg = reload_client
        llm_cfg.write_text("{broken", encoding="utf-8")
        r = client.post("/api/models/reload")
        assert r.status_code == 400
        # 运行中配置不受影响：既有模型仍可用
        r = client.get("/api/models/providers/p1/models")
        assert r.status_code == 200
        assert [m["id"] for m in r.json()] == ["m1"]
