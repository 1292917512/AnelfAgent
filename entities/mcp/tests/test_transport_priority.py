"""transport 层 Windows stdio 子进程优先级测试。

覆盖：priority 档位表、SDK spawn 补丁的安装与透传行为、
优先级 contextvar 仅在 spawn（enter）期间生效、配置面读写校验。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from entities.mcp import config as mcp_config
from entities.mcp import transport
from entities.mcp.config import MCPServerConfig, MCPServerStore


class _DummyCM:
    """记录 __aenter__ 时刻 contextvar 取值的假传输。"""

    def __init__(self) -> None:
        self.flag_at_enter = 0
        self.exited = False

    async def __aenter__(self) -> "_DummyCM":
        self.flag_at_enter = transport._stdio_priority_flag.get()
        return self

    async def __aexit__(self, *args: Any) -> bool:
        self.exited = True
        return False


@pytest.fixture
def store_with_tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MCPServerStore]:
    """把可写配置路径指向临时文件的 MCPServerStore。"""
    cfg_path = tmp_path / "mcp_servers.json"
    cfg_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    monkeypatch.setattr(mcp_config, "_writable_config_path", lambda: cfg_path)
    yield MCPServerStore()


def test_priority_flags_mapping_windows() -> None:
    """Win32 下三档优先级映射到 creationflags，其他平台为空表。"""
    transport._priority_flags_cache = None
    flags = transport._windows_priority_flags()
    try:
        if sys.platform == "win32":
            import subprocess

            assert flags["below_normal"] == int(subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            assert flags["idle"] == int(subprocess.IDLE_PRIORITY_CLASS)
            assert flags["normal"] == 0
        else:
            assert flags == {}
    finally:
        transport._priority_flags_cache = None


@pytest.mark.asyncio
async def test_scoped_transport_sets_flag_only_during_enter() -> None:
    """优先级 intent 只在 enter（spawn）期间可见，enter 后复位。"""
    dummy = _DummyCM()
    assert transport._stdio_priority_flag.get() == 0
    async with transport._priority_scoped_transport(dummy, 0x4000):
        assert dummy.flag_at_enter == 0x4000
        # spawn 已结束，contextvar 已复位
        assert transport._stdio_priority_flag.get() == 0
    assert dummy.exited
    assert transport._stdio_priority_flag.get() == 0


def test_create_transport_wraps_stdio_with_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """stdio + 有效 priority 时安装补丁并返回优先级包装传输。"""
    installed: list[bool] = []
    sentinel = object()
    wrapped_with: list[tuple[object, int]] = []

    def _fake_scoped(client_cm: object, flag: int) -> object:
        wrapped_with.append((client_cm, flag))
        return sentinel

    monkeypatch.setattr(transport, "_install_stdio_priority_patch", lambda: installed.append(True))
    monkeypatch.setattr(transport, "_priority_scoped_transport", _fake_scoped)
    monkeypatch.setattr(
        transport, "_windows_priority_flags", lambda: {"below_normal": 0x4000}
    )
    srv = MCPServerConfig(
        name="demo", command="node", transport="stdio", priority="below_normal"
    )
    cm = transport._create_transport(srv)
    if sys.platform == "win32":
        assert installed == [True]
        assert cm is sentinel
        assert len(wrapped_with) == 1
        assert wrapped_with[0][1] == 0x4000
    else:
        assert installed == []
        assert cm is not sentinel


def test_create_transport_without_priority_uses_plain_client() -> None:
    """未配置 priority 时保持 SDK 原生行为（无补丁无包装）。"""
    srv = MCPServerConfig(name="demo", command="node", transport="stdio")
    cm = transport._create_transport(srv)
    assert type(cm).__name__ == "_AsyncGeneratorContextManager"


def test_create_transport_normal_priority_uses_plain_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """normal 档等效 SDK 默认（flag 0），不走包装。"""
    monkeypatch.setattr(
        transport, "_windows_priority_flags", lambda: {"normal": 0}
    )
    srv = MCPServerConfig(name="demo", command="node", transport="stdio", priority="normal")
    cm = transport._create_transport(srv)
    assert type(cm).__name__ == "_AsyncGeneratorContextManager"


# ------------------------------------------------------------------
# 配置面：priority 字段解析与校验
# ------------------------------------------------------------------


def test_server_config_roundtrip_priority(store_with_tmp_config: MCPServerStore) -> None:
    """priority 字段经 store 写入后可原样读回。"""
    store_with_tmp_config.update_server_config(
        "demo", {"command": "node", "priority": "below_normal"},
        replace=True, create_if_missing=True, reload=False,
    )
    cfg = store_with_tmp_config.get_server_config("demo")
    assert cfg is not None and cfg.get("priority") == "below_normal"


def test_parse_mcp_data_picks_up_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """从 JSON 文件加载时 priority 进入 MCPServerConfig。"""
    cfg_path = tmp_path / "mcp_servers.json"
    cfg_path.write_text(
        json.dumps({"mcpServers": {"demo": {"command": "node", "priority": "idle"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_config, "_resolve_config_path", lambda: str(cfg_path))
    cfg = mcp_config.load_mcp_config()
    assert cfg.servers[0].priority == "idle"


def test_store_rejects_invalid_priority(store_with_tmp_config: MCPServerStore) -> None:
    with pytest.raises(ValueError, match="priority"):
        store_with_tmp_config.update_server_config(
            "demo", {"command": "node", "priority": "realtime"},
            replace=True, create_if_missing=True,
        )


# ------------------------------------------------------------------
# spawn：creationflags 注入与 Job Object 语义
# ------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows spawn 路径")
@pytest.mark.asyncio
async def test_spawn_process_injects_priority_creationflags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """优先级 spawn 给 anyio.open_process 追加 creationflags 并保持 Job Object 助手调用。"""
    import subprocess

    import anyio
    from mcp.os.win32 import utilities as win32_util

    captured: dict[str, Any] = {}
    job_calls: list[bool] = []

    class _FakeProcess:
        pass

    async def _fake_open_process(command: Any, **kwargs: Any) -> _FakeProcess:
        captured["command"] = command
        captured.update(kwargs)
        return _FakeProcess()

    def _fake_create_job() -> object:
        job_calls.append(True)
        return object()

    monkeypatch.setattr(anyio, "open_process", _fake_open_process)
    monkeypatch.setattr(win32_util, "_create_job_object", _fake_create_job)
    monkeypatch.setattr(
        win32_util, "_maybe_assign_process_to_job", lambda proc, job: job_calls.append(True)
    )

    process = await transport._spawn_windows_process_with_priority(
        "node", ["server.js"], {"A": "1"}, None, None, 0x4000
    )
    assert isinstance(process, _FakeProcess)
    assert captured["creationflags"] & 0x4000
    assert captured["creationflags"] & int(subprocess.CREATE_NO_WINDOW)
    assert captured["command"] == ["node", "server.js"]
    assert job_calls == [True, True]
