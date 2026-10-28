"""真实隐藏终端、流式包进度、取消及安装完成边界的回归测试。"""

from __future__ import annotations

import io
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.environment import Environment
from app.manager import Manager, pid_alive
from app.operation import Cancelled, Operation
from app.settings import Layout, Settings
from app.terminal import available
from app.tests.test_manager import wait_for
from app.uv_progress import UvProgress


def test_uv_output_chunks_and_terminal_frames(tmp_path: Path) -> None:
    operation = Operation(); operation.begin("install"); operation.stage("dependencies")
    report = UvProgress(operation)
    for chunk in ["Resolved 20 pack", "ages in 1ms\r\nDownlo", "ading torch (100MiB)\r",
                  "\nDownloaded torch\nPrepared 2 packages in 1s\n"]:
        report.feed(chunk)
    value = operation.snapshot()["progress"]
    assert value["resolved"] == 20 and value["prepared"] == 2 and value["downloads"] == 1
    assert not value["active"]
    pytest.importorskip("pyte")
    report.terminal("\x1b[2J\x1b[Htorch -------- 25.0 MiB/100.0 MiB\r\n")
    assert operation.snapshot()["progress"]["packages"] == [
        {"name": "torch", "received": 26214400, "total": 104857600}]
    report.terminal("\x1b[H\x1b[2Ktorch -------- 50.0 MiB/100.0 MiB\r\n")
    assert operation.snapshot()["progress"]["packages"][0]["received"] == 52428800
    operation.stage("frontend_build")
    assert operation.snapshot()["progress"] is None


@pytest.mark.skipif(not available(), reason="APP terminal adapters are optional for source CLI")
def test_hidden_terminal_streams_package_progress_and_cancels_tree(tmp_path: Path) -> None:
    operation = Operation(); operation.begin("install"); operation.stage("dependencies")
    environment = Environment(Layout(tmp_path), lambda _: None, operation)
    child_pid = tmp_path / "child.pid"
    script = tmp_path / "simulate.py"
    script.write_text("""
import os, sys, pathlib, subprocess, time
if os.name == 'nt':
    import ctypes
    assert not ctypes.windll.user32.IsWindowVisible(ctypes.windll.kernel32.GetConsoleWindow())
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
print('torch -------- 25.0 MiB/100.0 MiB', flush=True)
time.sleep(60)
""", encoding="utf-8")
    errors: list[BaseException] = []
    def work() -> None:
        try:
            environment.run([sys.executable, str(script), str(child_pid)], report_uv=True)
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=work)
    thread.start()
    try:
        wait_for(lambda: bool(operation.snapshot().get("progress", {}).get("packages")))
        assert operation.snapshot()["progress"]["packages"][0]["received"] == 26214400
        pid = int(child_pid.read_text())
        assert pid_alive(pid)
    finally:
        operation.cancel(); thread.join(10)
    assert not thread.is_alive() and len(errors) == 1 and isinstance(errors[0], Cancelled)
    wait_for(lambda: not pid_alive(pid))


def test_partial_install_cannot_start_even_with_an_interpreter(tmp_path: Path) -> None:
    from core.host_protocol import write_json
    manager = Manager(Layout(tmp_path))
    manager.layout.python.parent.mkdir(parents=True); manager.layout.python.touch()
    frontend = tmp_path / "web/frontend/dist/index.html"; frontend.parent.mkdir(parents=True); frontend.touch()
    write_json(manager.layout.state / "installation.json", {"ready": False})
    with pytest.raises(RuntimeError, match="Install"):
        manager._start()


def test_frontend_failure_does_not_finish_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = Manager(Layout(tmp_path))
    (tmp_path / "pyproject.toml").touch()
    def sync() -> None:
        manager.operation.stage("frontend_build")
        raise RuntimeError("frontend build failed")
    monkeypatch.setattr(manager.environment, "sync", sync)
    manager.submit("install")
    wait_for(lambda: not manager.status()["busy"])
    assert not manager.ready and not manager.status()["last"]["ok"]
    assert manager.status()["operation"]["stage"] == "frontend_build"


def test_project_environment_does_not_inherit_foreign_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "other"))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "other"))
    layout = Layout(tmp_path / "project"); layout.save_settings(Settings(download_source="china"))
    env = Environment(layout, lambda _: None).environ()
    assert "VIRTUAL_ENV" not in env
    assert env["UV_PROJECT_ENVIRONMENT"] == str(layout.root / ".venv")
    assert "npmmirror.com" in env["UV_PYTHON_INSTALL_MIRROR"]


