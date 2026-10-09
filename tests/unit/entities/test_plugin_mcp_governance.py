"""插件 MCP reconcile 保留用户治理状态 + channel-only 连接语义测试。"""

import json
from types import SimpleNamespace

import pytest

from entities.mcp.bridge import MCPBridge, _is_method_not_found


@pytest.fixture()
def mcp_config_file(tmp_path, monkeypatch):
    """隔离的 mcp_servers.json（env 指向临时文件）。"""
    path = tmp_path / "mcp_servers.json"
    monkeypatch.setenv("ANELF_MCP_CONFIG", str(path))
    return path


class TestReconcileKeepsGovernedFields:
    @staticmethod
    def _manifest():
        return SimpleNamespace(
            mcp_servers_inline={"eigenflux": {
                "command": "bun", "args": ["run"], "transport": "stdio",
            }},
            mcp_servers_file="",
        )

    def test_reactivate_keeps_enabled_and_stay_awake(self, mcp_config_file, tmp_path):
        """插件重激活以清单更新连接参数，但启停/常驻等治理字段保留。"""
        mcp_config_file.write_text(json.dumps({"mcpServers": {
            "eigenflux": {
                "command": "/abs/path/bun", "transport": "stdio",
                "enabled": False, "stay_awake": True, "plugin": "eigenflux",
            },
        }}), encoding="utf-8")

        from entities.plugins.activation import _activate_mcp_servers

        added = _activate_mcp_servers("eigenflux", tmp_path, self._manifest())
        assert added == ["eigenflux"]

        data = json.loads(mcp_config_file.read_text(encoding="utf-8"))
        cfg = data["mcpServers"]["eigenflux"]
        assert cfg["command"] == "bun"          # 连接参数以清单为准
        assert cfg["enabled"] is False          # 禁用状态不被翻回
        assert cfg["stay_awake"] is True        # 常驻开关不被抹掉
        assert cfg["plugin"] == "eigenflux"

    def test_field_wiped_entry_adopted_and_prefix_swept(self, mcp_config_file, tmp_path):
        """plugin 字段被外部改写抹掉时认领养（不增生前缀），历史前缀副本回收。"""
        mcp_config_file.write_text(json.dumps({"mcpServers": {
            "eigenflux": {"command": "old", "transport": "stdio", "enabled": False},
            "eigenflux__eigenflux": {
                "command": "old", "transport": "stdio",
                "enabled": True, "plugin": "eigenflux",
            },
        }}), encoding="utf-8")

        from entities.plugins.activation import _activate_mcp_servers

        added = _activate_mcp_servers("eigenflux", tmp_path, self._manifest())
        assert added == ["eigenflux"]

        data = json.loads(mcp_config_file.read_text(encoding="utf-8"))
        servers = data["mcpServers"]
        assert "eigenflux__eigenflux" not in servers   # 前缀残留回收
        cfg = servers["eigenflux"]
        assert cfg["plugin"] == "eigenflux"            # 来源标记回补
        assert cfg["enabled"] is False                 # 治理字段继承
        assert cfg["command"] == "bun"

    def test_other_plugin_claim_keeps_prefix(self, mcp_config_file, tmp_path):
        """同名被他插件占用时仍走前缀，且前缀目标已存在时继承其治理字段。"""
        mcp_config_file.write_text(json.dumps({"mcpServers": {
            "eigenflux": {"command": "x", "plugin": "other", "enabled": True},
            "eigenflux__eigenflux": {
                "command": "x", "plugin": "eigenflux", "enabled": False,
            },
        }}), encoding="utf-8")

        from entities.plugins.activation import _activate_mcp_servers

        added = _activate_mcp_servers("eigenflux", tmp_path, self._manifest())
        assert added == ["eigenflux__eigenflux"]

        data = json.loads(mcp_config_file.read_text(encoding="utf-8"))
        assert data["mcpServers"]["eigenflux"]["plugin"] == "other"  # 不抢他插件
        assert data["mcpServers"]["eigenflux__eigenflux"]["enabled"] is False


def _fake_session(method_not_found: bool):
    """list_tools 按场景抛错的假 session。"""
    from mcp.shared.exceptions import MCPError

    async def list_tools():
        if method_not_found:
            raise MCPError(-32601, "Method not found")
        raise RuntimeError("boom")

    return SimpleNamespace(list_tools=list_tools)


@pytest.fixture()
def bridge():
    """最小 bridge 实例（不经 __init__ 的事件循环线程）。"""
    import threading

    b = MCPBridge.__new__(MCPBridge)
    b._lock = threading.Lock()
    b._channel_only = set()
    b._tool_server_map = {}
    b._tool_original_names = {}
    yield b
    from core.entity import EntityRegistry
    EntityRegistry.unregister("mcp:t-srv")
    EntityRegistry.unregister_group("mcp:t-srv")


class TestChannelOnly:
    def test_method_not_found_registers_zero_tools(self, bridge):
        from core.entity import EntityRegistry
        from entities.mcp.config import MCPServerConfig

        srv = MCPServerConfig(name="t-srv", command="bun", enabled=True)
        import asyncio
        count = asyncio.run(bridge._register_server_tools(srv, _fake_session(True)))
        assert count == 0
        assert "t-srv" in bridge._channel_only
        entity = EntityRegistry.get("mcp:t-srv")
        assert entity is not None and entity.meta.get("channel_only") is True
        assert "channel-only" in EntityRegistry.get_group_description("mcp:t-srv")

    def test_other_errors_propagate(self, bridge):
        from entities.mcp.config import MCPServerConfig

        srv = MCPServerConfig(name="t-srv", command="bun", enabled=True)
        with pytest.raises(RuntimeError):
            import asyncio
            asyncio.run(bridge._register_server_tools(srv, _fake_session(False)))
        assert "t-srv" not in bridge._channel_only

    def test_is_method_not_found_discriminates(self):
        from mcp.shared.exceptions import MCPError

        assert _is_method_not_found(MCPError(-32601, "Method not found"))
        assert not _is_method_not_found(MCPError(-32000, "other"))
        assert not _is_method_not_found(RuntimeError("Method not found"))


class TestServiceFields:
    def test_list_servers_surfaces_plugin_and_channel_only(self, monkeypatch):
        """Web 面板数据源透出 plugin 来源与 channel_only 状态。"""
        from services.mcp import MCPService

        svc = MCPService.__new__(MCPService)
        monkeypatch.setattr(svc, "load_config", lambda: {"mcpServers": {
            "eigenflux": {"command": "bun", "transport": "stdio",
                          "enabled": False, "plugin": "eigenflux"},
            "plain": {"url": "https://x", "enabled": True},
        }})
        monkeypatch.setattr(svc, "get_connected_tools", lambda: {})
        monkeypatch.setattr(svc, "get_last_errors", lambda: {})
        monkeypatch.setattr(svc, "get_channel_only", lambda: {"eigenflux"})
        monkeypatch.setattr(svc, "_effective_sleeping", lambda name: False)

        rows = {r["name"]: r for r in svc.list_servers()}
        assert rows["eigenflux"]["plugin"] == "eigenflux"
        assert rows["eigenflux"]["channel_only"] is True
        assert rows["plain"]["plugin"] == ""
        assert rows["plain"]["channel_only"] is False
