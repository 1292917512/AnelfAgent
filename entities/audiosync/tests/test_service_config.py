import json

import pytest

from entities.audiosync.tools import audiosync_service


class TestServiceConfig:
    @pytest.fixture(autouse=True)
    def _funasr_cred(self, tmp_path, monkeypatch):
        """FunASR 凭据写入隔离的凭据中心存储（不碰真凭据文件）。"""
        from core import provider_keys as pk

        monkeypatch.setattr(pk, "_path", lambda: str(tmp_path / "keys.json"))
        monkeypatch.setattr(pk, "_cache", None)

    async def test_get_includes_funasr(self, monkeypatch) -> None:
        from core import provider_keys as pk
        from entities.audiosync import client as funasr_client

        async def fake_probe() -> bool:
            return True

        monkeypatch.setattr(funasr_client, "probe_available", fake_probe)
        monkeypatch.setattr(funasr_client, "reset_probe_cache", lambda: None)
        pk.set_provider_key("funasr", "funasr_endpoint", "http://funasr.local")
        out = json.loads(await audiosync_service(action="get"))
        assert out["config"]["funasr_reachable"] is True

    async def test_set_funasr_endpoint_reports_reachability(self, monkeypatch) -> None:
        from entities.audiosync import client as funasr_client

        async def fake_probe() -> bool:
            return False

        monkeypatch.setattr(funasr_client, "probe_available", fake_probe)
        monkeypatch.setattr(funasr_client, "reset_probe_cache", lambda: None)
        out = json.loads(await audiosync_service(action="set", key="funasr_endpoint",
                                            value="http://funasr.local"))
        assert out["success"] is True
        assert out["value"] == "http://funasr.local"
        assert out["reachable"] is False
