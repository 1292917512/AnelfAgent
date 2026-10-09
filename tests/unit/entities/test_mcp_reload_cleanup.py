"""MCP 热重载禁用清理测试：连接失败的 server 禁用/删除时不残留错误与注册表。"""

import threading

import pytest

import entities.mcp.bridge as bridge_mod
from entities.mcp.bridge import MCPBridge
from entities.mcp.config import MCPConfig, MCPServerConfig


@pytest.fixture()
def bridge(monkeypatch):
    """最小 bridge 实例 + 断开调用记录（不触事件循环与真实断开）。"""
    b = MCPBridge.__new__(MCPBridge)
    b._reload_lock = threading.RLock()
    b._lock = threading.RLock()
    b.config = MCPConfig(servers=[])
    b._sessions = {}
    b._stop_events = {}
    calls: list[str] = []
    monkeypatch.setattr(b, "disconnect_server_by_name", lambda name: calls.append(name))
    return b, calls


def _old_enabled(name="a"):
    return MCPServerConfig(name=name, command="bun", enabled=True)


class TestReloadCleanup:
    def test_disable_failed_server_cleans_up(self, bridge, monkeypatch):
        """连接失败的 server（无 session/无 lifecycle task）禁用时也走清理。"""
        b, calls = bridge
        b.config = MCPConfig(servers=[_old_enabled()])
        monkeypatch.setattr(
            bridge_mod, "load_mcp_config",
            lambda path=None: MCPConfig(servers=[
                MCPServerConfig(name="a", command="bun", enabled=False),
            ]),
        )
        b.reload_config()
        assert calls == ["a"]

    def test_remove_failed_server_cleans_up(self, bridge, monkeypatch):
        b, calls = bridge
        b.config = MCPConfig(servers=[_old_enabled()])
        monkeypatch.setattr(
            bridge_mod, "load_mcp_config", lambda path=None: MCPConfig(servers=[]))
        b.reload_config()
        assert calls == ["a"]

    def test_still_enabled_unconnected_skips_disconnect(self, bridge, monkeypatch):
        """仍启用且未连接的变更 server 保持原语义：跳过断开，随后直接重连。"""
        b, calls = bridge
        b.config = MCPConfig(servers=[_old_enabled()])
        monkeypatch.setattr(
            bridge_mod, "load_mcp_config",
            lambda path=None: MCPConfig(servers=[
                MCPServerConfig(name="a", command="node", enabled=True),
            ]),
        )
        b.reload_config()
        assert calls == []
