"""仅绑定回环地址的管理 API，应用离线时仍可使用。"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.manager import Manager
from app.network import inspect_network
from app.settings import DOWNLOADS, REPOSITORY_SOURCES, Layout, Settings, download_profile, probe_port
from core.host_protocol import HostLock, write_json

SETTINGS_SCHEMA = [
    {"name": "source", "type": "select", "advanced": True, "options": ["auto", "github", "gitee"],
     "option_labels": {"auto": {"zh": "沿用原仓库 / 新安装自动选择", "en": "Keep origin / automatic for new installs"}},
     "label": {"zh": "项目仓库来源", "en": "Project repository"}},
    {"name": "download_source", "type": "select", "advanced": True, "options": ["auto", "china", "global"],
     "label": {"zh": "环境下载源", "en": "Environment download source"}},
    {"name": "pypi_source", "type": "select", "advanced": True, "options": ["auto", *DOWNLOADS["pypi_sources"]],
     "option_labels": {"auto": {"zh": "跟随环境来源", "en": "Follow environment source"},
                       **{key: value["label"] for key, value in DOWNLOADS["pypi_sources"].items()}},
     "label": {"zh": "uv / Python 包镜像", "en": "uv / Python package index"}},
    {"name": "proxy", "type": "text", "advanced": True, "placeholder": "http://127.0.0.1:7890",
     "label": {"zh": "下载代理（安装用，可选）", "en": "Download proxy (install only, optional)"}},
    {"name": "web_host", "type": "select", "options": ["0.0.0.0", "127.0.0.1"],
     "option_labels": {"0.0.0.0": {"zh": "允许局域网访问", "en": "Allow LAN access"},
                       "127.0.0.1": {"zh": "仅本机访问", "en": "This device only"}},
     "label": {"zh": "访问范围", "en": "Access scope"}},
    {"name": "web_port", "type": "number", "min": 1, "max": 65535,
     "label": {"zh": "Anelf WebUI 端口", "en": "Anelf WebUI port"}},
    {"name": "stop_timeout", "type": "number", "advanced": True, "min": 5, "max": 3600,
     "label": {"zh": "安全关停等待（秒）", "en": "Shutdown timeout (seconds)"}},
    {"name": "auto_start", "type": "checkbox", "label": {"zh": "管理器启动时运行 Anelf", "en": "Run Anelf on manager start"}},
]


class ManagerServer(ThreadingHTTPServer):
    """API 令牌与进程状态按服务器实例隔离。"""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, manager: Manager, port: int) -> None:
        self.manager = manager
        self.token = secrets.token_urlsafe(32)
        self.detach = False
        super().__init__(("127.0.0.1", port), Handler)
        self.base_url = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    """限制 Host、Origin、请求大小和 API 令牌，不允许任意命令执行。"""

    server: ManagerServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _reply(self, code: int, data: Any, content_type: str | None = None) -> None:
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type or ("text/html; charset=utf-8" if isinstance(data, bytes)
                         else "application/json; charset=utf-8"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self, api: bool) -> bool:
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self._reply(403, {"message": "Invalid Host"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.base_url:
            self._reply(403, {"message": "Invalid Origin"})
            return False
        if api and not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.token):
            self._reply(401, {"message": "Manager token required"})
            return False
        return True

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        assets = {"/": ("panel.html", "text/html"), "/app.js": ("ui/app.js", "text/javascript"),
                  "/style.css": ("ui/style.css", "text/css"),
                  "/theme.css": ("desktop/theme.css", "text/css"),
                  "/desktop/errors.mjs": ("desktop/errors.mjs", "text/javascript"),
                  "/icon.svg": ("desktop/icon.svg", "image/svg+xml")}
        assets.update({"/" + name: ("ui/" + name, "text/javascript")
                       for name in ("i18n.js", "model.js", "dom.js", "progress.js", "network.js", "access.js")})
        if not self._allowed(api=path not in assets):
            return
        try:
            if path in assets:
                filename, content_type = assets[path]
                self._reply(200, (Path(__file__).parent / filename).read_bytes(), content_type + "; charset=utf-8")
            elif path == "/api/status":
                self._reply(200, self.server.manager.status())
            elif path == "/api/environment":
                self._reply(200, self.server.manager.environment.inspect())
            elif path == "/api/schema":
                self._reply(200, SETTINGS_SCHEMA)
            elif path == "/api/web-access":
                self._reply(200, self.server.manager.web_access())
            elif path == "/api/sources":
                self._reply(200, {"repositories": REPOSITORY_SOURCES, "pypi_sources": DOWNLOADS["pypi_sources"],
                                  "china_timezones": DOWNLOADS["china_timezones"],
                                  "pypi_defaults": {key: DOWNLOADS[key]["pypi"] for key in ("china", "global")},
                                  "downloads": download_profile(self.server.manager.settings),
                                  "repository": self.server.manager.inspect_repository()})
            elif path == "/api/ports":
                settings = self.server.manager.settings
                self._reply(200, probe_port(settings.web_host, settings.web_port))
            elif path in {"/api/logs/application", "/api/logs/commands"}:
                name = "application.log" if path.endswith("application") else "commands.log"
                file = self.server.manager.layout.state / name
                content = ""
                if file.exists():
                    with file.open("rb") as stream:
                        stream.seek(max(0, file.stat().st_size - 16000))
                        content = stream.read(16000).decode("utf-8", errors="replace")
                self._reply(200, {"text": content})
            else:
                self._reply(404, {"message": "Unknown endpoint"})
        except (OSError, ValueError, RuntimeError) as exc:
            self._reply(400, {"message": str(exc)})

    def do_POST(self) -> None:
        if not self._allowed(api=True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096 or self.headers.get_content_type() != "application/json":
                self._reply(400, {"message": "A JSON body of at most 4096 bytes is required"})
                return
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            path = urlsplit(self.path).path
            if path == "/api/action":
                source = body.get("source")
                if source is not None and not isinstance(source, str):
                    raise ValueError("Source must be a string")
                timezone = body.get("timezone")
                if timezone is not None and not isinstance(timezone, str):
                    raise ValueError("Timezone must be a string")
                self._reply(202, self.server.manager.submit(str(body.get("action", "")), source=source, timezone=timezone))
            elif path == "/api/settings":
                self.server.manager.configure(body)
                self._reply(200, {"ok": True})
            elif path == "/api/repository":
                if set(body) != {"source"} or not isinstance(body["source"], str):
                    raise ValueError("Expected a repository source")
                self._reply(200, self.server.manager.configure_repository(body["source"]))
            elif path == "/api/web-access":
                password = body.get("password")
                if not isinstance(password, str) or len(password) > 1024 or set(body) != {"password"}:
                    raise ValueError("Invalid password request")
                self._reply(200, self.server.manager.web_access(password))
            elif path == "/api/network":
                settings = Settings.parse(body)
                self._reply(200, inspect_network(settings))
            elif path == "/api/cancel":
                self._reply(202, self.server.manager.cancel())
            elif path == "/api/detach":
                if self.server.manager.status()["busy"]:
                    raise RuntimeError("Wait for the current operation before replacing the manager")
                self.server.detach = True
                self._reply(202, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif path == "/api/shutdown":
                if self.server.manager.status()["busy"]:
                    raise RuntimeError("Cancel or finish the current operation before closing the manager")
                self._reply(202, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._reply(404, {"message": "Unknown endpoint"})
        except (ValueError, TypeError) as exc:
            self._reply(400, {"message": str(exc)})
        except (OSError, RuntimeError) as exc:
            self._reply(409, {"message": str(exc)})


def serve(layout: Layout, port: int = 8090) -> None:
    """管理端口冲突时尝试后续端口，连接信息只在成功绑定后落盘。"""
    lock = HostLock(layout.state / "manager.lock")
    if not lock.acquire():
        raise RuntimeError("A manager is already running for this checkout")
    manager = Manager(layout)
    server: ManagerServer | None = None
    try:
        for candidate in ([0] if port == 0 else range(port, min(port + 20, 65536))):
            if candidate == manager.settings.web_port:
                continue
            try:
                server = ManagerServer(manager, candidate)
                break
            except OSError:
                continue
        if server is None:
            raise RuntimeError("No management port is available")
        import os
        write_json(layout.connection, {"url": server.base_url, "token": server.token, "pid": os.getpid()})
        layout.connection.chmod(0o600)
        manager.open()
        while True:
            try:
                server.serve_forever(poll_interval=0.25)
            except KeyboardInterrupt:
                manager.emit("Manager shutdown requested")
            try:
                manager.close(detach=server.detach)
                break
            except RuntimeError as exc:
                manager.emit(str(exc))
    finally:
        if server is not None:
            server.server_close()
        layout.connection.unlink(missing_ok=True)
        lock.release()
