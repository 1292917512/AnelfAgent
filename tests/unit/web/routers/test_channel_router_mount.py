"""web.server 频道路由挂载测试：create_app() 自动发现挂载频道 build_router() 的路由。

以微信扫码登录路由为样本端到端走通（mock QR 管理器），
验证「频道目录声明 build_router → web 层自动挂载」的装配契约。
"""

from __future__ import annotations


class TestChannelRouterMount:
    def test_hot_reload_updates_routes_removes_obsolete_routes_and_preserves_failed_build(self, monkeypatch):
        from fastapi import APIRouter, FastAPI
        from fastapi.testclient import TestClient

        import channels.weixin.adapter as module
        import web.server as server

        app = FastAPI()

        def router(version):
            result = APIRouter()

            @result.get(f"/version{version}")
            def version_route():
                return {"version": version}

            return result

        monkeypatch.setattr(module, "build_router", lambda: router(1))
        server._mount_one_module_router(app, "channel", "weixin")
        client = TestClient(app)
        assert client.get("/api/channels/weixin/version1").json() == {"version": 1}
        assert "/api/channels/weixin/version1" in client.get("/openapi.json").json()["paths"]
        monkeypatch.setattr(module, "build_router", lambda: router(2))
        server._mount_one_module_router(app, "channel", "weixin")
        assert client.get("/api/channels/weixin/version1").status_code == 404
        assert client.get("/api/channels/weixin/version2").json() == {"version": 2}
        assert "/api/channels/weixin/version1" not in client.get("/openapi.json").json()["paths"]
        monkeypatch.setattr(module, "build_router", lambda: None)
        server._mount_one_module_router(app, "channel", "weixin")
        assert client.get("/api/channels/weixin/version2").status_code == 200
        monkeypatch.delattr(module, "build_router")
        server._mount_one_module_router(app, "channel", "weixin")
        assert client.get("/api/channels/weixin/version2").status_code == 404

    def test_server_mounts_channel_routers(self, monkeypatch):
        """create_app() 挂载微信扫码路由，端到端走通（mock QR 管理器）。"""
        from unittest.mock import AsyncMock

        import channels.weixin.qr_login as qr_mod

        mock_manager = AsyncMock()
        mock_manager.start.return_value = {
            "session_id": "s1", "qr_png": "data:png,x", "qr_url": "u",
        }
        mock_manager.poll.return_value = {"status": "wait"}
        monkeypatch.setattr(qr_mod, "get_qr_manager", lambda: mock_manager)

        import web.server as server_mod

        monkeypatch.setattr(server_mod, "_load_auth_password", lambda: "")

        from fastapi.testclient import TestClient

        client = TestClient(server_mod.create_app())
        resp = client.post("/api/channels/weixin/qr/start")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "s1"
        resp = client.get("/api/channels/weixin/qr/s1/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "wait"
