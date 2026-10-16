"""请求级网络路由：全局代理地址、顺序规则与组件策略。"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from core.config import ConfigManager, get_config_bool, register_configs

ProxyMode = Literal["system", "direct", "proxy"]
RouteAction = Literal["direct", "proxy", "fallback"]


def validate_proxy_url(value: str) -> str:
    """校验 HTTP/SOCKS 代理地址，禁止路径与查询参数。"""
    value = value.strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https", "socks5", "socks5h"}
                or not parts.hostname or not parts.port
                or parts.path not in {"", "/"} or parts.query or parts.fragment):
            raise ValueError
    except ValueError:
        raise ValueError("代理地址须为 http(s)://host:port 或 socks5(h)://host:port") from None
    return value.rstrip("/")


def _domain(value: str) -> str:
    host = value.strip().rstrip(".").lower().encode("idna").decode("ascii")
    if not host or len(host) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
        for part in host.split(".")
    ):
        raise ValueError("域名格式无效（不含协议、端口或通配符）")
    return host


@dataclass(frozen=True)
class RouteRule:
    kind: str
    target: str
    action: RouteAction

    def matches(self, host: str) -> bool:
        if self.kind == "DOMAIN":
            return host == self.target
        if self.kind == "DOMAIN-SUFFIX":
            return host == self.target or host.endswith("." + self.target)
        try:
            return ipaddress.ip_address(host) in ipaddress.ip_network(self.target)
        except ValueError:
            return False


@lru_cache(maxsize=32)
def parse_rules(value: str) -> tuple[RouteRule, ...]:
    """解析 DOMAIN / DOMAIN-SUFFIX / IP-CIDR 规则，首条命中生效。"""
    if len(value) > 65536:
        raise ValueError("代理规则文本最多 65536 字符")
    rules: list[RouteRule] = []
    for index, line in enumerate(value.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            kind, target, raw_action = (part.strip() for part in line.split(","))
            kind, raw_action = kind.upper(), raw_action.lower()
            action: RouteAction
            if raw_action == "direct":
                action = "direct"
            elif raw_action == "proxy":
                action = "proxy"
            elif raw_action == "fallback":
                action = "fallback"
            else:
                raise ValueError("策略须为 DIRECT、PROXY 或 FALLBACK")
            if kind in {"DOMAIN", "DOMAIN-SUFFIX"}:
                target = _domain(target)
            elif kind == "IP-CIDR":
                target = str(ipaddress.ip_network(target, strict=False))
            else:
                raise ValueError("类型须为 DOMAIN、DOMAIN-SUFFIX 或 IP-CIDR")
            rules.append(RouteRule(kind, target, action))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"代理规则第 {index} 行无效：{exc}") from None
    if len(rules) > 512:
        raise ValueError("代理规则最多 512 条")
    return tuple(rules)


def validate_rules(value: str) -> str:
    parse_rules(value)
    return value.strip()


@dataclass(frozen=True)
class NetworkRoute:
    """一次请求的路由快照；proxies 按尝试顺序排列，None 表示直连。"""

    action: RouteAction
    source: str
    proxies: tuple[str | None, ...] = field(repr=False)
    connect_timeout: float = 3.0


def _local_host(host: str) -> bool:
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return "." not in host or host.endswith((".localhost", ".local"))


def resolve_network_route(url: str, proxy_mode: ProxyMode = "system") -> NetworkRoute:
    """按组件选择、全局开关、站点规则、内网直连与默认策略依次解析。"""
    if proxy_mode not in {"system", "direct", "proxy"}:
        raise ValueError("proxy_mode 须为 system、direct 或 proxy")
    parts = urlsplit(url)
    host = (parts.hostname or "").rstrip(".").lower().encode("idna").decode("ascii")
    if not host:
        raise ValueError("网络请求 URL 缺少主机名")
    if proxy_mode == "direct" or parts.scheme not in {"http", "https", "ws", "wss", "tcp"}:
        return NetworkRoute("direct", "component", (None,))
    if not get_config_bool("proxy_enabled", False):
        if proxy_mode == "proxy":
            raise ValueError("全局代理未启用，请配置 network/proxy 或选择 system/direct")
        return NetworkRoute("direct", "disabled", (None,))
    action: RouteAction = "proxy"
    source = "component"
    if proxy_mode == "system":
        source = "default"
        configured = str(ConfigManager.get("proxy_default_mode", "fallback"))
        if configured not in {"direct", "proxy", "fallback"}:
            raise ValueError("proxy_default_mode 无效")
        action = "fallback" if configured == "fallback" else "proxy" if configured == "proxy" else "direct"
        for index, rule in enumerate(parse_rules(str(ConfigManager.get("proxy_rules", "")))):
            if rule.matches(host):
                action, source = rule.action, f"rule:{index + 1}"
                break
        else:
            if _local_host(host):
                return NetworkRoute("direct", "local", (None,))
    if action == "direct":
        return NetworkRoute(action, source, (None,))
    proxy = ConfigManager.get("http_proxy", "")
    if parts.scheme in {"https", "wss"}:
        proxy = ConfigManager.get("https_proxy", "") or proxy
    proxy = validate_proxy_url(str(proxy or ""))
    if not proxy:
        if action == "proxy":
            raise ValueError("未配置全局代理地址，请设置 network/proxy 的 http_proxy")
        return NetworkRoute("direct", "unconfigured", (None,))
    timeout = max(0.1, float(ConfigManager.get("proxy_connect_timeout", 3.0)))
    return NetworkRoute(action, source, (None, proxy) if action == "fallback" else (proxy,), timeout)


def get_proxy_url(url: str, proxy_mode: ProxyMode = "system") -> str | None:
    """返回首选路由的代理地址；需要失败回退的 SDK 应使用完整路由或网络客户端。"""
    return resolve_network_route(url, proxy_mode).proxies[0]


register_configs({"network/proxy": {
    "proxy_enabled": {"default": False, "description": "允许组件使用全局代理地址与站点规则，默认关闭；关闭时 system 直连、proxy 明确报错。只影响项目 SDK，不修改系统代理，下一次请求生效"},
    "http_proxy": {"default": "", "description": "全局代理地址，例如 http://127.0.0.1:7890；支持 HTTP / SOCKS5", "validator": validate_proxy_url},
    "https_proxy": {"default": "", "description": "HTTPS 专用代理（留空复用全局代理地址）", "advanced": True, "validator": validate_proxy_url},
    "proxy_default_mode": {"default": "fallback", "value_type": "enum", "options": ["fallback", "direct", "proxy"], "description": "未命中规则时：fallback 直连失败后代理 / direct 直连 / proxy 代理"},
    "proxy_rules": {"default": "", "value_type": "text", "validator": validate_rules, "description": "站点规则（从上到下首条命中）。每行：DOMAIN,api.example.com,PROXY 或 DOMAIN-SUFFIX,example.com,DIRECT 或 IP-CIDR,10.0.0.0/8,DIRECT；支持 FALLBACK。IP 规则只匹配 URL 中的 IP，不额外查询 DNS。"},
    "proxy_connect_timeout": {"default": 3.0, "min": 0.1, "max": 30.0, "unit": "s", "advanced": True, "description": "自动回退时直连建立连接的等待上限（不改变响应读取超时）"},
}})
