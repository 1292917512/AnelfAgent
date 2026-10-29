"""真实启动脚本 + 旧项目组合根的控制回归，不启动实际 Agent 或桌面 APP。"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import pytest

from app.manager import Manager
from app.processes import background_options
from app.settings import Layout, Settings


def wait(predicate: Callable[[], bool], manager: Manager) -> None:
    deadline = time.monotonic() + 30
    while not predicate():
        if time.monotonic() > deadline:
            log = manager.layout.state / "application.log"
            pytest.fail(str(manager.status()) + "\n" + (log.read_text("utf-8", errors="replace") if log.exists() else ""))
        time.sleep(.1)


@pytest.mark.skipif(not shutil.which("uv"), reason="Requires uv to create an isolated fixture environment")
def test_old_checkout_gets_control_without_source_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "中文 project"
    layout = Layout(root)
    repo = Path(__file__).resolve().parents[2]
    subprocess.run([str(shutil.which("uv")), "venv", "--offline", "--no-config", "--no-python-downloads",
                    "--cache-dir", str(tmp_path / "cache"), "--python", sys._base_executable, str(root / ".venv")],
                   check=True, capture_output=True, **background_options())
    # uv --with may put dependencies in an overlay rather than the interpreter's purelib.
    dependencies = [path for path in sys.path if Path(path).name in {"site-packages", "dist-packages"}]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(repo), *dependencies]))
    files = {
        "core/__init__.py": f"__path__.append({str(repo / 'core')!r})\n",
        "web/__init__.py": "",
        "web/server.py": "def _load_server_config(): return ('0.0.0.0', 8092)\n",
        "entities/__init__.py": "",
        "entities/devops/__init__.py": "",
        "entities/devops/service.py": "from core.lifecycle import Lifecycle\ndef request_restart(source):\n Lifecycle.request_shutdown(True)\n return {'ok':True}\n",
        "web/frontend/dist/index.html": "ready",
        "start.bat": "@echo off\nexit /b 91\n",
        "start.sh": "#!/bin/sh\nexit 91\n",
        "launch.py": '''import argparse, asyncio, json, sys
from pathlib import Path
from core.application import Application
from core.instance_guard import acquire_instance, release_instance
from core.lifecycle import Lifecycle
def create_application(args):
 app=Application()
 @app.startup.node(skip_on_error=False)
 async def acquire_single_instance():
  acquire_instance(Path('logs/anelf.pid'),Path.cwd())
 @app.startup.node(skip_on_error=False)
 async def run_bootstrap():
  await asyncio.sleep(.7)
 @app.startup.node(skip_on_error=False)
 async def register_web_service():
  from web import server
  from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
  import socket, threading
  # The fixture serves a numeric local address and needs no reverse DNS.
  socket.getfqdn=lambda host:host
  class Handler(BaseHTTPRequestHandler):
   def do_GET(self):
    self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ok"}')
   def log_message(self,*args): pass
  web=None
  def started():
   nonlocal web
   Path('server.json').write_text(json.dumps(server._load_server_config()))
   web=ThreadingHTTPServer(server._load_server_config(),Handler)
   threading.Thread(target=web.serve_forever,daemon=True).start()
  def stop():
   if web: web.shutdown();web.server_close()
  Lifecycle.register('fixture_web',None,on_start=started,cleanup=stop)
 app.on_post_shutdown('release',lambda:release_instance(Path('logs/anelf.pid')))
 return app
def main():
 counter=Path('launch-count')
 counter.write_text(str(int(counter.read_text())+1 if counter.exists() else 1))
 asyncio.run(create_application(argparse.Namespace(no_webui=False)).run())
 sys.exit(42 if Lifecycle.restart_requested() else 0)
if __name__=='__main__': main()
''',
    }
    for name, content in files.items():
        file = root / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_text(content, "utf-8")
    manager = Manager(layout)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    manager.settings = Settings(web_port=port, stop_timeout=15)
    observed_starting = False
    try:
        manager.submit("start")
        def ready() -> bool:
            nonlocal observed_starting
            state = manager.status()
            if (root / "logs/anelf.pid").exists():
                assert state["external_pid"] == 0
                observed_starting |= state["phase"] in {"start", "starting"}
            return not state["busy"] and state["phase"] == "running"
        wait(ready, manager)
        assert observed_starting
        assert json.loads((root / "server.json").read_text()) == ["0.0.0.0", port]
        first_pid = manager.status()["pid"]
        manager.submit("restart")
        wait(lambda: not manager.status()["busy"] and manager.status()["phase"] == "running"
             and manager.status()["pid"] != first_pid, manager)
        assert (root / "launch-count").read_text() == "2"
        manager.submit("stop")
        wait(lambda: not manager.status()["busy"], manager)
        assert manager.status()["last"]["ok"]
        assert manager.status()["phase"] == "stopped"
        assert not (root / "logs/anelf.pid").exists()
        for name in ("launch.py", "start.bat", "start.sh"):
            assert (root / name).read_text("utf-8") == files[name]
    finally:
        manager.close()
