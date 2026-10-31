"""启动器配置与端口探测，安装前即可运行。"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.host_protocol import write_json

REPOSITORY_SOURCES = json.loads(Path(__file__).with_name("repositories.json").read_text("utf-8"))
DOWNLOADS = json.loads(Path(__file__).with_name("downloads.json").read_text("utf-8"))


def local_timezone() -> str:
    """优先使用 Electron 识别的 IANA 时区，命令行使用系统时区名称。"""
    return os.environ.get("ANELF_LAUNCHER_TIMEZONE") or os.environ.get("TZ") or time.tzname[0]


def resolve_source(source: str, timezone: str) -> str:
    """按中国时区选择国内源；显式选择始终优先于自动判断。"""
    if source != "auto":
        return source
    return "gitee" if timezone in DOWNLOADS["china_timezones"] else "github"


def download_profile(settings: Settings) -> dict[str, str]:
    key = settings.download_source
    if key == "auto":
        key = "china" if resolve_source("auto", settings.timezone) == "gitee" else "global"
    profile = dict(DOWNLOADS[key])
    if settings.pypi_source != "auto":
        profile["pypi"] = DOWNLOADS["pypi_sources"][settings.pypi_source]["url"]
    return profile


def probe_port(host: str, port: int) -> dict[str, Any]:
    """实际尝试绑定，不依赖平台 netstat 文本，也不终止占用进程。"""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((host, port))
        return {"host": host, "port": port, "available": True, "detail": ""}
    except OSError as exc:
        return {"host": host, "port": port, "available": False, "detail": str(exc)}


@dataclass(frozen=True)
class Settings:
    """仅保存管理器自己的设置；应用配置仍由应用配置中心持有。"""

    web_host: str = "0.0.0.0"
    web_port: int = 8092
    stop_timeout: int = 600
    auto_start: bool = False
    source: str = "auto"
    download_source: str = "auto"
    pypi_source: str = "auto"
    proxy: str = ""
    timezone: str = field(default_factory=local_timezone)

    @classmethod
    def parse(cls, value: dict[str, Any]) -> Settings:
        if set(value) - set(cls.__dataclass_fields__):
            raise ValueError("Unknown setting")
        setting = cls(**value)
        ipaddress.ip_address(setting.web_host)
        if type(setting.web_port) is not int or not 1 <= setting.web_port <= 65535:
            raise ValueError("WebUI port must be between 1 and 65535")
        if type(setting.stop_timeout) is not int or not 5 <= setting.stop_timeout <= 3600:
            raise ValueError("Stop timeout must be between 5 and 3600 seconds")
        if type(setting.auto_start) is not bool:
            raise ValueError("auto_start must be boolean")
        if setting.source not in {"auto", *REPOSITORY_SOURCES}:
            raise ValueError("Source must be auto, github or gitee")
        if setting.download_source not in {"auto", "china", "global"}:
            raise ValueError("Download source must be auto, china or global")
        if setting.pypi_source not in {"auto", *DOWNLOADS["pypi_sources"]}:
            raise ValueError("Unknown uv package source")
        if not isinstance(setting.proxy, str) or len(setting.proxy) > 300 or any(c.isspace() for c in setting.proxy):
            raise ValueError("Invalid proxy")
        if setting.proxy and not setting.proxy.startswith(("http://", "https://")):
            raise ValueError("Proxy must use http:// or https://")
        if not isinstance(setting.timezone, str) or not 1 <= len(setting.timezone) <= 100:
            raise ValueError("Invalid timezone")
        return setting


PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def apply_proxy_env(settings: Settings) -> None:
    """把代理同步进管理进程环境，供进程内下载使用；空值清空旧变量保持行为确定。"""
    for key in (*PROXY_VARIABLES, "NO_PROXY", "no_proxy"):
        os.environ.pop(key, None)
    if settings.proxy:
        for key in PROXY_VARIABLES:
            os.environ[key] = settings.proxy
        os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"


class Layout:
    """所有管理状态归属目标检出，目录可包含空格与中文。"""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.state = self.root / "logs" / "manager"
        self.state.mkdir(parents=True, exist_ok=True)
        self.tools = self.state / "tools"
        self.connection = self.state / "connection.json"
        self.settings_file = self.state / "settings.json"

    @property
    def python(self) -> Path:
        return self.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def load_settings(self) -> Settings:
        if self.settings_file.exists():
            return Settings.parse(json.loads(self.settings_file.read_text("utf-8")))
        return Settings()

    def save_settings(self, settings: Settings) -> None:
        write_json(self.settings_file, asdict(settings))
