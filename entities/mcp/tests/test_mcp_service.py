"""MCPService 配置读写 / 校验 / 脱敏单元测试。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Generator

import pytest

from services.mcp import MCPService


@pytest.fixture()
def svc(monkeypatch: pytest.MonkeyPatch) -> Generator[MCPService, None, None]:
    """内存配置存储 + 禁用热重载，避免触碰真实配置文件与 MCP Bridge。"""
    store: Dict[str, Any] = {
        "mcpServers": {
            "alpha": {"url": "http://localhost:8000/mcp", "enabled": True},
            "beta": {"command": "npx", "args": ["-y", "demo"], "enabled": False},
        }
    }

    def fake_load(self: MCPService) -> Dict[str, Any]:
        return json.loads(json.dumps(store))

    def fake_save(self: MCPService, data: Dict[str, Any]) -> None:
        store.clear()
        store.update(json.loads(json.dumps(data)))

    monkeypatch.setattr(MCPService, "load_config", fake_load)
    monkeypatch.setattr(MCPService, "save_config", fake_save)
    monkeypatch.setattr(MCPService, "_trigger_reload", staticmethod(lambda: None))
    yield MCPService()


class TestUpdateValidation:
    def test_rejects_unknown_field(self, svc: MCPService) -> None:
        with pytest.raises(ValueError, match="不支持的字段"):
            svc.update_server_config("alpha", {"evil": "x"}, reload=False)

    def test_rejects_bad_transport(self, svc: MCPService) -> None:
        with pytest.raises(ValueError, match="transport"):
            svc.update_server_config("alpha", {"transport": "websocket"}, reload=False)

    def test_rejects_non_positive_timeout(self, svc: MCPService) -> None:
        with pytest.raises(ValueError, match="必须 > 0"):
            svc.update_server_config("alpha", {"timeout": 0}, reload=False)

    def test_requires_url_or_command(self, svc: MCPService) -> None:
        with pytest.raises(ValueError, match="url 或 command"):
            svc.update_server_config("alpha", {"url": ""}, replace=True, reload=False)

    def test_missing_server_rejected_without_create_flag(self, svc: MCPService) -> None:
        with pytest.raises(ValueError, match="不存在"):
            svc.update_server_config("ghost", {"url": "http://x"}, reload=False)

    def test_merge_preserves_existing_fields(self, svc: MCPService) -> None:
        svc.update_server_config("beta", {"env": {"A": "1"}}, reload=False)
        cfg = svc.get_server_config("beta")
        assert cfg is not None
        assert cfg["command"] == "npx"
        assert cfg["env"] == {"A": "1"}

    def test_create_with_full_config(self, svc: MCPService) -> None:
        result = svc.create_server("gamma", {
            "command": "uvx",
            "args": ["demo"],
            "headers": {"Authorization": "Bearer token"},
            "timeout": 10,
        })
        assert result["after"]["transport"] == "stdio"
        assert result["after"]["enabled"] is True
        assert svc.get_server_config("gamma") is not None

    def test_create_rejects_duplicate_name(self, svc: MCPService) -> None:
        with pytest.raises(ValueError, match="已存在"):
            svc.create_server("alpha", {"url": "http://evil.example.com"})


class TestConcurrentWrites:
    def test_concurrent_updates_do_not_lose_servers(self, svc: MCPService) -> None:
        """多线程并发更新不同 server，最终配置不得丢失任何一方。"""

        def update(i: int) -> None:
            svc.update_server_config(
                f"server-{i}", {"url": f"http://localhost:{9000 + i}"},
                create_if_missing=True, reload=False,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(update, range(20)))

        names = svc.get_server_names()
        for i in range(20):
            assert f"server-{i}" in names
        assert "alpha" in names and "beta" in names

    def test_concurrent_enabled_toggle_consistent(self, svc: MCPService) -> None:
        def toggle(i: int) -> None:
            svc.set_server_enabled("alpha", i % 2 == 0, reload=False)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(toggle, range(10)))

        cfg = svc.get_server_config("alpha")
        assert cfg is not None
        assert isinstance(cfg["enabled"], bool)


class TestListServers:
    def test_masks_url_with_embedded_key(
        self, svc: MCPService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import services.mcp as mcp_module
        monkeypatch.setattr(mcp_module, "is_sanitize_enabled", lambda: True)

        svc.update_server_config(
            "secret",
            {"url": "https://mcp.example.com/?api_key=sk-ant-abcdefghijklmnopqrstuvwxyz"},
            create_if_missing=True, reload=False,
        )
        listed = {s["name"]: s for s in svc.list_servers()}
        assert "sk-ant-abcdefghijklmnopqrstuvwxyz" not in listed["secret"]["url"]
        assert "****" in listed["secret"]["url"]

    def test_includes_transport_and_last_error(self, svc: MCPService) -> None:
        listed = {s["name"]: s for s in svc.list_servers()}
        assert listed["alpha"]["transport"] == "streamable_http"
        assert listed["beta"]["transport"] == "stdio"
        assert listed["alpha"]["last_error"] == ""

    def test_remove_missing_server_raises(self, svc: MCPService) -> None:
        with pytest.raises(ValueError, match="不存在"):
            svc.remove_server("ghost")
