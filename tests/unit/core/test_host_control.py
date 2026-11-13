"""独立守护的控制鉴权、跨进程锁与配置覆盖。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.host_control import HostControl, register_host_control
from core.host_protocol import HostLock, write_json
from core.lifecycle import Lifecycle


@pytest.mark.parametrize("failure", ["transient", "permission", "other"])
def test_host_record_replacement_preserves_atomicity_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    path = tmp_path / "installation.json"
    write_json(path, {"ready": False})
    replace = os.replace
    attempts = 0

    def occupied(source: str, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        assert json.loads(path.read_text("utf-8")) == {"ready": False}
        if failure == "other":
            raise OSError("replacement failed")
        if failure == "permission" or attempts == 1:
            raise PermissionError("replacement denied")
        replace(source, destination)

    monkeypatch.setattr("core.host_protocol.os.replace", occupied)
    monkeypatch.setattr("core.host_protocol.time.sleep", lambda _: None)
    if failure == "transient":
        write_json(path, {"ready": True})
        assert attempts == 2
        assert json.loads(path.read_text("utf-8")) == {"ready": True}
    else:
        with pytest.raises(PermissionError if failure == "permission" else OSError):
            write_json(path, {"ready": True})
        assert json.loads(path.read_text("utf-8")) == {"ready": False}
        assert 1 < attempts < 20 if failure == "permission" else attempts == 1
    assert list(tmp_path.iterdir()) == [path]


def test_host_lock_is_exclusive_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "project.lock"
    lock = HostLock(path)
    assert lock.acquire()
    assert not HostLock(path).acquire()
    code = "from pathlib import Path; from core.host_protocol import HostLock; import sys; " \
           "lock=HostLock(Path(sys.argv[1])); print(lock.acquire())"
    try:
        result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == "False"
    finally:
        lock.release()
    other = HostLock(path)
    assert other.acquire()
    other.release()
    assert path.exists()


async def test_control_accepts_only_its_token_and_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "control.json"
    service = HostControl(path, "secret")
    requests: list[bool] = []
    monkeypatch.setattr(Lifecycle, "request_shutdown", lambda: requests.append(True))
    service.start()
    try:
        write_json(path, {"action": "stop", "token": "wrong", "pid": os.getpid()})
        await asyncio.sleep(0.3)
        assert not requests
        write_json(path, {"action": "execute", "token": "secret", "pid": os.getpid()})
        await asyncio.sleep(0.3)
        assert not requests
        write_json(path, {"action": "stop", "token": "secret", "pid": os.getpid()})
        await asyncio.sleep(0.3)
        assert requests == [True]
        assert not path.exists()
    finally:
        await service.stop()


def test_direct_start_registers_control_without_launcher_environment(tmp_path: Path) -> None:
    import json
    register_host_control(tmp_path, restart=lambda: {"ok": True}, web_url=lambda: "http://127.0.0.1:8092/webui/")
    value = json.loads((tmp_path / "logs/manager/runtime.json").read_text("utf-8"))
    assert value["pid"] == os.getpid()
    assert value["phase"] == "starting"
    assert len(value["token"]) >= 32
    assert value["web_url"].endswith("8092/webui/")


async def test_host_restart_uses_injected_devops_and_publishes_state(tmp_path: Path) -> None:
    import json
    path = tmp_path / "control.json"
    calls: list[bool] = []
    def restart() -> dict[str, object]:
        calls.append(True)
        return {"ok": True}
    service = HostControl(path, "secret", connection=tmp_path / "runtime.json", restart=restart)
    service.start()
    try:
        write_json(path, {"action": "restart", "token": "secret", "pid": os.getpid(), "id": "request"})
        await asyncio.sleep(0.3)
        assert calls == [True]
        assert json.loads((tmp_path / "runtime.json").read_text())["phase"] == "restarting"
        assert json.loads(path.with_suffix(".result.json").read_text()) == {"ok": True, "id": "request"}
    finally:
        await service.stop()


async def test_running_is_published_only_after_lifecycle_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    ready = False
    monkeypatch.setattr(Lifecycle, "started", lambda: ready)
    connection = tmp_path / "runtime.json"
    service = HostControl(tmp_path / "control.json", "secret", connection=connection)
    service.start()
    try:
        await asyncio.sleep(.3)
        assert json.loads(connection.read_text())["phase"] == "starting"
        ready = True
        await asyncio.sleep(.3)
        assert json.loads(connection.read_text())["phase"] == "running"
    finally:
        await service.stop()


async def test_host_republishes_its_identity_without_resetting_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    monkeypatch.setattr(Lifecycle, "started", lambda: True)
    connection = tmp_path / "runtime.json"
    service = HostControl(tmp_path / "control.json", "secret", connection=connection)
    service.start()
    try:
        await asyncio.sleep(.3)
        scoped = tmp_path / f"runtime-{os.getpid()}.json"
        assert json.loads(scoped.read_text())["phase"] == "running"
        write_json(connection, {"pid": 2147483647, "phase": "starting"})
        service._published_at = 0
        await asyncio.sleep(.3)
        assert json.loads(connection.read_text())["pid"] == os.getpid()
        service.publish("stopping")
        service._published_at = 0
        await asyncio.sleep(.3)
        assert json.loads(connection.read_text())["phase"] == "stopping"
    finally:
        await service.stop()
    assert not (tmp_path / f"runtime-{os.getpid()}.json").exists()


def test_webui_environment_override_preserves_persisted_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.path import ConfigPaths
    from web.server import _load_server_config
    path = tmp_path / "webui.json"
    write_json(path, {"server": {"host": "0.0.0.0", "port": 8123}, "api_keys": ["keep"]})
    monkeypatch.setattr(ConfigPaths, "WEBUI_CONFIG", str(path))
    monkeypatch.delenv("ANELF_WEBUI_HOST", raising=False)
    monkeypatch.delenv("ANELF_WEBUI_PORT", raising=False)
    before = path.read_bytes()
    assert _load_server_config() == ("0.0.0.0", 8123)
    monkeypatch.setenv("ANELF_WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("ANELF_WEBUI_PORT", "8130")
    assert _load_server_config() == ("127.0.0.1", 8130)
    assert path.read_bytes() == before
    monkeypatch.setenv("ANELF_WEBUI_PORT", "70000")
    with pytest.raises(ValueError):
        _load_server_config()
