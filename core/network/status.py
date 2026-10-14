"""实际代理请求的有界内存快照，不发探测请求，不改变路由与重试策略。"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from core.config import ConfigManager, get_config_bool


def _origin(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")


def _proxy_key(proxy: str) -> str:
    return hashlib.sha256(proxy.strip().rstrip("/").encode()).hexdigest()


@dataclass(frozen=True)
class ProxyObservation:
    """一次目标上的最近访问结果；地址仅含协议、主机和端口。"""

    proxy: str
    target: str
    state: Literal["reachable", "failed"]
    error_code: str
    status_code: int | None
    observed_at: float
    changed_at: float
    failures: int
    recovered: bool


class ProxyObservations:
    """跨线程合并代理与目标的访问结果，最多保留 64 项、15 分钟证据。"""

    def __init__(self) -> None:
        self._records: OrderedDict[tuple[str, str], ProxyObservation] = OrderedDict()
        self._lock = threading.Lock()

    def record(
        self, proxy: str | None, target: str, *,
        error: Exception | None = None, status_code: int | None = None,
    ) -> None:
        """记录代理访问结果，忽略直连和本地连接池拥堵，不保留异常正文。"""
        if not proxy or isinstance(error, httpx.PoolTimeout):
            return
        try:
            endpoint, target = _origin(proxy), _origin(target)
        except ValueError:
            return
        code = "proxy_auth" if status_code == 407 else type(error).__name__ if error else ""
        key = (_proxy_key(proxy), target)
        now = time.time()
        with self._lock:
            previous = self._records.pop(key, None)
            if previous and now - previous.observed_at > 900:
                previous = None
            failures = (previous.failures if previous else 0) + 1 if code else 0
            state: Literal["reachable", "failed"] = "failed" if code else "reachable"
            changed_at = previous.changed_at if previous and previous.state == state else now
            self._records[key] = ProxyObservation(
                endpoint, target, state, code, status_code, now, changed_at,
                failures, not code and bool(previous and (previous.state == "failed" or previous.recovered)),
            )
            while len(self._records) > 64:
                self._records.popitem(last=False)

    def snapshot(self) -> dict[str, Any]:
        """读取当前配置对应的脱敏快照；配置变化后不沿用旧代理的状态。"""
        proxies = dict.fromkeys(str(ConfigManager.get(key, "") or "").strip().rstrip("/")
                                for key in ("http_proxy", "https_proxy"))
        endpoints: dict[str, str] = {}
        for proxy in proxies:
            if proxy:
                try:
                    endpoints[_proxy_key(proxy)] = _origin(proxy)
                except ValueError:
                    continue
        now = time.time()
        with self._lock:
            observations = [asdict(row) for (key, _), row in reversed(self._records.items())
                            if key in endpoints and now - row.observed_at <= 900]
        return {
            "enabled": get_config_bool("proxy_enabled", False),
            "mode": str(ConfigManager.get("proxy_default_mode", "fallback")),
            "proxies": list(endpoints.values()),
            "observations": observations,
        }

    def clear(self) -> None:
        """进程关停时清除观测记录。"""
        with self._lock:
            self._records.clear()


proxy_observations = ProxyObservations()


def get_proxy_status() -> dict[str, Any]:
    """返回实际访问的近期代理状态；无观测不等于不可用。"""
    return proxy_observations.snapshot()
