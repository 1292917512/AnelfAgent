"""代理桌面组件只读取公共状态，并按当前证据注入。"""

from core.config import ConfigManager
from core.network.status import ProxyObservations
from entities.ai_desktop.modules.network.module import NetworkModule


def test_proxy_context_is_passive_compact_and_configuration_scoped(monkeypatch):
    import httpx

    now = 1000.0
    monkeypatch.setattr("core.network.status.time.time", lambda: now)
    ConfigManager.set("proxy_enabled", True)
    ConfigManager.set("http_proxy", "http://proxy.test:7890")
    observations = ProxyObservations()
    monkeypatch.setattr("entities.ai_desktop.modules.network.module.get_proxy_status", observations.snapshot)
    module = NetworkModule()
    assert module.render() is None and not module.due(now)
    observations.record("http://proxy.test:7890", "https://api.example.com", error=httpx.ConnectTimeout("slow"))
    assert "ConnectTimeout" in module.render()
    assert len(module.detail()["network"]["observations"]) == 1
    observations.record("http://proxy.test:7890", "https://api.example.com", status_code=200)
    assert "已恢复连接" in module.render()
    now += 121
    assert module.render() is None
    ConfigManager.set("proxy_enabled", False)
    assert module.render() is None
