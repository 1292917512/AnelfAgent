"""网络路由的按需采样诊断；不保存响应正文、凭据或长期状态。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.config import ConfigManager
from core.network.policy import ProxyMode, resolve_network_route
from core.network.transport import AsyncNetworkTransport

DEFAULT_PROBE_URL = "https://www.gstatic.com/generate_204"
DiagnosticMode = Literal["compare", "system", "direct", "proxy"]


class NetworkProbeRequest(BaseModel):
    """从服务端发起无认证 HEAD 请求，不跟随重定向。"""

    model_config = ConfigDict(extra="forbid")
    url: str = Field(default=DEFAULT_PROBE_URL, max_length=2048)
    mode: DiagnosticMode = "compare"
    samples: int = Field(default=3, ge=1, le=5, strict=True)
    timeout_seconds: float = Field(default=5, ge=1, le=10, allow_inf_nan=False)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        try:
            parts = urlsplit(value)
            if (parts.scheme not in {"http", "https"} or not parts.hostname
                    or parts.username is not None or parts.password is not None
                    or parts.query or parts.fragment or any(ord(c) < 33 for c in value)):
                raise ValueError
            _ = parts.port
            httpx.URL(value)
        except (ValueError, httpx.InvalidURL):
            raise ValueError("检测地址须为 HTTP(S) URL，不能含凭据、查询参数、片段或空白") from None
        return value


class ProbeSample(BaseModel):
    index: int
    elapsed_ms: float
    reachable: bool = False
    http_ok: bool = False
    status_code: int | None = None
    path: Literal["direct", "proxy"] | None = None
    source: str = ""
    attempts: int = 0
    error_code: str | None = None


class ProbeRouteReport(BaseModel):
    mode: ProxyMode
    status: Literal["passed", "partial", "failed", "http_error", "unavailable"]
    samples: list[ProbeSample]
    reachable_count: int = 0
    http_ok_count: int = 0
    median_ms: float | None = None
    spread_ms: float | None = None
    error_code: str | None = None


class NetworkProbeReport(BaseModel):
    url: str
    checked_at: float
    elapsed_ms: float
    configuration_changed: bool
    routes: list[ProbeRouteReport]
    scope: str = "server_head_request"
    limitation: str = "仅反映当前采样；HTTP 错误不等于代理故障，直连成功不证明代理可用。"


def _config_revision() -> bytes:
    values = {key: ConfigManager.get(key) for key in (
        "proxy_enabled", "http_proxy", "https_proxy", "proxy_default_mode", "proxy_rules", "proxy_connect_timeout",
    )}
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).digest()


def _error_code(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, httpx.ProxyError):
        return "proxy_error"
    if isinstance(exc, httpx.ConnectError):
        return "connect_error"
    if isinstance(exc, httpx.TransportError):
        return "transport_error"
    return "client_error"


async def _probe(request: NetworkProbeRequest, mode: ProxyMode, index: int) -> ProbeSample:
    started = time.perf_counter()
    result = ProbeSample(index=index, elapsed_ms=0)
    outgoing = httpx.Request("HEAD", request.url, headers={"User-Agent": "AnelfAgent-NetworkCheck"})
    try:
        async with asyncio.timeout(request.timeout_seconds):
            async with httpx.AsyncClient(
                transport=AsyncNetworkTransport(mode, limits=httpx.Limits(max_keepalive_connections=0)),
                timeout=request.timeout_seconds, trust_env=False, follow_redirects=False,
            ) as client:
                response = await client.send(outgoing, stream=True)
                try:
                    result.status_code = response.status_code
                    result.reachable = response.status_code != 407
                    result.http_ok = 200 <= response.status_code < 400
                    if not result.http_ok:
                        result.error_code = "proxy_auth" if response.status_code == 407 else "http_status"
                finally:
                    await response.aclose()
    except ValueError:
        result.error_code = "configuration"
    except Exception as exc:
        result.error_code = _error_code(exc)
    route = outgoing.extensions.get("network_route", {})
    result.path = route.get("path")
    result.source = route.get("source", "")
    result.attempts = route.get("attempts", 0)
    result.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return result


async def _probe_route(request: NetworkProbeRequest, mode: ProxyMode) -> ProbeRouteReport:
    try:
        resolve_network_route(request.url, mode)
    except ValueError:
        return ProbeRouteReport(mode=mode, status="unavailable", samples=[], error_code="configuration")
    samples = []
    for index in range(1, request.samples + 1):
        samples.append(await _probe(request, mode, index))
        if index < request.samples:
            await asyncio.sleep(0.2)
    reachable = sum(item.reachable for item in samples)
    ok = sum(item.http_ok for item in samples)
    times = sorted(item.elapsed_ms for item in samples if item.reachable)
    report = ProbeRouteReport(
        mode=mode, status="failed", samples=samples, reachable_count=reachable, http_ok_count=ok,
    )
    if ok == len(samples):
        report.status = "passed"
    elif reachable == len(samples) and not ok:
        report.status = "http_error"
    elif reachable:
        report.status = "partial"
    if times:
        middle = len(times) // 2
        report.median_ms = round((times[middle] + times[(len(times) - 1) // 2]) / 2, 1)
        report.spread_ms = round(times[-1] - times[0], 1)
    return report


async def diagnose_network(request: NetworkProbeRequest) -> NetworkProbeReport:
    """比较独立连接的规则、直连与代理路径，报告有限样本和配置漂移。"""
    started, checked_at = time.perf_counter(), time.time()
    revision = _config_revision()
    modes: list[ProxyMode] = ["system", "direct", "proxy"] if request.mode == "compare" else [request.mode]
    routes = await asyncio.gather(*(_probe_route(request, mode) for mode in modes))
    return NetworkProbeReport(
        url=request.url, checked_at=checked_at, routes=routes,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        configuration_changed=revision != _config_revision(),
    )
