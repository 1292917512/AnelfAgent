"""只读确认 Web 健康与监听进程归属，不把端口占用视为启动成功。"""

from __future__ import annotations

import http.client
import ipaddress
import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import psutil


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: urllib.request.Request, fp: object, code: int, msg: str,
                         headers: object, newurl: str) -> None:
        return None


def web_ready(root: Path, pid: int, url: str) -> bool:
    """核实项目进程、具体监听地址和 /health；不使用系统代理或跟随重定向。"""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return False
        host = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        process = psutil.Process(pid)
        if Path(process.cwd()).resolve() != root.resolve():
            return False
        if not any(Path(arg).name in {"launch.py", "runtime_entry.py"} for arg in process.cmdline()):
            return False
        owned = False
        for connection in process.net_connections(kind="tcp"):
            if connection.status != psutil.CONN_LISTEN or connection.laddr.port != port:
                continue
            bound = ipaddress.ip_address(connection.laddr.ip)
            if bound == host or bound.is_unspecified and bound.version == host.version:
                owned = True
                break
        if not owned:
            return False
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(f"{parsed.scheme}://{parsed.netloc}/health", timeout=1.0) as response:
            result = json.loads(response.read(4097))
            return response.status == 200 and isinstance(result, dict) and result.get("status") == "ok" and process.is_running()
    except (OSError, ValueError, psutil.Error, urllib.error.URLError, http.client.HTTPException):
        return False