def test_network_checks_only_selected_sources_and_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.network import inspect_network
    urls: list[str] = []
    def open_url(request: urllib.request.Request, timeout: int) -> io.BytesIO:
        url = request.full_url
        urls.append(url)
        assert timeout == 5
        raise urllib.error.URLError("test timeout")
    monkeypatch.setattr("app.network.urllib.request.urlopen", open_url)
    result = inspect_network(Settings(download_source="china"))
    assert len(result["results"]) == 4 and all(not item["ok"] for item in result["results"])
    assert result["source"] == "china"
    assert any("tuna.tsinghua.edu.cn" in url for url in urls)
    assert all("npmmirror.com" in url or "tuna.tsinghua.edu.cn" in url for url in urls)


@pytest.mark.skipif(not available() or not shutil.which("uv"), reason="Requires uv and APP terminal adapters")
def test_real_uv_download_reports_bytes_without_an_external_network(tmp_path: Path) -> None:
    """向临时 venv 安装本地限速 wheel，验证真实 uv 终端输出，不安装项目或访问外网。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as wheel:
        metadata = "anelf_progress_fixture-1.0.dist-info/"
        wheel.writestr(metadata + "METADATA", "Metadata-Version: 2.1\nName: anelf-progress-fixture\nVersion: 1.0\n")
        wheel.writestr(metadata + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(metadata + "RECORD", "")
        wheel.writestr("anelf_progress_fixture/data.bin", os.urandom(3 * 1024 * 1024))
    payload = buffer.getvalue()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_HEAD(self) -> None:
            self.send_response(200); self.send_header("Content-Length", str(len(payload))); self.end_headers()

        def do_GET(self) -> None:
            self.send_response(200); self.send_header("Content-Length", str(len(payload))); self.end_headers()
            try:
                for offset in range(0, len(payload), 32768):
                    self.wfile.write(payload[offset:offset+32768]); self.wfile.flush(); time.sleep(.025)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    serving = threading.Thread(target=server.serve_forever); serving.start()
    operation = Operation(); operation.begin("install"); operation.stage("dependencies")
    environment = Environment(Layout(tmp_path), lambda _: None, operation)
    uv = shutil.which("uv"); assert uv
    errors: list[BaseException] = []
    snapshots: list[dict[str, object]] = []
    def install() -> None:
        try:
            environment.run([uv, "venv", "--python", sys.executable, str(tmp_path / ".venv")])
            environment.run([uv, "pip", "install", "--python", str(environment.layout.python), "--no-cache", "--no-deps",
                             f"http://127.0.0.1:{server.server_port}/anelf_progress_fixture-1.0-py3-none-any.whl"], report_uv=True)
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=install); worker.start()
    try:
        deadline = time.monotonic() + 20
        while worker.is_alive() and time.monotonic() < deadline:
            packages = (operation.snapshot().get("progress") or {}).get("packages", [])
            snapshots.extend(packages)
            time.sleep(.05)
    finally:
        if worker.is_alive(): operation.cancel()
        worker.join(10); server.shutdown(); server.server_close(); serving.join(5)
    assert not worker.is_alive() and not errors
    assert any(0 < item["received"] < item["total"] for item in snapshots), snapshots
    assert any(item["name"] == "anelf-progress-fixture" for item in snapshots)


@pytest.mark.skipif(not shutil.which("uv"), reason="Requires uv")
def test_real_start_entry_reuses_verified_venv_and_handles_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """实际 start 脚本在空格/中文目录运行轻量宿主，不重新同步依赖或启动真实 Agent。"""
    root = tmp_path / "中文 app"
    layout = Layout(root)
    environment = Environment(layout, lambda _: None)
    uv = shutil.which("uv"); assert uv
    environment.run([uv, "venv", "--python", sys.executable, str(root / ".venv")])
    frontend = root / "web/frontend/dist/index.html"; frontend.parent.mkdir(parents=True); frontend.touch()
    (root / "launch.py").write_text("""
from pathlib import Path
import sys
assert Path(sys.prefix).name == '.venv'
counter = Path('count')
count = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(count))
sys.exit(42 if count == 1 else 0)
""", encoding="utf-8")
    script = "start.bat" if os.name == "nt" else "start.sh"
    shutil.copy2(Path(__file__).resolve().parents[2] / script, root / script)
    monkeypatch.setenv("ANELF_ENV_READY", "1")
    command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(root / script)] if os.name == "nt" else ["bash", str(root / script)]
    output = environment.run(command, timeout=20)
    assert (root / "count").read_text() == "2"
    assert "uv sync" not in output and "npm" not in output
