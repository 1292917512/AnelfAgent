"""真实本地 HTTP 与进程归属回归；不启动用户项目。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.manager import Manager
from app.processes import background_options
from app.readiness import web_ready
from app.settings import Layout, Settings
from core.host_protocol import write_json


@pytest.fixture
def web_project(tmp_path: Path) -> Iterator[tuple[Path, int, int]]:
    script = tmp_path / "launch.py"
    script.write_text('''import faulthandler,json,os,socket
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
faulthandler.dump_traceback_later(5)
# The fixture serves a numeric loopback address and needs no reverse DNS.
socket.getfqdn=lambda host:host
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  mode=Path('mode').read_text() if Path('mode').exists() else 'ok'
  self.send_response(503 if mode=='fail' else 302 if mode=='redirect' else 200)
  if mode=='redirect': self.send_header('Location','http://example.invalid/health')
  self.end_headers();self.wfile.write(json.dumps({'status':mode}).encode())
 def log_message(self,*args): pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
Path('listener.tmp').write_text(json.dumps({'pid':os.getpid(),'port':server.server_port}))
Path('listener.tmp').replace('listener.json')
faulthandler.cancel_dump_traceback_later()
server.serve_forever()
''', encoding="utf-8")
    log = tmp_path / "listener.log"
    with log.open("w", encoding="utf-8") as output:
        child = subprocess.Popen([sys._base_executable, str(script)], cwd=tmp_path,
                                 stdout=output, stderr=output, **background_options())
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "listener.json").exists():
            assert child.poll() is None, log.read_text("utf-8", errors="replace")
            assert time.monotonic() < deadline, log.read_text("utf-8", errors="replace")
            time.sleep(.02)
        listener = json.loads((tmp_path / "listener.json").read_text())
        assert listener["pid"] == child.pid
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs/anelf.pid").write_text(str(child.pid))
        yield tmp_path, child.pid, listener["port"]
    finally:
        child.terminate()
        child.wait(timeout=10)


def manager_at(project: tuple[Path, int, int]) -> Manager:
    root, _pid, port = project
    manager = Manager(Layout(root))
    manager.settings = Settings(web_host="127.0.0.1", web_port=port)
    return manager


def record(manager: Manager, pid: int, phase: str = "starting") -> dict[str, object]:
    return {"pid": pid, "phase": phase, "token": "x" * 32,
            "control_file": str(manager.layout.state / "control.json"),
            "web_url": f"http://127.0.0.1:{manager.settings.web_port}/webui/"}


def test_stale_runtime_cannot_hide_a_healthy_project(web_project: tuple[Path, int, int]) -> None:
    manager = manager_at(web_project)
    write_json(manager.layout.state / "runtime.json", record(manager, 2147483647))
    state = manager.status()
    assert (state["phase"], state["pid"], state["running"], state["web_ready"]) == ("running", web_project[1], True, True)
    assert state["controllable"] is False
    assert state["external_pid"] == web_project[1]


@pytest.mark.parametrize("phase", ["starting", "running", "stopping", "restarting"])
def test_health_completes_startup_but_never_overrides_shutdown(web_project: tuple[Path, int, int], phase: str) -> None:
    manager = manager_at(web_project)
    write_json(manager.layout.state / "runtime.json", record(manager, web_project[1], phase))
    state = manager.status()
    assert state["phase"] == ("running" if phase in {"starting", "running"} else phase)
    assert state["web_ready"] == (phase in {"starting", "running"})
    assert state["controllable"] is True


def test_pid_record_and_cached_control_survive_a_corrupt_shared_pointer(web_project: tuple[Path, int, int]) -> None:
    manager = manager_at(web_project)
    value = record(manager, web_project[1], "running")
    scoped = manager.layout.state / f"runtime-{web_project[1]}.json"
    write_json(scoped, value)
    write_json(manager.layout.state / "runtime.json", record(manager, os.getpid()))
    assert manager.status()["controllable"] is True
    assert manager._runtime() == value
    scoped.unlink()
    assert manager.status()["controllable"] is True


@pytest.mark.parametrize("mode", ["fail", "redirect", "other"])
def test_listening_port_without_healthy_response_is_not_ready(web_project: tuple[Path, int, int], mode: str) -> None:
    root, pid, port = web_project
    (root / "mode").write_text(mode)
    assert not web_ready(root, pid, f"http://127.0.0.1:{port}/webui/")
    manager = manager_at(web_project)
    write_json(manager.layout.state / "runtime.json", record(manager, pid, "running"))
    assert manager.status()["phase"] == "web_unavailable"
    assert not manager.status()["web_ready"]


def test_port_owned_by_another_process_or_project_is_rejected(web_project: tuple[Path, int, int]) -> None:
    root, pid, port = web_project
    assert not web_ready(root / "other-project", pid, f"http://127.0.0.1:{port}/")
    with ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as other:
        assert not web_ready(root, pid, f"http://127.0.0.1:{other.server_port}/")
    assert not web_ready(root, pid, "http://example.invalid:8092/")


def test_health_failure_does_not_remain_cached_forever(web_project: tuple[Path, int, int]) -> None:
    root, pid, _port = web_project
    manager = manager_at(web_project)
    write_json(manager.layout.state / "runtime.json", record(manager, pid, "running"))
    (root / "mode").write_text("fail")
    assert not manager.status()["web_ready"]
    (root / "mode").unlink()
    time.sleep(1.05)
    assert manager.status()["web_ready"]
