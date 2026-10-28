"""在目标项目解释器中复用 ConfigPaths 与 auth.password，不启动应用或创建另一套认证。"""

from __future__ import annotations

import ipaddress
import json
import socket
import sys
from pathlib import Path
from typing import Any


def configure_access(path: Path, password: str | None = None) -> dict[str, Any]:
    """保留 Web 配置的其他字段；空密码显式解除保护，读取永远不回显秘密。"""
    from core.host_protocol import write_json

    if password is not None and (not isinstance(password, str) or len(password) > 1024):
        raise ValueError("Invalid password request")
    config = json.loads(path.read_text("utf-8")) if path.exists() else {}
    if not isinstance(config, dict) or not isinstance(config.get("auth", {}), dict):
        raise ValueError("Invalid Web access configuration")
    auth = config.get("auth", {})
    if not isinstance(auth.get("password", ""), str):
        raise ValueError("Invalid Web access configuration")
    if password is not None:
        auth["password"] = password
        if not password:
            auth["strict"] = False
        config["auth"] = auth
        write_json(path, config)
    return {"available": True, "password_set": bool(auth.get("password"))}


def local_addresses() -> list[str]:
    """只枚举已启用网卡，不发送网络探测请求；回环与链路本地 IPv6 不作分享地址。"""
    import psutil

    stats = psutil.net_if_stats()
    addresses: set[str] = set()
    for name, values in psutil.net_if_addrs().items():
        if name not in stats or not stats[name].isup:
            continue
        for value in values:
            if value.family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            address = ipaddress.ip_address(value.address.split("%", 1)[0])
            if address.is_loopback or address.is_unspecified or address.is_multicast:
                continue
            if address.version == 6 and address.is_link_local:
                continue
            addresses.add(str(address))
    return sorted(addresses)


def main() -> None:
    """密码仅经标准输入传递，使用项目实际配置路径，避免在命令行或日志暴露。"""
    sys.path.insert(0, str(Path.cwd()))
    import core
    core.__path__.append(str(Path(__file__).resolve().parents[1] / "core"))
    from core.path import ConfigPaths

    request = json.load(sys.stdin)
    if not isinstance(request, dict) or set(request) - {"password"}:
        raise ValueError("Invalid Web access request")
    result = configure_access(Path(ConfigPaths.WEBUI_CONFIG), request.get("password"))
    try:
        result["addresses"] = local_addresses()
    except (OSError, ImportError, ValueError):
        result["addresses"] = []
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never include request data or configuration contents in subprocess diagnostics.
        print(json.dumps({"error": "Web access configuration is unavailable"}))
        sys.exit(1)
