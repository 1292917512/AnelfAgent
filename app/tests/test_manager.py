"""管理器的安装保护、HTTP 安全边界与真实子进程重启协议。"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

import pytest

from app.environment import Environment, unpack
from app.manager import Manager, pid_alive
from app.server import ManagerServer
from app.settings import Layout, Settings, probe_port, resolve_source
from core.host_protocol import HostLock


def wait_for(predicate: Callable[[], bool], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Expected state was not reached")
        time.sleep(0.03)


def test_stop_without_control_fails_before_full_shutdown_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = Manager(Layout(tmp_path))
    monkeypatch.setattr(manager, "_external_pid", lambda: 0)
    monkeypatch.setattr(manager, "_state", lambda: ("starting", None, None))
    ticks = iter([0.0, 0.0, 11.0, 11.0])
    monkeypatch.setattr("app.manager.time.monotonic", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="Service control is unavailable"):
        manager._stop()


@pytest.mark.parametrize("value", [{"web_port": 0}, {"web_port": 65536}, {"web_port": True},
                                  {"web_port": "8092"}, {"web_host": "example.com"},
                                  {"stop_timeout": 1}, {"auto_start": "yes"}, {"unknown": 1}])
def test_invalid_settings_are_rejected(value: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError)):
        Settings.parse(value)


def test_port_probe_and_settings_are_non_destructive(tmp_path: Path) -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        assert not probe_port("127.0.0.1", port)["available"]
        manager = Manager(Layout(tmp_path))
        with pytest.raises(RuntimeError, match="Port unavailable"):
            manager.configure({"web_port": port})
        assert not manager.layout.settings_file.exists()
    assert probe_port("127.0.0.1", port)["available"]
    manager.configure({"web_port": port})
    assert manager.layout.load_settings().web_port == port


def test_archive_traversal_and_links_are_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("../escape", "bad")
    with pytest.raises(ValueError, match="Unsafe"):
        unpack(archive, tmp_path / "output")
    assert not (tmp_path / "escape").exists()
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as source:
        member = tarfile.TarInfo("link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../escape"
        source.addfile(member)
        member = tarfile.TarInfo("ordinary")
        member.size = 2
        source.addfile(member, io.BytesIO(b"ok"))
    with pytest.raises(ValueError, match="Unsafe"):
        unpack(archive, tmp_path / "tar-output")


def test_dirty_update_does_not_stop_application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
    manager = Manager(Layout(tmp_path))
    monkeypatch.setattr(manager.environment, "install_tools", lambda: None)
    monkeypatch.setattr(manager.environment, "tool", lambda name: name)
    monkeypatch.setattr(manager.environment, "run", lambda *args, **kwargs: " M launch.py")
    stopped: list[bool] = []
    monkeypatch.setattr(manager, "_stop", lambda: stopped.append(True))
    manager.submit("update")
    wait_for(lambda: not manager.status()["busy"])
    assert not manager.status()["last"]["ok"]
    assert "Uncommitted" in manager.status()["last"]["message"]
    assert not stopped


@pytest.mark.parametrize("source,url", [("github", "https://github.com/1292917512/AnelfAgent.git"),
                                        ("gitee", "https://gitee.com/eflyz/AnelfAgent.git")])
def test_empty_install_clones_selected_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                             source: str, url: str) -> None:
    manager = Manager(Layout(tmp_path / "new project"))
    manager.settings = Settings(source=source)
    monkeypatch.setattr(manager.environment, "install_git", lambda: None)
    monkeypatch.setattr(manager.environment, "tool", lambda name: name)
    commands: list[list[str]] = []
    def clone(args: list[str], **kwargs: object) -> str:
        commands.append(args)
        folder = Path(args[-1])
        folder.mkdir()
        (folder / "uv.lock").write_text("lock", encoding="utf-8")
        (folder / "launch.py").write_text("entry", encoding="utf-8")
        (folder / ".git").mkdir()
        return ""
    monkeypatch.setattr(manager.environment, "run", clone)
    manager._checkout()
    assert commands[0][:4] == ["git", "clone", "--progress", url]
    assert (manager.layout.root / "uv.lock").read_text() == "lock"
    assert (manager.layout.root / ".git").is_dir()


def test_checkout_refuses_to_overwrite_existing_files(tmp_path: Path) -> None:
    manager = Manager(Layout(tmp_path))
    existing = tmp_path / "keep.txt"
    existing.write_text("precious", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty"):
        manager._checkout()
    assert existing.read_text() == "precious"


def test_ai_operation_lock_blocks_manager(tmp_path: Path) -> None:
    manager = Manager(Layout(tmp_path))
    ai_lock = HostLock(tmp_path / "logs/project-operation.lock")
    assert ai_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="AI"):
            manager.submit("install")
        assert not manager.status()["busy"]
    finally:
        ai_lock.release()


def test_stop_does_not_report_success_for_an_older_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = Manager(Layout(tmp_path))
    monkeypatch.setattr(manager, "_external_pid", lambda: 123)
    manager.submit("stop")
    wait_for(lambda: not manager.status()["busy"])
    assert not manager.status()["last"]["ok"]
    assert "older instance" in manager.status()["last"]["message"]


@pytest.mark.parametrize("action,code", [("status", 0), ("stop", 0), ("restart", 1)])
def test_local_cli_controls_need_no_manager_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str], action: str, code: int) -> None:
    from app.__main__ import main
    from app.client import Client
    def forbid_server(*args: object, **kwargs: object) -> None:
        pytest.fail("Local controls must not create a management server")
    monkeypatch.setattr(Client, "ensure", forbid_server)
    assert main(["--root", str(tmp_path), action]) == code
    value = json.loads(capsys.readouterr().out)
    assert (value["phase"] == "stopped") if action == "status" else value["ok"] is (code == 0)
    assert not (tmp_path / "logs/manager/connection.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Runs the actual Windows batch entry")
@pytest.mark.parametrize("action", ["stop", "restart", "update", "status"])
def test_windows_entry_dispatches_controls_before_environment_sync(tmp_path: Path, action: str) -> None:
    root = tmp_path / "中文 path"
    root.mkdir()
    source = Path(__file__).resolve().parents[2] / "start.bat"
    (root / "start.bat").write_bytes(source.read_bytes())
    (root / "app").mkdir()
    (root / "app/__main__.py").write_text('''
import json,sys
from pathlib import Path
assert sys.argv[1]=='--root' and Path(sys.argv[2]).resolve()==Path.cwd()
print(json.dumps({'action':sys.argv[3]}))
''', encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(Path(sys._base_executable).parent), str(Path(os.environ["SystemRoot"]) / "System32")])
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONPATH", None)
    result = subprocess.run([env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(root / "start.bat"), action],
                            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip())["action"] == action


@pytest.mark.skipif(os.name != "nt", reason="Runs the actual Windows restart loop")
def test_windows_start_relaunches_exit_42_without_restart_script(tmp_path: Path) -> None:
    root = tmp_path / "中文 path"
    root.mkdir()
    (root / "start.bat").write_bytes((Path(__file__).resolve().parents[2] / "start.bat").read_bytes())
    (root / "launch.py").write_text('''
import sys
from pathlib import Path
assert sys.argv[1:]==['--no-webui']
counter=Path('launch-count')
count=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(count))
sys.exit(42 if count==1 else 0)
''', encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(Path(sys._base_executable).parent), str(Path(os.environ["SystemRoot"]) / "System32")])
    env["PYTHONUTF8"] = "1"
    result = subprocess.run([env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(root / "start.bat"), "--no-webui"],
                            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "launch-count").read_text() == "2"


def test_failed_sync_does_not_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
    manager = Manager(Layout(tmp_path))
    monkeypatch.setattr(manager, "_state", lambda: ("running", 123, None))
    monkeypatch.setattr(manager.environment, "install_tools", lambda: None)
    monkeypatch.setattr(manager.environment, "tool", lambda name: name)
    monkeypatch.setattr(manager.environment, "run", lambda *args, **kwargs: "")
    monkeypatch.setattr(manager, "_stop", lambda: None)
    def fail_sync() -> None:
        raise RuntimeError("sync failed")
    monkeypatch.setattr(manager.environment, "sync", fail_sync)
    starts: list[bool] = []
    monkeypatch.setattr(manager, "_start", lambda: starts.append(True))
    manager.submit("update")
    wait_for(lambda: not manager.status()["busy"])
    assert not starts
    assert manager.status()["last"]["message"] == "sync failed"


def test_install_uses_locked_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = Layout(tmp_path)
    (tmp_path / ".python-version").write_text("3.12", encoding="utf-8")
    layout.save_settings(Settings(download_source="global"))
    environment = Environment(layout, lambda _: None)
    monkeypatch.setattr(environment, "_existing_python", lambda version: None)
    monkeypatch.setattr(environment, "install_tools", lambda: None)
    monkeypatch.setattr(environment, "tool", lambda name: name)
    commands: list[list[str]] = []
    monkeypatch.setattr(environment, "run", lambda args, **kwargs: commands.append(args))
    environment.sync()
    assert commands == [["uv", "python", "install", "3.12"],
                        ["uv", "sync", "--locked", "--inexact", "--no-dev", "--python", "3.12"],
                        ["npm", "ci", "--no-audit", "--no-fund"], ["npm", "run", "build"]]


def test_http_requires_token_and_rejects_cross_origin(tmp_path: Path) -> None:
    manager = Manager(Layout(tmp_path))
    server = ManagerServer(manager, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path: str, **headers: str) -> object:
        with opener.open(urllib.request.Request(server.base_url + path, headers=headers)) as response:
            return json.load(response)
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            request("/api/status")
        assert error.value.code == 401
        assert isinstance(request("/api/status", Authorization="Bearer " + server.token), dict)
        with pytest.raises(urllib.error.HTTPError) as error:
            request("/api/status", Authorization="Bearer " + server.token, Origin="https://evil.example")
        assert error.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as error:
            request("/api/status", Authorization="Bearer " + server.token, Host="evil.example")
        assert error.value.code == 403
        with opener.open(server.base_url) as response:
            assert b"AnelfAgent" in response.read()
        for asset, mime in (("theme.css", "text/css"), ("icon.svg", "image/svg+xml")):
            with opener.open(server.base_url + "/" + asset) as response:
                assert response.headers.get_content_type() == mime
                assert response.read() == (Path(__file__).parents[1] / "desktop" / asset).read_bytes()
        with opener.open(server.base_url + "/desktop/errors.mjs") as response:
            assert response.headers.get_content_type() == "text/javascript"
            assert response.read() == (Path(__file__).parents[1] / "desktop/errors.mjs").read_bytes()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_start_entry_and_manager_share_restart_and_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = Layout(tmp_path / "中文 path")
    frontend = layout.root / "web/frontend/dist/index.html"
    frontend.parent.mkdir(parents=True)
    frontend.touch()
    monkeypatch.setattr(Layout, "python", property(lambda self: Path(sys.executable)))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    (layout.root / "launch.py").write_text('''
import asyncio, os, sys
from pathlib import Path
from core.host_control import HostControl
from core.lifecycle import Lifecycle
counter=Path('launch-count')
count=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(count))
async def run():
    event=asyncio.Event()
    Lifecycle.set_shutdown_requester(event.set)
    def restart():
        Lifecycle.request_shutdown(True)
        return {'ok':True}
    state=Path('logs/manager')
    host=HostControl(state/f'control-{os.getpid()}.json','x'*32,connection=state/'runtime.json',restart=restart)
    await Lifecycle.start_all()
    host.start()
    await event.wait()
    await asyncio.sleep(.4)
    host.quiesce()
    await host.stop()
asyncio.run(run())
sys.exit(42 if Lifecycle.restart_requested() else 0)
''', encoding="utf-8")
    if os.name == "nt":
        (layout.root / "start.bat").write_text(f'@echo off\nrem ANELF_LAUNCH_ENTRY fixture owns its entry\n:run\n"{sys.executable}" "%~dp0launch.py"\nif %ERRORLEVEL% EQU 42 goto run\n', encoding="utf-8")
    else:
        (layout.root / "start.sh").write_text(f'#!/bin/sh\n# ANELF_LAUNCH_ENTRY fixture owns its entry\nwhile true; do "{sys.executable}" launch.py; code=$?; [ "$code" = 42 ] || exit "$code"; done\n', encoding="utf-8")
    manager = Manager(layout)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    manager.settings = Settings(web_host="127.0.0.1", web_port=port, stop_timeout=5)
    manager.open()
    try:
        manager.submit("start")
        wait_for(lambda: manager.status()["phase"] == "running" and not manager.status()["busy"])
        manager.submit("restart")
        wait_for(lambda: not manager.status()["busy"])
        assert manager.status()["last"]["ok"]
        wait_for(lambda: (layout.root / "launch-count").read_text() == "2" and manager.status()["running"])
        assert manager.status()["running"]
        pid = manager.status()["pid"]
        assert pid_alive(pid)
        manager.submit("stop")
        wait_for(lambda: not manager.status()["busy"])
        assert manager.status()["last"]["ok"]
        assert not pid_alive(pid)
        assert (layout.root / "launch-count").read_text() == "2"
    finally:
        manager.close()


@pytest.mark.parametrize("timezone,expected", [("Asia/Shanghai", "gitee"), ("Asia/Urumqi", "gitee"),
    ("China Standard Time", "gitee"), ("中国标准时间", "gitee"), ("America/New_York", "github"),
    ("Asia/Singapore", "github"), ("Europe/London", "github")])
def test_auto_repository_follows_timezone(timezone: str, expected: str) -> None:
    assert resolve_source("auto", timezone) == expected
    assert resolve_source("github", timezone) == "github"
    assert resolve_source("gitee", timezone) == "gitee"


def test_install_streams_output_and_cancel_reaps_owned_process_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """真实安装子进程及孙进程被取消，应用不启动，队列可重新接受操作。"""
    manager = Manager(Layout(tmp_path))
    child_pid = tmp_path / "child.pid"
    def install(update: bool) -> None:
        manager.operation.stage("dependencies")
        manager.environment.run([sys.executable, "-u", "-c",
            "import subprocess,sys,time,pathlib; "
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
            f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid)); "
            "print('download 10/100',flush=True);time.sleep(60)"])
    monkeypatch.setattr(manager, "_install", install)
    manager.submit("install")
    wait_for(lambda: manager.status()["operation"].get("detail", "").strip() == "download 10/100")
    assert manager.status()["busy"]
    pid = int(child_pid.read_text())
    manager.cancel()
    wait_for(lambda: not manager.status()["busy"])
    assert manager.status()["phase"] == "cancelled"
    assert manager.status()["last"]["cancelled"]
    wait_for(lambda: not pid_alive(pid))
    manager.submit("stop")
    wait_for(lambda: not manager.status()["busy"])
    assert manager.status()["last"]["ok"]


def test_cancel_during_checkout_keeps_user_data_and_allows_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.operation import Cancelled
    manager = Manager(Layout(tmp_path))
    manager.operation.begin("install")
    monkeypatch.setattr(manager.environment, "install_git", lambda: None)
    monkeypatch.setattr(manager.environment, "tool", lambda name: name)
    def clone(args: list[str], **kwargs: object) -> str:
        target = Path(args[-1]); target.mkdir()
        (target / "uv.lock").touch(); (target / "launch.py").touch()
        manager.operation.cancel()
        return ""
    monkeypatch.setattr(manager.environment, "run", clone)
    with pytest.raises(Cancelled):
        manager._checkout()
    assert [item.name for item in tmp_path.iterdir()] == ["logs"]
    assert not list(manager.layout.state.glob("tmp*"))


@pytest.mark.parametrize("source", ["china", "global"])
def test_download_source_covers_python_packages_and_node_without_changing_system(tmp_path: Path, source: str,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    layout = Layout(tmp_path); layout.save_settings(Settings(download_source=source))
    (tmp_path / ".python-version").write_text("3.12", encoding="utf-8")
    environment = Environment(layout, lambda _: None)
    monkeypatch.setattr(environment, "install_tools", lambda: None)
    monkeypatch.setattr(environment, "tool", lambda name: name)
    monkeypatch.setattr(environment, "_existing_python", lambda version: "existing-python")
    commands: list[list[str]] = []
    monkeypatch.setattr(environment, "run", lambda args, **kwargs: commands.append(args))
    environment.sync()
    assert not any(args[1:3] == ["python", "install"] for args in commands)
    env = environment.environ()
    assert str(layout.tools) in env["UV_CACHE_DIR"] and env["UV_PYTHON_INSTALL_REGISTRY"] == "0"
    if source == "china":
        assert "npmmirror.com" in env["UV_PYTHON_INSTALL_MIRROR"]
        assert env["npm_config_registry"] == "https://registry.npmmirror.com"
        assert commands[0][1:5] == ["export", "--locked", "--no-dev", "--no-emit-project"]
        install = next(args for args in commands if args[1:3] == ["pip", "install"])
        assert "--require-hashes" in install and "--no-deps" in install and "--no-config" in install
        assert "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple" in install
    else:
        assert commands[0] == ["uv", "sync", "--locked", "--inexact", "--no-dev", "--python", "existing-python"]
        assert env["npm_config_registry"] == "https://registry.npmjs.org"


def test_interrupted_install_is_not_reported_as_ready(tmp_path: Path) -> None:
    from core.host_protocol import write_json
    layout = Layout(tmp_path)
    layout.python.parent.mkdir(parents=True); layout.python.touch()
    frontend = tmp_path / "web/frontend/dist/index.html"; frontend.parent.mkdir(parents=True); frontend.touch()
    write_json(layout.state / "installation.json", {"ready": False})
    write_json(layout.state / "operation.json", {"status": "running", "action": "install"})
    manager = Manager(layout)
    assert manager.status()["phase"] == "interrupted"
    assert not manager.status()["ready"]
