"""安装管理与运行观测；应用守护只由项目 start 入口负责。"""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.environment import Environment
from app.operation import Cancelled, Operation
from app.processes import background_options
from app.readiness import web_ready
from app.repository import RepositoryInfo, change_origin, describe, origin, set_source
from app.settings import REPOSITORY_SOURCES, Layout, Settings, download_profile, probe_port, resolve_source
from app.storage import inspect_storage, require_storage
from core.host_protocol import HostLock, write_json


def pid_alive(pid: int) -> bool:
    """检测 PID 存活；Windows 不使用会终止进程的 os.kill。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


class Manager:
    """APP 和网页共用的操作队列；通过宿主控制面协调所有启动入口。"""

    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.settings = layout.load_settings()
        self._mutex = threading.RLock()
        self._operation = threading.Lock()
        self._project = HostLock(layout.root / "logs" / "project-operation.lock")
        self._process: subprocess.Popen[bytes] | None = None
        self._job: threading.Thread | None = None
        self._events: deque[str] = deque(maxlen=150)
        self._phase = "stopped"
        self._last: dict[str, Any] | None = None
        self._known_runtime: dict[str, Any] | None = None
        self._web_probe: tuple[int, str, float, bool] = (0, "", 0.0, False)
        try:
            self._backend_id = str(json.loads((Path(__file__).resolve().parent.parent / "manifest.json").read_text("utf-8"))["sha256"])
        except (OSError, ValueError, KeyError):
            self._backend_id = ""
        self.operation = Operation()
        self.environment = Environment(layout, self.emit, self.operation)
        self._repository = describe("")
        try:
            self.inspect_repository()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            logging.getLogger(__name__).debug("Repository inspection unavailable: %s", exc)
        try:
            previous = json.loads((layout.state / "operation.json").read_text("utf-8"))
            if previous.get("status") == "running":
                self._last = {"ok": False, "action": previous.get("action"), "message": "The previous installation was interrupted. Review the log and retry."}
                self._phase = "interrupted"
        except (OSError, ValueError):
            logging.getLogger(__name__).debug("No previous installation operation to recover")

    def emit(self, message: str) -> None:
        if message:
            with self._mutex:
                self._events.append(f"{datetime.now().strftime('%H:%M:%S')} {message[-4000:]}")

    def open(self) -> None:
        if self.settings.auto_start:
            self.submit("start")

    def close(self, *, detach: bool = False) -> None:
        """只停止本管理器启动的入口；独立 start 启动的应用继续运行。"""
        if self._job is not None:
            self._job.join()
        if not detach and self._process is not None and self._process.poll() is None:
            self._stop()

    def _project_pid(self) -> int:
        try:
            pid = int((self.layout.root / "logs/anelf.pid").read_text("utf-8").strip())
            return pid if pid_alive(pid) else 0
        except (OSError, ValueError):
            return 0

    def _runtime(self) -> dict[str, Any] | None:
        project_pid = self._project_pid()
        stale: dict[str, Any] | None = None
        paths = [self.layout.state / f"runtime-{project_pid}.json"] if project_pid else []
        paths.append(self.layout.state / "runtime.json")
        for path in paths:
            try:
                value = json.loads(path.read_text("utf-8"))
                if (isinstance(value, dict) and type(value.get("pid")) is int and value["pid"] > 0
                        and (not project_pid or value["pid"] == project_pid)
                        and isinstance(value.get("token"), str) and len(value["token"]) >= 16
                        and isinstance(value.get("control_file"), str)
                        and Path(value["control_file"]).resolve().parent == self.layout.state.resolve()):
                    if pid_alive(value["pid"]):
                        self._known_runtime = value
                        return value
                    stale = value
            except (OSError, ValueError):
                continue
        if self._known_runtime and pid_alive(self._known_runtime["pid"]) and (not project_pid or self._known_runtime["pid"] == project_pid):
            return self._known_runtime
        return stale

    def _web_ready(self, pid: int) -> bool:
        url = self.web_url
        previous_pid, previous_url, checked, ready = self._web_probe
        now = time.monotonic()
        if (pid, url) != (previous_pid, previous_url) or now - checked >= 1:
            ready = web_ready(self.layout.root, pid, url)
            self._web_probe = (pid, url, now, ready)
        return ready

    def _external_pid(self) -> int:
        try:
            pid = int((self.layout.root / "logs/anelf.pid").read_text("utf-8").strip())
            runtime = self._runtime()
            if self._process is not None and self._process.poll() is None:
                return 0  # 自己启动的入口仍在初始化时，PID 早于控制通道出现。
            return pid if pid_alive(pid) and (runtime is None or runtime["pid"] != pid) else 0
        except (OSError, ValueError):
            return 0

    def _state(self) -> tuple[str, int | None, dict[str, Any] | None]:
        runtime = self._runtime()
        if runtime is not None:
            if pid_alive(runtime["pid"]):
                phase = str(runtime.get("phase", "starting"))
                if phase in {"starting", "running"} and runtime.get("web_url"):
                    phase = "running" if self._web_ready(runtime["pid"]) else "starting" if phase == "starting" else "web_unavailable"
                return "stopping" if phase == "stopped" else phase, runtime["pid"], runtime
            if runtime.get("phase") == "restarting" and time.time() - float(runtime.get("updated_at", 0)) < 30:
                return "restarting", None, runtime
        project_pid = self._project_pid()
        if project_pid and self._web_ready(project_pid):
            return "running", project_pid, None
        if os.name != "nt":
            try:
                guardian = int((self.layout.root / "logs/anelf_guardian.pid").read_text("utf-8").strip())
                if pid_alive(guardian):
                    return "starting", None, runtime
            except (OSError, ValueError) as exc:
                logging.getLogger(__name__).debug("Guardian is not active: %s", exc)
        if self._process is not None and self._process.poll() is None:
            return "starting", None, runtime
        if self._process is not None and self._process.returncode not in {None, 0, 130, 143, -2, -15}:
            return "failed", None, runtime
        external = self._external_pid()
        if external:
            return "unmanaged", external, runtime
        return "stopped", None, runtime

    def status(self) -> dict[str, Any]:
        with self._mutex:
            phase, pid, _runtime = self._state()
            busy = self._operation.locked()
            if busy or (phase == "stopped" and self._last and not self._last["ok"]):
                phase = self._phase
            ready = self.ready
            return {"root": str(self.layout.root), "backend_id": self._backend_id, "phase": phase, "running": pid is not None,
                    "web_ready": bool(pid and self._web_ready(pid)) if phase == "running" else False,
                    "controllable": bool(_runtime and pid and _runtime["pid"] == pid),
                    "pid": pid, "external_pid": self._external_pid(), "busy": busy,
                    "settings": asdict(self.settings), "web_url": self.web_url,
                    "resolved_source": self._repository["source"] or resolve_source(self.settings.source, self.settings.timezone),
                    "repository": self._repository,
                    "downloads": download_profile(self.settings),
                    "storage": inspect_storage(self.layout.root),
                    "ready": ready, "resumable": not ready and (self.layout.root / "pyproject.toml").is_file(),
                    "operation": self.operation.snapshot(),
                    "last": self._last, "events": list(self._events)}

    @property
    def ready(self) -> bool:
        if not (self.layout.python.is_file() and (self.layout.root / "web/frontend/dist/index.html").is_file()):
            return False
        try:
            return bool(json.loads((self.layout.state / "installation.json").read_text("utf-8"))["ready"])
        except FileNotFoundError:
            return True  # 已有 start 安装的项目没有 APP 安装记录。
        except (OSError, ValueError, KeyError):
            return False

    def cancel(self) -> dict[str, bool]:
        with self._mutex:
            if not self._operation.locked():
                raise RuntimeError("There is no installation in progress")
            self.operation.cancel()
            self.emit("Cancellation requested; waiting for the current installer to exit.")
        return {"ok": True}

    @property
    def web_url(self) -> str:
        runtime = self._runtime()
        if runtime is not None and pid_alive(runtime["pid"]) and "web_url" in runtime:
            return str(runtime["web_url"])
        host = self.settings.web_host
        host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
        return f"http://{'[' + host + ']' if ':' in host else host}:{self.settings.web_port}/webui/"

    def configure(self, value: dict[str, Any]) -> None:
        with self._mutex:
            if self._operation.locked() or self._state()[0] not in {"stopped", "failed"}:
                raise RuntimeError("Stop the application before changing settings")
            settings = Settings.parse({**asdict(self.settings), **value})
            port = probe_port(settings.web_host, settings.web_port)
            if not port["available"]:
                raise RuntimeError(f"Port unavailable: {port['detail']}")
            self._save_configuration(settings, repository="source" in value)

    def inspect_repository(self) -> RepositoryInfo:
        self._repository = describe(origin(self.layout.root, self.environment.tool("git")))
        return self._repository

    def configure_repository(self, source: str) -> RepositoryInfo:
        """Change an explicit upstream without restarting a service or touching its branch."""
        with self._mutex:
            if self._operation.locked():
                raise RuntimeError("Wait for the current project operation before changing its repository")
            settings = Settings.parse({**asdict(self.settings), "source": source})
            self._save_configuration(settings, repository=True)
            return self._repository

    def _save_configuration(self, settings: Settings, *, repository: bool) -> None:
        if not self._project.acquire():
            raise RuntimeError("Another operation is using this project")
        try:
            git = self.environment.tool("git") if repository else None
            old = origin(self.layout.root, git) if repository else ""
            selected = set_source(self.layout.root, git, settings.source) if repository else self._repository
            try:
                self.layout.save_settings(settings)
            except OSError:
                if repository and git:
                    change_origin(self.layout.root, git, origin(self.layout.root, git), old)
                raise
            self.settings = settings
            self._repository = selected
        finally:
            self._project.release()

    def web_access(self, password: str | None = None) -> dict[str, Any]:
        """通过项目解释器与 ConfigPaths 读写原 Web 密码；只返回是否设置，不回传密码。"""
        with self._mutex:
            if password is not None and (not isinstance(password, str) or len(password) > 1024):
                raise ValueError("Invalid password request")
            if password is not None and (self._operation.locked() or self._state()[0] not in {"stopped", "failed"}):
                raise RuntimeError("Stop the application before changing its password")
            if not self.ready:
                if password is not None:
                    raise RuntimeError("Install the project environment first")
                return {"available": False, "password_set": False, "addresses": []}
            try:
                result = subprocess.run(
                    [str(self.layout.python), str(Path(__file__).with_name("web_access.py"))],
                    cwd=self.layout.root, env=self.environment.environ(),
                    input=json.dumps({} if password is None else {"password": password}),
                    capture_output=True, text=True, encoding="utf-8", timeout=20, **background_options(),
                )
                if result.returncode != 0:
                    raise ValueError("Project access helper failed")
                value = json.loads(result.stdout)
                if not isinstance(value, dict) or value.get("available") is not True:
                    raise ValueError("Invalid project access response")
                return value
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError("Unable to confirm Web access settings. Reopen settings to check their status.") from exc

    def submit(self, action: str, source: str | None = None, timezone: str | None = None) -> dict[str, Any]:
        if action not in {"setup", "install", "update", "start", "stop", "restart"}:
            raise ValueError("Unknown action")
        settings = Settings.parse({**asdict(self.settings),
                                   **({"source": source} if source is not None else {}),
                                   **({"timezone": timezone} if timezone is not None else {})})
        with self._mutex:
            if not self._operation.acquire(blocking=False):
                raise RuntimeError("Another operation is in progress")
            if not self._project.acquire():
                self._operation.release()
                raise RuntimeError("AI or another manager is performing a project operation")
            try:
                self.layout.save_settings(settings)
                self.settings = settings
                # 重启转交既有 devops 服务，由它取得同一项目锁。
                if action in {"restart", "stop"}:
                    self._project.release()
                self._last = None
                self._phase = action
                self.operation.begin(action)
                write_json(self.layout.state / "operation.json", self.operation.snapshot())
                self._job = threading.Thread(target=self._execute, args=(action,), name="manager.operation", daemon=True)
                self._job.start()
            except Exception:
                self._project.release()
                self._operation.release()
                raise
        return {"ok": True, "accepted": action}

    def _execute(self, action: str) -> None:
        try:
            if action == "start":
                self._start()
            elif action == "stop":
                self._stop()
            elif action == "restart":
                self._host_request("restart")
            else:
                self._install(update=action == "update")
                if action == "setup":
                    self.operation.check()
                    self._start()
            with self._mutex:
                self._last = {"ok": True, "action": action}
                self.operation.finish("done")
        except Cancelled as exc:
            self.emit(str(exc))
            with self._mutex:
                self._last = {"ok": False, "cancelled": True, "action": action, "message": str(exc)}
                self._phase = "cancelled"
                self.operation.finish("cancelled")
        except Exception as exc:
            self.emit(str(exc))
            with self._mutex:
                self._last = {"ok": False, "action": action, "message": str(exc)}
                self._phase = "failed"
                self.operation.finish("failed")
        finally:
            try:
                write_json(self.layout.state / "operation.json", self.operation.snapshot())
            finally:
                self._project.release()
                self._operation.release()

    def _install(self, update: bool) -> None:
        require_storage(self.layout.root)
        if self._external_pid():
            raise RuntimeError("Stop the older instance with its original entry point before installing")
        if not (self.layout.root / "pyproject.toml").exists():
            if update:
                raise RuntimeError("Install AnelfAgent before updating")
            self._checkout()
        self.operation.stage("check")
        git = self.environment.tool("git")
        if update:
            if not git:
                raise RuntimeError("Git is unavailable")
            if self.environment.run([git, "status", "--porcelain"]).strip():
                raise RuntimeError("Uncommitted changes found; update refused without changing files")
        was_running = self._state()[0] not in {"stopped", "failed"}
        self.operation.check()
        self.operation.stage("stop")
        self._stop()
        self.operation.check()
        write_json(self.layout.state / "installation.json", {"ready": False})
        if git:
            self._repository = set_source(self.layout.root, git, self.settings.source)
        if update and git:
            self.operation.stage("checkout")
            self.environment.run([git, "pull", "--ff-only"], timeout=120)
        self.environment.sync()
        self.operation.stage("verify")
        if not self.layout.python.is_file() or not (self.layout.root / "web/frontend/dist/index.html").is_file():
            raise RuntimeError("Installation is incomplete: Python or the built web interface is missing")
        write_json(self.layout.state / "installation.json", {"ready": True})
        self._phase = "stopped"
        if was_running:
            self.operation.check()
            self._start()

    def _checkout(self) -> None:
        """空目标目录从所选官方源获取源码，已有用户文件拒绝覆盖。"""
        import shutil
        import tempfile
        if any(item.name != "logs" for item in self.layout.root.iterdir()):
            raise RuntimeError("Choose an existing AnelfAgent checkout or an empty install directory")
        self.operation.stage("git")
        self.environment.install_git()
        git = self.environment.tool("git")
        if not git:
            raise RuntimeError("Git is unavailable")
        source = resolve_source(self.settings.source, self.settings.timezone)
        with tempfile.TemporaryDirectory(dir=self.layout.state) as staging:
            target = Path(staging) / "source"
            self.operation.stage("checkout")
            self.environment.run([git, "clone", "--progress", REPOSITORY_SOURCES[source], str(target)], timeout=600)
            self.operation.check()
            if not (target / "uv.lock").is_file() or not (target / "launch.py").is_file():
                raise RuntimeError("Downloaded repository is not an AnelfAgent checkout")
            for item in target.iterdir():
                if (self.layout.root / item.name).exists():
                    raise RuntimeError(f"Install destination already contains {item.name}")
            for item in target.iterdir():
                shutil.move(str(item), self.layout.root / item.name)

    def _start(self) -> None:
        if self._external_pid():
            raise RuntimeError("Another Anelf instance owns this checkout")
        if self._state()[0] not in {"stopped", "failed"}:
            return
        if not self.ready:
            raise RuntimeError("Install the project environment first")
        port = probe_port(self.settings.web_host, self.settings.web_port)
        if not port["available"]:
            raise RuntimeError(f"WebUI port is occupied: {port['detail']}")
        script = self.layout.root / ("start.bat" if os.name == "nt" else "start.sh")
        if not script.is_file():
            raise RuntimeError("The project start entry is missing")
        backend = Path(__file__).resolve().parent.parent
        if "ANELF_LAUNCH_ENTRY" not in script.read_text("utf-8"):
            script = backend / script.name
            if not script.is_file():
                raise RuntimeError("The compatible start entry is missing; reinstall the desktop app")
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(script)] if os.name == "nt" else ["bash", str(script)]
        env = self.environment.environ()
        env.update(ANELF_WEBUI_HOST=self.settings.web_host, ANELF_WEBUI_PORT=str(self.settings.web_port),
                   ANELF_ENV_READY="1", ANELF_PROJECT_ROOT=str(self.layout.root),
                   ANELF_LAUNCH_ENTRY=str(backend / "app/runtime_entry.py"))
        with (self.layout.state / "application.log").open("ab") as log:
            self._process = subprocess.Popen(command, cwd=self.layout.root, env=env, stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=subprocess.STDOUT,
                                             **background_options())
        self.emit("Application started through " + script.name)
        self.operation.stage("starting")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            self.operation.check()
            phase, _pid, _runtime = self._state()
            if phase == "running":
                return
            if phase in {"failed", "stopped"}:
                raise RuntimeError("Application exited during startup; check application logs")
            time.sleep(.15)
        raise RuntimeError("Application startup is taking longer than expected; check application logs")

    def _host_request(self, action: str) -> None:
        runtime = self._runtime()
        if runtime is None or not pid_alive(runtime["pid"]):
            raise RuntimeError("The application control channel is not ready; check startup logs")
        path = Path(runtime["control_file"])
        request_id = secrets.token_hex(16)
        write_json(path, {"action": action, "token": runtime["token"], "pid": runtime["pid"], "id": request_id})
        deadline = time.monotonic() + min(30, self.settings.stop_timeout)
        result_path = path.with_suffix(".result.json")
        while time.monotonic() < deadline:
            try:
                result = json.loads(result_path.read_text("utf-8"))
                if result.get("id") == request_id:
                    result_path.unlink(missing_ok=True)
                    if not result.get("ok"):
                        raise RuntimeError(str(result.get("message", "Operation refused")))
                    self.emit("Application accepted " + action)
                    return
            except (OSError, ValueError) as exc:
                logging.getLogger(__name__).debug("Host acknowledgement not ready: %s", exc)
            if not pid_alive(runtime["pid"]) and action == "stop":
                return
            time.sleep(0.1)
        raise RuntimeError("Application did not acknowledge the request; no process was killed")

    def _stop(self) -> None:
        if self._external_pid():
            raise RuntimeError("The older instance has no local control channel; stop it through its original terminal")
        deadline = time.monotonic() + self.settings.stop_timeout
        control_deadline = time.monotonic() + min(10, self.settings.stop_timeout)
        requested: set[int] = set()
        while time.monotonic() < deadline:
            phase, pid, _runtime = self._state()
            if phase in {"stopped", "failed"}:
                return
            if not requested and (_runtime is None or not pid_alive(_runtime["pid"])) and time.monotonic() >= control_deadline:
                raise RuntimeError("Service control is unavailable. Stop the application from its original entry point; no process was killed")
            if pid is not None and pid not in requested:
                self._host_request("stop")
                requested.add(pid)
            time.sleep(0.1)
        raise RuntimeError("Application is still shutting down; no process was killed. Check logs before retrying")
