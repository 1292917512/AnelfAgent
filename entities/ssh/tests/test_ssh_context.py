"""SshStatusProvider 花名册渲染测试：全量连接（含离线）+ 缺省/在线/异常标记。"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from entities.ssh import context as context_module
from entities.ssh.context import SshStatusProvider, _render_roster_line
from entities.ssh.manager import (
    STATUS_CONNECTED,
    STATUS_CONNECTING,
    STATUS_DISCONNECTED,
    STATUS_ERROR,
)


class _FakeManager:
    """list_statuses 替身（避免触达真实连接池与凭据存储）。"""

    def __init__(self, statuses: List[Dict[str, Any]]) -> None:
        self._statuses = statuses

    def list_statuses(self) -> List[Dict[str, Any]]:
        return [dict(s) for s in self._statuses]


def _status(name: str = "web", **overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "name": name,
        "host": "192.168.1.10",
        "port": 22,
        "username": "root",
        "description": "",
        "status": STATUS_DISCONNECTED,
        "last_error": "",
        "is_default": False,
        "work_dir": "",
        "home_missing": "",
    }
    base.update(overrides)
    return base


@pytest.fixture
def provider() -> SshStatusProvider:
    return SshStatusProvider()


def _patch_manager(monkeypatch: pytest.MonkeyPatch, statuses: List[Dict[str, Any]]) -> None:
    monkeypatch.setattr(context_module, "get_ssh_manager", lambda: _FakeManager(statuses))


class TestRoster:
    async def test_no_connections_renders_none(
        self, provider: SshStatusProvider, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_manager(monkeypatch, [])
        assert await provider.provide("user_a:1") is None

    async def test_disconnected_roster_with_default_mark(
        self, provider: SshStatusProvider, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_manager(monkeypatch, [
            _status("nas", host="192.168.1.4", username="admin",
                    is_default=True, description="内网存储"),
            _status("web"),
        ])
        snapshot = await provider.provide("user_a:1")
        assert snapshot is not None
        content = snapshot.content or ""
        assert "[SSH 远程连接]" in content
        assert "- nas: admin@192.168.1.4（缺省） - 内网存储" in content
        assert "- web: root@192.168.1.10" in content
        # 离线连接不带状态标记
        assert "（离线）" not in content

    async def test_connected_shows_work_dir(
        self, provider: SshStatusProvider, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_manager(monkeypatch, [
            _status("web", status=STATUS_CONNECTED, work_dir="/var/www"),
        ])
        snapshot = await provider.provide("user_a:1")
        assert snapshot is not None
        assert "（在线 · 目录 /var/www）" in (snapshot.content or "")

    async def test_error_shows_last_error(
        self, provider: SshStatusProvider, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_manager(monkeypatch, [
            _status("web", status=STATUS_ERROR,
                    last_error="TCP 连接被拒绝: 192.168.1.10:22 端口未开放"),
        ])
        snapshot = await provider.provide("user_a:1")
        assert snapshot is not None
        content = snapshot.content or ""
        assert "连接失败: TCP 连接被拒绝" in content

    async def test_home_missing_mark(
        self, provider: SshStatusProvider, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_manager(monkeypatch, [
            _status("nas", status=STATUS_CONNECTED, home_missing="/home/admin"),
        ])
        snapshot = await provider.provide("user_a:1")
        assert snapshot is not None
        assert "登录目录缺失，命令起点 /" in (snapshot.content or "")


class TestRosterLine:
    def test_non_default_port_shown(self) -> None:
        line = _render_roster_line(_status("web", port=2222))
        assert line.startswith("- web: root@192.168.1.10:2222")

    def test_default_port_omitted(self) -> None:
        line = _render_roster_line(_status("web"))
        assert ":22" not in line

    def test_connecting_mark(self) -> None:
        line = _render_roster_line(_status("web", status=STATUS_CONNECTING))
        assert "（连接中）" in line

    def test_marks_order_default_first(self) -> None:
        line = _render_roster_line(_status(
            "web", is_default=True, status=STATUS_CONNECTED, work_dir="/srv",
        ))
        assert "（缺省 · 在线 · 目录 /srv）" in line

    def test_description_newline_flattened(self) -> None:
        line = _render_roster_line(_status("web", description="第一行\n第二行"))
        assert "第一行 第二行" in line
        assert "\n" not in line
