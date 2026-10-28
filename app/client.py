"""客户端只通过本机管理 API 工作，不另行持有应用子进程。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from app.processes import background_options
from app.settings import Layout


class Client:
    """连接同一检出的现有守护进程，或启动一个后台管理进程。"""

    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.url = ""
        self.token = ""
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def connect(self) -> bool:
        try:
            value = json.loads(self.layout.connection.read_text("utf-8"))
            self.url, self.token = value["url"], value["token"]
            from urllib.parse import urlsplit
            parsed = urlsplit(self.url)
            if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
                raise ValueError("Invalid local manager address")
            self.request("/api/status")
            return True
        except (OSError, ValueError, KeyError, RuntimeError):
            return False

    def ensure(self, port: int = 8090) -> None:
        if self.connect():
            return
        command = [sys.executable, "-m", "app"]
        command += ["--root", str(self.layout.root), "serve", "--port", str(port)]
        # 源码入口的模块位于当前工具目录，目标检出允许与工具目录不同。
        from pathlib import Path
        environment = dict(os.environ)
        with (self.layout.state / "manager.log").open("ab") as log:
            directory = Path(__file__).resolve().parent.parent
            subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT, close_fds=True, env=environment,
                             start_new_session=os.name != "nt",
                             **background_options())
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.connect():
                return
            time.sleep(0.15)
        raise RuntimeError(f"Manager startup failed; see {self.layout.state / 'manager.log'}")

    @property
    def browser_url(self) -> str:
        return self.url + "/#" + self.token

    def request(self, path: str, body: dict[str, Any] | None = None) -> Any:
        request = urllib.request.Request(self.url + path,
                                         data=None if body is None else json.dumps(body).encode("utf-8"),
                                         headers={"Authorization": "Bearer " + self.token,
                                                  "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=50) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                result = json.load(exc)
            raise RuntimeError(str(result.get("message", "Request failed"))) from exc
