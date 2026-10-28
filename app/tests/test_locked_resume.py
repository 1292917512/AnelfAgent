"""锁定镜像安装不受项目覆盖规则干扰，并保留已有虚拟环境和用户包。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

from app.environment import Environment
from app.manager import Manager
from app.operation import Cancelled
from app.settings import Layout, Settings, download_profile


def wheel(directory: Path, name: str, version: str, payload_bytes: int = 0) -> None:
    """生成本地无依赖测试 wheel，测试不访问外部源。"""
    target = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(target, "w") as archive:
        metadata = f"{name}-{version}.dist-info/"
        archive.writestr(metadata + "METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        archive.writestr(metadata + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(metadata + "RECORD", "")
        if payload_bytes:
            archive.writestr(name + "/data.bin", os.urandom(payload_bytes))
    index = directory / name.replace("_", "-") / "index.html"
    index.parent.mkdir(exist_ok=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    with index.open("a", encoding="utf-8") as stream:
        stream.write(f'<a href="../{target.name}#sha256={digest}">{target.name}</a>\n')


@pytest.fixture
def local_index(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    directory = tmp_path / "index"; directory.mkdir()
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self) -> None:
            pause = directory / "pause-download"
            if self.path.endswith("anelf_pending-1.0-py3-none-any.whl") and pause.exists():
                payload = Path(self.translate_path(self.path)).read_bytes()
                self.send_response(200); self.send_header("Content-Length", str(len(payload))); self.end_headers()
                try:
                    self.wfile.write(payload[:32768]); self.wfile.flush()
                    (directory / "download-started").touch()
                    while pause.exists():
                        time.sleep(.03)
                    self.wfile.write(payload[32768:])
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
            else:
                super().do_GET()
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(directory)))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever); thread.start()
    try:
        yield directory, f"http://127.0.0.1:{server.server_port}"
    finally:
        (directory / "pause-download").unlink(missing_ok=True)
        server.shutdown(); server.server_close(); thread.join(5)


@pytest.mark.skipif(not shutil.which("uv"), reason="Requires uv")
@pytest.mark.parametrize("switch_mirror", [False, True])
def test_mirror_install_keeps_locked_hashes_and_resumes_existing_venv(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch,
                                                                    local_index: tuple[Path, str],
                                                                    switch_mirror: bool) -> None:
    packages, index_url = local_index
    layout = Layout(tmp_path / "project")
    layout.save_settings(Settings(download_source="china"))
    (layout.root / ".python-version").write_text(f"{sys.version_info.major}.{sys.version_info.minor}")
    (layout.root / "pyproject.toml").write_text('''
[project]
name = "resume-test"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = ["anelf-hash-fixture>=1.0"]
[tool.uv]
override-dependencies = ["anelf-hash-fixture>=1.0"]
''' + f'\n[[tool.uv.index]]\nurl = "{index_url}"\ndefault = true\n', encoding="utf-8")
    wheel(packages, "anelf_hash_fixture", "1.0")
    wheel(packages, "user_extra", "1.0")
    monkeypatch.delenv("UV_NO_INDEX", raising=False)
    monkeypatch.delenv("UV_FIND_LINKS", raising=False)
    profile = download_profile(layout.load_settings())
    monkeypatch.setattr("app.environment.download_profile", lambda _: {**profile, "pypi": index_url})
    environment = Environment(layout, lambda _: None)
    uv = shutil.which("uv"); assert uv
    environment.run([uv, "lock", "--python", sys.executable])
    lock_file = layout.root / "uv.lock"
    original_lock = lock_file.read_bytes()
    original_project = (layout.root / "pyproject.toml").read_bytes()
    # A newer release appears after locking; the broad project override must not select it.
    wheel(packages, "anelf_hash_fixture", "2.0")
    environment.run([uv, "venv", "--python", sys.executable, str(layout.root / ".venv")])
    environment.run([uv, "pip", "install", "--no-config", "--default-index", index_url,
                     "--python", str(layout.python), "user-extra==1.0"])
    venv_config = layout.root / ".venv/pyvenv.cfg"
    original_config = venv_config.read_bytes()
    run = environment.run
    attempts = 0
    commands: list[list[str]] = []

    def install(args: list[str], **kwargs: Any) -> str:
        nonlocal attempts
        commands.append(args)
        if args[0] == "npm":
            if args[1] == "run":
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("simulated frontend interruption")
            return ""
        return run(args, **kwargs)

    monkeypatch.setattr(environment, "install_tools", lambda: None)
    monkeypatch.setattr(environment, "tool", lambda name: uv if name == "uv" else name)
    monkeypatch.setattr(environment, "_existing_python", lambda _: str(layout.python))
    monkeypatch.setattr(environment, "run", install)
    with pytest.raises(RuntimeError, match="simulated frontend interruption"):
        environment.sync()
    # Resumption is discovered from the project, including after the APP is restarted/upgraded.
    assert Manager(layout).status()["resumable"]
    installed_metadata = next((layout.root / ".venv").rglob("anelf_hash_fixture-1.0.dist-info/METADATA"))
    installed_at = installed_metadata.stat().st_mtime_ns
    if switch_mirror:
        shutil.copytree(packages, packages / "alternate", ignore=shutil.ignore_patterns("alternate"))
        profile["pypi"] = index_url + "/alternate"
        monkeypatch.setattr("app.environment.download_profile", lambda _: profile)
    environment.sync()
    versions = json.loads(run([str(layout.python), "-c",
        "import importlib.metadata as m,json;print(json.dumps([m.version('anelf-hash-fixture'),m.version('user-extra')]))"]))
    assert versions == ["1.0", "1.0"]
    assert installed_metadata.stat().st_mtime_ns == installed_at
    assert venv_config.read_bytes() == original_config
    assert lock_file.read_bytes() == original_lock
    assert (layout.root / "pyproject.toml").read_bytes() == original_project
    assert not any(args[1:3] == ["python", "install"] or args[1] == "venv" for args in commands)


@pytest.mark.skipif(not shutil.which("uv"), reason="Requires uv")
def test_cancel_real_download_then_switch_mirror_only_installs_missing_package(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_index: tuple[Path, str]) -> None:
    packages, index_url = local_index
    wheel(packages, "anelf_existing", "1.0")
    wheel(packages, "anelf_pending", "1.0", 3 * 1024 * 1024)
    layout = Layout(tmp_path / "project")
    layout.save_settings(Settings(download_source="china", pypi_source="tsinghua"))
    (layout.root / ".python-version").write_text(f"{sys.version_info.major}.{sys.version_info.minor}")
    (layout.root / "pyproject.toml").write_text(
        '[project]\nname="resume-download"\nversion="0.0.0"\nrequires-python=">=3.11"\n'
        'dependencies=["anelf-existing==1.0","anelf-pending==1.0"]\n'
        f'[[tool.uv.index]]\nurl="{index_url}"\ndefault=true\n', "utf-8")
    profile = {**download_profile(layout.load_settings()), "pypi": index_url}
    monkeypatch.setattr("app.environment.download_profile", lambda _: profile)
    uv = shutil.which("uv"); assert uv
    environment = Environment(layout, lambda _: None)
    environment.run([uv, "lock", "--python", sys.executable, "--cache-dir", str(tmp_path / "lock-cache")])
    environment.run([uv, "venv", "--python", sys.executable, str(layout.root / ".venv")])
    environment.run([uv, "pip", "install", "--no-config", "--python", str(layout.python),
                     "--default-index", index_url, "anelf-existing==1.0"], isolated_index=True)
    metadata = next((layout.root / ".venv").rglob("anelf_existing-1.0.dist-info/METADATA"))
    installed_at = metadata.stat().st_mtime_ns
    originals = {name: (layout.root / name).read_bytes() for name in ("pyproject.toml", "uv.lock", ".python-version", ".venv/pyvenv.cfg")}
    run = environment.run
    monkeypatch.setattr(environment, "install_tools", lambda: None)
    monkeypatch.setattr(environment, "tool", lambda name: uv if name == "uv" else name)
    monkeypatch.setattr(environment, "_existing_python", lambda _: str(layout.python))
    monkeypatch.setattr(environment, "run", lambda args, **kwargs: "" if args[0] == "npm" else run(args, **kwargs))
    errors: list[BaseException] = []
    def install() -> None:
        try:
            environment.sync()
        except BaseException as exc:
            errors.append(exc)
    pause = packages / "pause-download"; pause.touch()
    environment.operation.begin("install")
    worker = threading.Thread(target=install); worker.start()
    try:
        deadline = time.monotonic() + 20
        while not (packages / "download-started").exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        assert (packages / "download-started").exists(), errors
    finally:
        environment.operation.cancel(); worker.join(10); pause.unlink(missing_ok=True)
    assert not worker.is_alive() and len(errors) == 1 and isinstance(errors[0], Cancelled)
    assert metadata.stat().st_mtime_ns == installed_at and layout.python.is_file()
    shutil.copytree(packages, packages / "alternate", ignore=shutil.ignore_patterns("alternate"))
    profile["pypi"] = index_url + "/alternate"
    layout.save_settings(Settings(download_source="china", pypi_source="huawei"))
    environment.operation.begin("install")
    environment.sync()
    assert environment.operation.snapshot()["existing_packages"] == 1
    assert metadata.stat().st_mtime_ns == installed_at
    assert next((layout.root / ".venv").rglob("anelf_pending-1.0.dist-info/METADATA")).is_file()
    assert all((layout.root / name).read_bytes() == content for name, content in originals.items())
